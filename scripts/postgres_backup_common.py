"""Shared, dependency-free helpers for PostgreSQL backup operator scripts."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

BACKUP_PREFIX = "daily-firehose-postgres-"
REMOTE_DUMP_COMMAND = (
    "exec pg_dump --format=custom --compress=9 --no-owner --no-acl "
    '--username="$POSTGRES_USER" --dbname="$POSTGRES_DB"'
)
CANONICAL_SOURCE_HOST = "daily-firehose"
CANONICAL_SOURCE_PATH = Path("/home/ubuntu/daily-firehose")
NAS_MOUNT_POINT = Path("/nas/homes")
NAS_BACKUP_ROOT = Path("/nas/homes/backups/daily-firehose")


class OperatorError(RuntimeError):
    """An expected, safely reportable operator error."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_adapter(environment_name: str) -> str:
    """Resolve one executable without accepting command strings or secret arguments."""
    value = os.environ.get(environment_name, "")
    if not value:
        raise OperatorError(f"{environment_name} must name an executable adapter")
    if any(character.isspace() for character in value):
        raise OperatorError(
            f"{environment_name} must be one executable path, not a command string"
        )
    resolved = shutil.which(value)
    if resolved is None:
        raise OperatorError(f"{environment_name} executable was not found")
    return resolved


def run(
    command: Sequence[str],
    *,
    stdin: BinaryIO | int | None = None,
    stdout: BinaryIO | int | None = None,
    capture_output: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            list(command),
            stdin=stdin,
            stdout=subprocess.PIPE if capture_output and stdout is None else stdout,
            stderr=subprocess.PIPE if capture_output else None,
            check=True,
            env=env,
        )
    except FileNotFoundError as error:
        raise OperatorError(
            f"required executable was not found: {Path(command[0]).name}"
        ) from error
    except subprocess.CalledProcessError as error:
        raise OperatorError(
            f"command failed safely: {Path(command[0]).name}"
        ) from error


def compose_command(compose_file: Path, *arguments: str) -> list[str]:
    return ["docker", "compose", "-f", str(compose_file), *arguments]


def validate_source(source_host: str, source_path: Path) -> tuple[str, Path]:
    """Accept only the owner-approved production source, never arbitrary SSH input."""
    if source_host != CANONICAL_SOURCE_HOST:
        raise OperatorError(
            f"source host must be the canonical {CANONICAL_SOURCE_HOST!r} alias"
        )
    if source_path != CANONICAL_SOURCE_PATH:
        raise OperatorError(
            f"source path must be the canonical {CANONICAL_SOURCE_PATH} checkout"
        )
    return source_host, source_path


def remote_compose_command(
    source_host: str, source_path: Path, *args: str
) -> list[str]:
    """Build a fixed-shape BatchMode SSH invocation with no remote shell fragments.

    OpenSSH joins remote arguments into a command string. Every remote token below is
    therefore repository-owned and constant after strict host/path validation; caller
    values are not interpolated into the remote command.
    """
    validate_source(source_host, source_path)
    allowed = {
        ("config", "--services"),
        (
            "exec",
            "-T",
            "db",
            "sh",
            "-eu",
            "-c",
            REMOTE_DUMP_COMMAND,
        ),
        ("exec", "-T", "db", "pg_restore", "--list"),
    }
    if tuple(args) not in allowed:
        raise OperatorError("unsupported remote Compose operation")
    compose_file = source_path / "docker-compose.yml"
    remote_tokens = [
        "docker",
        "compose",
        "--project-directory",
        str(source_path),
        "-f",
        str(compose_file),
        *args,
    ]
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "--",
        source_host,
        shlex.join(remote_tokens),
    ]


def require_remote_compose_db(source_host: str, source_path: Path) -> None:
    result = run(
        remote_compose_command(source_host, source_path, "config", "--services"),
        capture_output=True,
    )
    services = result.stdout.decode("utf-8", errors="strict").splitlines()
    if "db" not in services:
        raise OperatorError("remote Compose configuration has no db service")


def _unescape_mount_field(value: str) -> str:
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def _device_number(path: Path) -> str:
    """Return the kernel major:minor device backing the effective path."""
    device = path.stat().st_dev
    return f"{os.major(device)}:{os.minor(device)}"


def _effective_mount_filesystems(
    destination: Path, mountinfo_path: Path
) -> frozenset[str]:
    """Return filesystem types for the effective device at the deepest mount.

    Loki exposes an autofs trigger and a CIFS mount at the same path. Matching only
    the path can therefore select the trigger, while accepting any matching CIFS layer
    can miss a later local overmount. The destination's effective ``st_dev`` identifies
    the active kernel device; the deepest matching mount ancestor identifies its type.
    """
    try:
        lines = mountinfo_path.read_text(encoding="utf-8").splitlines()
        effective_device = _device_number(destination)
    except OSError as error:
        raise OperatorError("cannot inspect active mounts") from error
    candidates: list[tuple[int, str]] = []
    for line in lines:
        fields = line.split()
        if "-" not in fields or len(fields) < 7 or fields[2] != effective_device:
            continue
        separator = fields.index("-")
        if separator + 1 >= len(fields):
            continue
        mounted_at = Path(_unescape_mount_field(fields[4]))
        try:
            destination.relative_to(mounted_at)
        except ValueError:
            continue
        candidates.append((len(mounted_at.parts), fields[separator + 1]))
    if not candidates:
        return frozenset()
    deepest = max(depth for depth, _filesystem in candidates)
    return frozenset(filesystem for depth, filesystem in candidates if depth == deepest)


def validate_nas_destination(
    destination: Path,
    *,
    approved_root: Path = NAS_BACKUP_ROOT,
    mount_point: Path = NAS_MOUNT_POINT,
    mountinfo_path: Path = Path("/proc/self/mountinfo"),
) -> Path:
    """Require a resolved destination below the approved active CIFS NAS mount."""
    resolved_root = approved_root.resolve(strict=True)
    resolved_destination = destination.resolve(strict=True)
    try:
        resolved_root.relative_to(mount_point.resolve(strict=True))
        resolved_destination.relative_to(resolved_root)
    except ValueError as error:
        raise OperatorError(
            "backup destination is outside the approved NAS path"
        ) from error
    filesystems = _effective_mount_filesystems(resolved_destination, mountinfo_path)
    if not filesystems or not filesystems.issubset({"cifs", "smb3"}):
        raise OperatorError(f"{mount_point} must be the effective active CIFS mount")
    return resolved_destination


def fsync_file(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise OperatorError("backup artifact is missing or empty")
    with path.open("rb") as source:
        os.fsync(source.fileno())


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.part")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(value, output, indent=2, sort_keys=True)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    fsync_directory(path.parent)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise OperatorError(f"invalid metadata file: {path.name}") from error
    if not isinstance(value, dict):
        raise OperatorError(f"invalid metadata file: {path.name}")
    return value


def fail_safely(error: Exception) -> None:
    print(f"error: {error}", file=sys.stderr)
