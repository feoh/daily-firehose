"""Shared, dependency-free helpers for PostgreSQL backup operator scripts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

BACKUP_PREFIX = "daily-firehose-postgres-"
BACKUP_ID_PATTERN = r"\d{8}T\d{6}Z-[0-9a-f]{8}"
CANONICAL_SOURCE_HOST = "daily-firehose"
CANONICAL_SOURCE_PATH = Path("/home/ubuntu/daily-firehose")
COMPOSE_FILE = CANONICAL_SOURCE_PATH / "docker-compose.yml"
MAX_DUMP_BYTES = 1024**3
DUMP_CONTAINER_COMMAND = (
    "exec pg_dump --format=custom --compress=9 --no-owner --no-acl "
    '--username="$POSTGRES_USER" --dbname="$POSTGRES_DB"'
)


class OperatorError(RuntimeError):
    """An expected, safely reportable operator error."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def run(
    command: Sequence[str],
    *,
    stdin: BinaryIO | int | None = None,
    stdout: BinaryIO | int | None = None,
    capture_output: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run an argv-only command and expose only a bounded, non-secret error."""
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
            f"command failed safely: {Path(command[0]).name} "
            f"exited {error.returncode}"
        ) from error


def compose_command(compose_file: Path, *arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-directory",
        str(compose_file.parent),
        "-f",
        str(compose_file),
        *arguments,
    ]


def production_compose_command(*arguments: str) -> list[str]:
    """Return one of the exact local production Compose operations."""
    allowed = {
        ("config", "--services"),
        ("exec", "-T", "db", "sh", "-eu", "-c", DUMP_CONTAINER_COMMAND),
        ("exec", "-T", "db", "pg_restore", "--list"),
    }
    if tuple(arguments) not in allowed:
        raise OperatorError("unsupported production Compose operation")
    return compose_command(COMPOSE_FILE, *arguments)


def require_production_compose_db() -> None:
    result = run(
        production_compose_command("config", "--services"), capture_output=True
    )
    services = result.stdout.decode("utf-8", errors="strict").splitlines()
    if "db" not in services:
        raise OperatorError("canonical Compose configuration has no db service")


def sha256_stream(source: BinaryIO) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    with path.open("rb") as source:
        return sha256_stream(source)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    """Publish JSON durably without overwriting, including concurrent writers."""
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.part")
    linked = False
    try:
        with temporary.open("x", encoding="utf-8") as output:
            os.chmod(temporary, 0o600)
            json.dump(value, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as error:
            raise OperatorError("refusing to overwrite existing evidence") from error
        linked = True
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
        if linked:
            fsync_directory(path.parent)


def read_json_bytes(value: bytes, description: str = "metadata") -> dict[str, Any]:
    try:
        decoded = json.loads(value.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise OperatorError(f"invalid {description}") from error
    if not isinstance(decoded, dict):
        raise OperatorError(f"invalid {description}")
    return decoded


def validate_backup_metadata(
    metadata: dict[str, Any], backup_id: str
) -> tuple[int, str, datetime]:
    """Validate every field needed to identify, restore, and retain a backup."""
    if (
        not re.fullmatch(BACKUP_ID_PATTERN, backup_id)
        or metadata.get("backup_id") != backup_id
    ):
        raise OperatorError("metadata backup ID is invalid")
    artifact = metadata.get("artifact")
    database = metadata.get("database")
    source = metadata.get("source")
    storage = metadata.get("storage")
    validation = metadata.get("validation")
    if not all(
        isinstance(value, dict)
        for value in (artifact, database, source, storage, validation)
    ):
        raise OperatorError("metadata structure is incomplete")
    assert isinstance(artifact, dict)
    assert isinstance(database, dict)
    assert isinstance(source, dict)
    assert isinstance(storage, dict)
    assert isinstance(validation, dict)
    expected_name = f"{BACKUP_PREFIX}{backup_id}.dump"
    size = artifact.get("bytes")
    checksum = artifact.get("sha256")
    if (
        metadata.get("schema_version") != 3
        or artifact.get("file") != expected_name
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size < 1
        or size > MAX_DUMP_BYTES
        or not isinstance(checksum, str)
        or not re.fullmatch(r"[0-9a-f]{64}", checksum)
        or database
        != {
            "archive_format": "pg_dump-custom",
            "compression": 9,
            "ownership_included": False,
            "privileges_included": False,
        }
        or source
        != {
            "compose_path": str(CANONICAL_SOURCE_PATH),
            "host": CANONICAL_SOURCE_HOST,
        }
        or storage
        != {
            "offsite": "not_verified_by_this_script",
            "transport": "ssh_push",
        }
        or validation.get("local_pg_restore_list") is not True
        or validation.get("source_archive_fsynced") is not True
        or not isinstance(validation.get("dump_manifest_entries"), int)
        or isinstance(validation.get("dump_manifest_entries"), bool)
        or validation.get("dump_manifest_entries", 0) < 1
    ):
        raise OperatorError("metadata does not describe a complete verified backup")
    timestamps: dict[str, datetime] = {}
    for field in ("started_at", "completed_at", "recovery_point_at"):
        raw = metadata.get(field)
        if not isinstance(raw, str) or not raw.endswith("Z"):
            raise OperatorError(f"metadata {field} is invalid")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as error:
            raise OperatorError(f"metadata {field} is invalid") from error
        if parsed.tzinfo is None:
            raise OperatorError(f"metadata {field} is invalid")
        timestamps[field] = parsed.astimezone(UTC)
    if timestamps["completed_at"] < timestamps["started_at"]:
        raise OperatorError("metadata completion precedes start")
    if timestamps["recovery_point_at"] != timestamps["started_at"]:
        raise OperatorError("metadata recovery point is inconsistent")
    try:
        id_timestamp = datetime.strptime(backup_id[:16], "%Y%m%dT%H%M%SZ").replace(
            tzinfo=UTC
        )
    except ValueError as error:
        raise OperatorError("backup ID timestamp is invalid") from error
    if timestamps["recovery_point_at"] != id_timestamp:
        raise OperatorError("metadata recovery point does not match backup ID")
    return size, checksum, timestamps["recovery_point_at"]


def fail_safely(error: Exception) -> None:
    print(f"error: {error}", file=sys.stderr)
