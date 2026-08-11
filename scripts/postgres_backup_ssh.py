"""Strict SSH transport for the dedicated TrueNAS backup account."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from scripts.postgres_backup_common import (
    BACKUP_ID_PATTERN,
    MAX_DUMP_BYTES,
    OperatorError,
)

NAS_HOST = "192.168.1.2"
NAS_USER = "daily-firehose-backup"
REMOTE_TARGET = f"{NAS_USER}@{NAS_HOST}"
DEFAULT_IDENTITY_FILE = Path("/etc/daily-firehose-backup/ssh/id_ed25519")
DEFAULT_KNOWN_HOSTS_FILE = Path("/etc/daily-firehose-backup/ssh/known_hosts")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_BACKUP_ID = re.compile(BACKUP_ID_PATTERN)
_MAX_METADATA_BYTES = 64 * 1024


def _credential_path(environment_name: str, default: Path) -> Path:
    raw = os.environ.get(environment_name)
    path = Path(raw) if raw else default
    if not path.is_file():
        raise OperatorError(
            f"{environment_name} must identify a regular credential file"
        )
    return path


def _remote_command(operation: str, arguments: tuple[str, ...]) -> str:
    if operation == "health" and not arguments:
        return operation
    if operation == "put" and len(arguments) == 4:
        backup_id, kind, size_text, checksum = arguments
        if (
            not _BACKUP_ID.fullmatch(backup_id)
            or kind not in {"dump", "metadata"}
            or not size_text.isascii()
            or not size_text.isdigit()
            or not _SHA256.fullmatch(checksum)
        ):
            raise OperatorError("unsupported SSH backup command")
        size = int(size_text)
        maximum = _MAX_METADATA_BYTES if kind == "metadata" else MAX_DUMP_BYTES
        if size < 1 or size > maximum:
            raise OperatorError("unsupported SSH backup size")
        return " ".join((operation, *arguments))
    if operation == "read" and len(arguments) == 2:
        backup_id, kind = arguments
        if not _BACKUP_ID.fullmatch(backup_id) or kind not in {"dump", "metadata"}:
            raise OperatorError("unsupported SSH backup command")
        return " ".join((operation, *arguments))
    raise OperatorError("unsupported SSH backup command")


def ssh_command(operation: str, *arguments: str) -> list[str]:
    """Build one fixed-policy, noninteractive SSH invocation.

    The remote account's authorized key forces the receiver. This client also permits
    only protocol commands understood by that receiver; no caller supplies a shell.
    """
    ssh = shutil.which("ssh")
    if ssh is None:
        raise OperatorError("required executable was not found: ssh")
    identity = _credential_path("BACKUP_SSH_IDENTITY_FILE", DEFAULT_IDENTITY_FILE)
    known_hosts = _credential_path(
        "BACKUP_SSH_KNOWN_HOSTS_FILE", DEFAULT_KNOWN_HOSTS_FILE
    )
    remote = _remote_command(operation, tuple(arguments))
    return [
        ssh,
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-i",
        str(identity),
        "--",
        REMOTE_TARGET,
        remote,
    ]
