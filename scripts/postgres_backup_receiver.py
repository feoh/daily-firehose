#!/usr/bin/env python3
"""Forced SSH receiver and local-only maintenance for TrueNAS SCALE."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO

from scripts.postgres_backup_common import (
    BACKUP_ID_PATTERN,
    BACKUP_PREFIX,
    MAX_DUMP_BYTES,
    OperatorError,
    read_json_bytes,
    validate_backup_metadata,
)

DATA_DIRECTORY_PATH = Path("/mnt/nas_general/homes/backups/daily-firehose")
CONTROL_DIRECTORY_PATH = Path("/mnt/nas_general/homes/backups/daily-firehose-control")
# Test-only override. Production always uses DATA_DIRECTORY_PATH.
DATA_DIRECTORY: Path | None = None
DATASET_QUOTA_BYTES = 20 * 1024**3
MOUNTINFO_PATH = Path("/proc/self/mountinfo")
ZFS_DATASET_SOURCE = "nas_general/homes/backups/daily-firehose"
ORPHAN_SAFE_AGE = timedelta(hours=48)
_MAX_METADATA_BYTES = 64 * 1024
_BACKUP_ID = re.compile(BACKUP_ID_PATTERN)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_METADATA_NAME = re.compile(rf"{BACKUP_PREFIX}({BACKUP_ID_PATTERN})\.json")
_DUMP_NAME = re.compile(rf"{BACKUP_PREFIX}({BACKUP_ID_PATTERN})\.dump")
_RECEIPT_NAME = re.compile(rf"{BACKUP_PREFIX}({BACKUP_ID_PATTERN})\.receipt\.json")
_PART_NAME = re.compile(
    rf"\.{BACKUP_PREFIX}{BACKUP_ID_PATTERN}\."
    rf"(?:dump|json|receipt\.json)\.\d+\.[0-9a-f]{{8}}\.part"
)
_LOCK_NAME = ".receiver.lock"
_RECEIPT_SEQUENCE_NAME = ".receipt-sequence"
_SSH_ENVIRONMENT = ("SSH_ORIGINAL_COMMAND", "SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")


@dataclass(frozen=True)
class StoredPair:
    backup_id: str
    retention_at: datetime
    dump_name: str
    metadata_name: str
    receipt_name: str
    receipt_sequence: int


def _names(backup_id: str) -> tuple[str, str, str]:
    if not _BACKUP_ID.fullmatch(backup_id):
        raise OperatorError("invalid backup ID")
    base = f"{BACKUP_PREFIX}{backup_id}"
    return f"{base}.dump", f"{base}.json", f"{base}.receipt.json"


def _configured_data_directory() -> Path:
    return DATA_DIRECTORY if DATA_DIRECTORY is not None else DATA_DIRECTORY_PATH


def _unescape_mount_field(value: str) -> str:
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def _device_number(path: Path) -> str:
    device = path.stat().st_dev
    return f"{os.major(device)}:{os.minor(device)}"


def _require_active_zfs_dataset(
    data_directory: Path, expected_source: str = ZFS_DATASET_SOURCE
) -> None:
    """Reject an ordinary directory, parent mount, or masking overmount."""
    try:
        lines = MOUNTINFO_PATH.read_text(encoding="utf-8").splitlines()
        effective_device = _device_number(data_directory)
    except OSError as error:
        raise OperatorError("cannot inspect the active backup dataset mount") from error
    matches: list[tuple[str, str, str]] = []
    for line in lines:
        fields = line.split()
        if "-" not in fields or len(fields) < 10 or fields[2] != effective_device:
            continue
        separator = fields.index("-")
        if separator + 2 >= len(fields):
            continue
        mount_root = _unescape_mount_field(fields[3])
        mounted_at = Path(_unescape_mount_field(fields[4]))
        if mounted_at != data_directory:
            continue
        matches.append((mount_root, fields[separator + 1], fields[separator + 2]))
    if matches != [("/", "zfs", expected_source)]:
        raise OperatorError("backup path must be the exact effective ZFS dataset mount")


def _open_data_directory() -> int:
    data_directory = _configured_data_directory()
    try:
        resolved_directory = data_directory.resolve(strict=True)
        info = data_directory.lstat()
    except OSError as error:
        raise OperatorError("backup dataset directory is unavailable") from error
    if (
        resolved_directory != data_directory
        or stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
    ):
        raise OperatorError("backup dataset path must contain no symlink or alias")
    _require_active_zfs_dataset(data_directory)
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise OperatorError("backup dataset must have mode 0700")
    if os.geteuid() not in {0, info.st_uid}:
        raise OperatorError("receiver must run as root or the backup dataset owner")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(data_directory, flags)
    except OSError as error:
        raise OperatorError(
            "backup dataset directory cannot be opened safely"
        ) from error


@contextmanager
def _receiver_lock(directory_fd: int) -> Iterator[None]:
    """Serialize every put, read, and maintenance operation receiver-wide."""
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(_LOCK_NAME, flags, 0o600, dir_fd=directory_fd)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise OperatorError("receiver lock is not a regular file")
        directory_owner = os.fstat(directory_fd).st_uid
        if os.geteuid() == 0 and info.st_uid != directory_owner:
            os.fchown(descriptor, directory_owner, -1)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _read_regular(directory_fd: int, name: str, maximum: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=directory_fd)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size < 1 or info.st_size > maximum:
            raise OperatorError("stored artifact is not a bounded regular file")
        chunks: list[bytes] = []
        remaining = info.st_size
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            if not block:
                raise OperatorError("stored artifact ended early")
            chunks.append(block)
            remaining -= len(block)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _stream_regular(
    directory_fd: int, name: str, maximum: int, output: BinaryIO
) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=directory_fd)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size < 1 or info.st_size > maximum:
            raise OperatorError("stored artifact is not a bounded regular file")
        remaining = info.st_size
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            if not block:
                raise OperatorError("stored artifact ended early")
            written = output.write(block)
            if written is not None and written != len(block):
                raise OperatorError("backup read output was incomplete")
            remaining -= len(block)
    finally:
        os.close(descriptor)


def _hash_regular(directory_fd: int, name: str, expected_size: int) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=directory_fd)
    digest = hashlib.sha256()
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size != expected_size:
            raise OperatorError("stored artifact size is invalid")
        remaining = expected_size
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            if not block:
                raise OperatorError("stored artifact ended early")
            digest.update(block)
            remaining -= len(block)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _parse_receipt(value: bytes, backup_id: str) -> tuple[dict[str, object], datetime]:
    receipt = read_json_bytes(value, "receipt")
    received_at = receipt.get("received_at")
    receipt_sequence = receipt.get("receipt_sequence")
    if (
        receipt.get("schema_version") != 1
        or receipt.get("backup_id") != backup_id
        or not isinstance(received_at, str)
        or not received_at.endswith("Z")
        or not isinstance(receipt_sequence, int)
        or isinstance(receipt_sequence, bool)
        or receipt_sequence < 1
    ):
        raise OperatorError("receipt is incomplete")
    try:
        parsed = datetime.fromisoformat(received_at)
    except ValueError as error:
        raise OperatorError("receipt timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise OperatorError("receipt timestamp is invalid")
    return receipt, parsed.astimezone(UTC)


def _validated_pair(directory_fd: int, backup_id: str) -> StoredPair:
    dump_name, metadata_name, receipt_name = _names(backup_id)
    receipt_bytes = _read_regular(directory_fd, receipt_name, _MAX_METADATA_BYTES)
    receipt, received_at = _parse_receipt(receipt_bytes, backup_id)
    metadata_bytes = _read_regular(directory_fd, metadata_name, _MAX_METADATA_BYTES)
    metadata = read_json_bytes(metadata_bytes)
    dump_size, dump_checksum, _ = validate_backup_metadata(metadata, backup_id)
    expected_receipt = {
        "backup_id": backup_id,
        "dump": {
            "bytes": dump_size,
            "file": dump_name,
            "sha256": dump_checksum,
        },
        "metadata": {
            "bytes": len(metadata_bytes),
            "file": metadata_name,
            "sha256": hashlib.sha256(metadata_bytes).hexdigest(),
        },
        "received_at": receipt.get("received_at"),
        "receipt_sequence": receipt.get("receipt_sequence"),
        "schema_version": 1,
    }
    if receipt != expected_receipt:
        raise OperatorError("receipt does not match stored pair")
    if _hash_regular(directory_fd, dump_name, dump_size) != dump_checksum:
        raise OperatorError("stored dump checksum is invalid")
    receipt_sequence = receipt["receipt_sequence"]
    assert isinstance(receipt_sequence, int) and not isinstance(receipt_sequence, bool)
    return StoredPair(
        backup_id,
        received_at,
        dump_name,
        metadata_name,
        receipt_name,
        receipt_sequence,
    )


def retention_partition(
    pairs: list[StoredPair], now: datetime
) -> tuple[list[StoredPair], list[StoredPair]]:
    """Keep newest LKG and immutable first-received points in tier buckets."""
    if not pairs:
        return [], []
    current = now.astimezone(UTC)
    newest = max(pairs, key=lambda pair: pair.receipt_sequence)
    keep: list[StoredPair] = [newest]
    delete: list[StoredPair] = []
    bucket_candidates: dict[tuple[object, ...], list[StoredPair]] = {}
    for pair in pairs:
        if pair is newest:
            continue
        age = current - pair.retention_at
        if age < timedelta(0) or age <= timedelta(days=7):
            keep.append(pair)
            continue
        if age <= timedelta(days=30):
            bucket: tuple[object, ...] = ("day", pair.retention_at.date())
        elif age <= timedelta(days=90):
            year, week, _ = pair.retention_at.isocalendar()
            bucket = ("week", year, week)
        elif age <= timedelta(days=365):
            bucket = ("month", pair.retention_at.year, pair.retention_at.month)
        else:
            delete.append(pair)
            continue
        bucket_candidates.setdefault(bucket, []).append(pair)
    for candidates in bucket_candidates.values():
        earliest = min(candidates, key=lambda pair: pair.receipt_sequence)
        keep.append(earliest)
        delete.extend(pair for pair in candidates if pair is not earliest)
    return keep, delete


def _write_all(descriptor: int, value: bytes) -> None:
    offset = 0
    while offset < len(value):
        written = os.write(descriptor, value[offset:])
        if written < 1:
            raise OperatorError("upload write did not make progress")
        offset += written


def _store_bytes_no_replace(directory_fd: int, final_name: str, value: bytes) -> None:
    try:
        os.stat(final_name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        raise OperatorError("refusing to overwrite a stored artifact")
    temporary_name = f".{final_name}.{os.getpid()}.{secrets.token_hex(4)}.part"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary_name, flags, 0o600, dir_fd=directory_fd)
    linked = False
    committed = False
    try:
        _write_all(descriptor, value)
        os.fsync(descriptor)
        os.link(
            temporary_name,
            final_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        linked = True
        os.fsync(directory_fd)
        committed = True
    except FileExistsError as error:
        raise OperatorError("refusing to overwrite a stored artifact") from error
    finally:
        os.close(descriptor)
        if linked and not committed:
            try:
                os.unlink(final_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.fsync(directory_fd)


def _next_receipt_sequence(directory_fd: int) -> int:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(_RECEIPT_SEQUENCE_NAME, flags, 0o600, dir_fd=directory_fd)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 32:
            raise OperatorError("receipt sequence state is invalid")
        raw = os.read(descriptor, 33)
        if raw:
            try:
                current = int(raw.decode("ascii", errors="strict"))
            except (UnicodeError, ValueError) as error:
                raise OperatorError("receipt sequence state is invalid") from error
            if current < 0:
                raise OperatorError("receipt sequence state is invalid")
        else:
            current = 0
        for name in os.listdir(directory_fd):
            match = _RECEIPT_NAME.fullmatch(name)
            if match is None:
                continue
            try:
                receipt, _received_at = _parse_receipt(
                    _read_regular(directory_fd, name, _MAX_METADATA_BYTES),
                    match.group(1),
                )
            except (OperatorError, OSError):
                continue
            sequence = receipt.get("receipt_sequence")
            if isinstance(sequence, int) and not isinstance(sequence, bool):
                current = max(current, sequence)
        following = current + 1
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.ftruncate(descriptor, 0)
        _write_all(descriptor, f"{following}\n".encode("ascii"))
        os.fsync(descriptor)
        os.fsync(directory_fd)
        return following
    finally:
        os.close(descriptor)


def _publish_receipt(
    directory_fd: int,
    backup_id: str,
    metadata_bytes: bytes,
    metadata: dict[str, object],
) -> None:
    dump_name, metadata_name, receipt_name = _names(backup_id)
    artifact = metadata["artifact"]
    assert isinstance(artifact, dict)
    receipt = {
        "backup_id": backup_id,
        "dump": {
            "bytes": artifact["bytes"],
            "file": dump_name,
            "sha256": artifact["sha256"],
        },
        "metadata": {
            "bytes": len(metadata_bytes),
            "file": metadata_name,
            "sha256": hashlib.sha256(metadata_bytes).hexdigest(),
        },
        "received_at": datetime.now(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
        "receipt_sequence": _next_receipt_sequence(directory_fd),
        "schema_version": 1,
    }
    receipt_bytes = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()
    _store_bytes_no_replace(directory_fd, receipt_name, receipt_bytes)


def _put(
    directory_fd: int,
    backup_id: str,
    kind: str,
    expected_size: int,
    expected_checksum: str,
    source: BinaryIO,
) -> None:
    dump_name, metadata_name, _receipt_name = _names(backup_id)
    final_name = dump_name if kind == "dump" else metadata_name
    maximum = MAX_DUMP_BYTES if kind == "dump" else _MAX_METADATA_BYTES
    if (
        expected_size < 1
        or expected_size > maximum
        or not _SHA256.fullmatch(expected_checksum)
    ):
        raise OperatorError("invalid put arguments")
    content = bytearray()
    digest = hashlib.sha256()
    remaining = expected_size
    temporary_name = f".{final_name}.{os.getpid()}.{secrets.token_hex(4)}.part"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        os.stat(final_name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        raise OperatorError("refusing to overwrite a stored artifact")
    descriptor = os.open(temporary_name, flags, 0o600, dir_fd=directory_fd)
    linked = False
    committed = False
    try:
        while remaining:
            block = source.read(min(1024 * 1024, remaining))
            if not block:
                raise OperatorError("upload ended before expected size")
            _write_all(descriptor, block)
            digest.update(block)
            if kind == "metadata":
                content.extend(block)
            remaining -= len(block)
        if source.read(1):
            raise OperatorError("upload exceeded expected size")
        if digest.hexdigest() != expected_checksum:
            raise OperatorError("upload checksum does not match")
        os.fsync(descriptor)
        metadata: dict[str, object] | None = None
        if kind == "metadata":
            metadata = read_json_bytes(bytes(content))
            dump_size, dump_checksum, _ = validate_backup_metadata(metadata, backup_id)
            if _hash_regular(directory_fd, dump_name, dump_size) != dump_checksum:
                raise OperatorError("metadata does not match stored dump")
        os.link(
            temporary_name,
            final_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        linked = True
        os.fsync(directory_fd)
        committed = True
        if metadata is not None:
            _publish_receipt(directory_fd, backup_id, bytes(content), metadata)
    except FileExistsError as error:
        raise OperatorError("refusing to overwrite a stored artifact") from error
    finally:
        os.close(descriptor)
        if linked and not committed:
            try:
                os.unlink(final_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.fsync(directory_fd)


def _regular_mtime(directory_fd: int, name: str) -> datetime | None:
    try:
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    return datetime.fromtimestamp(info.st_mtime, UTC)


def _unlink_if_stale(
    directory_fd: int, name: str, now: datetime, *, safe_age: timedelta
) -> bool:
    modified = _regular_mtime(directory_fd, name)
    if modified is None or now.astimezone(UTC) - modified < safe_age:
        return False
    os.unlink(name, dir_fd=directory_fd)
    return True


def _maintenance_retention(directory_fd: int, *, now: datetime | None = None) -> None:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    names = set(os.listdir(directory_fd))
    pairs: list[StoredPair] = []
    skipped = 0
    valid_ids: set[str] = set()
    for name in names:
        match = _RECEIPT_NAME.fullmatch(name)
        if match is None:
            continue
        try:
            pair = _validated_pair(directory_fd, match.group(1))
        except (OperatorError, OSError):
            skipped += 1
        else:
            pairs.append(pair)
            valid_ids.add(pair.backup_id)
    kept, expired = retention_partition(pairs, current)
    for pair in expired:
        os.unlink(pair.receipt_name, dir_fd=directory_fd)
        os.unlink(pair.metadata_name, dir_fd=directory_fd)
        os.unlink(pair.dump_name, dir_fd=directory_fd)
        os.fsync(directory_fd)

    orphan_deleted = 0
    for name in names:
        if _PART_NAME.fullmatch(name) and _unlink_if_stale(
            directory_fd, name, current, safe_age=ORPHAN_SAFE_AGE
        ):
            orphan_deleted += 1
    for name in names:
        match = _DUMP_NAME.fullmatch(name)
        if match is None or match.group(1) in valid_ids:
            continue
        backup_id = match.group(1)
        _dump_name, metadata_name, receipt_name = _names(backup_id)
        if receipt_name in names:
            continue  # Invalid received pairs require administrator review, not deletion.
        dump_mtime = _regular_mtime(directory_fd, name)
        metadata_mtime = _regular_mtime(directory_fd, metadata_name)
        timestamps = [
            value for value in (dump_mtime, metadata_mtime) if value is not None
        ]
        if not timestamps:
            continue
        newest = max(timestamps)
        if current - newest < ORPHAN_SAFE_AGE:
            continue
        if metadata_name in names and _unlink_if_stale(
            directory_fd, metadata_name, current, safe_age=ORPHAN_SAFE_AGE
        ):
            orphan_deleted += 1
        if _unlink_if_stale(directory_fd, name, current, safe_age=ORPHAN_SAFE_AGE):
            orphan_deleted += 1
        os.fsync(directory_fd)
    print(
        "maintenance fixed-policy: "
        f"keep={len(kept)} deleted={len(expired)} skipped={skipped} "
        f"orphans_deleted={orphan_deleted}"
    )


def _parse_original_command(value: str) -> tuple[str, list[str]]:
    if not value or value != value.strip() or "\t" in value or "  " in value:
        raise OperatorError("unsupported forced command")
    fields = value.split(" ")
    operation, arguments = fields[0], fields[1:]
    if operation == "health" and not arguments:
        return operation, arguments
    if operation == "read" and len(arguments) == 2:
        _names(arguments[0])
        if arguments[1] in {"dump", "metadata"}:
            return operation, arguments
    if operation == "put" and len(arguments) == 4:
        _names(arguments[0])
        size_text = arguments[2]
        maximum = MAX_DUMP_BYTES if arguments[1] == "dump" else _MAX_METADATA_BYTES
        if (
            arguments[1] in {"dump", "metadata"}
            and size_text.isascii()
            and size_text.isdigit()
            and len(size_text) <= 10
            and str(int(size_text)) == size_text
            and 1 <= int(size_text) <= maximum
            and _SHA256.fullmatch(arguments[3])
        ):
            return operation, arguments
    raise OperatorError("unsupported forced command")


def _ssh_environment_present() -> bool:
    return any(name in os.environ for name in _SSH_ENVIRONMENT)


def main() -> int:
    arguments = sys.argv[1:]
    maintenance = arguments == ["--maintenance-retention"]
    if maintenance:
        if _ssh_environment_present():
            raise OperatorError("maintenance is forbidden in an SSH session")
    elif arguments:
        raise OperatorError("receiver accepts only local --maintenance-retention")

    directory_fd = _open_data_directory()
    try:
        with _receiver_lock(directory_fd):
            if maintenance:
                _maintenance_retention(directory_fd)
                return 0
            operation, command_arguments = _parse_original_command(
                os.environ.get("SSH_ORIGINAL_COMMAND", "")
            )
            if operation == "health":
                print("daily-firehose-backup receiver ok")
            elif operation == "put":
                backup_id, kind, size, checksum = command_arguments
                _put(
                    directory_fd, backup_id, kind, int(size), checksum, sys.stdin.buffer
                )
                print(f"stored {kind} {backup_id}")
            elif operation == "read":
                backup_id, kind = command_arguments
                pair = _validated_pair(directory_fd, backup_id)
                name = pair.dump_name if kind == "dump" else pair.metadata_name
                maximum = MAX_DUMP_BYTES if kind == "dump" else _MAX_METADATA_BYTES
                _stream_regular(directory_fd, name, maximum, sys.stdout.buffer)
            else:  # pragma: no cover
                raise OperatorError("unsupported forced command")
    finally:
        os.close(directory_fd)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OperatorError, OSError, UnicodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from None
