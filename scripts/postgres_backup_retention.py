#!/usr/bin/env python3
"""Preview or explicitly apply tiered retention to validated NAS backup pairs."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.postgres_backup_common import (
    BACKUP_PREFIX,
    NAS_BACKUP_ROOT,
    OperatorError,
    fail_safely,
    fsync_directory,
    read_json,
    sha256_file,
    utc_now,
    validate_nas_destination,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class BackupPair:
    backup_id: str
    recovery_point_at: datetime
    artifact_path: Path
    metadata_path: Path
    bytes: int


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=NAS_BACKUP_ROOT)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="delete eligible NAS pairs; default behavior is a dry-run",
    )
    return parser.parse_args()


def _parse_recovery_point(value: object, metadata_name: str) -> datetime:
    if not isinstance(value, str):
        raise OperatorError(f"invalid recovery_point_at in {metadata_name}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise OperatorError(f"invalid recovery_point_at in {metadata_name}") from error
    if parsed.tzinfo is None:
        raise OperatorError(f"recovery_point_at lacks a timezone in {metadata_name}")
    return parsed.astimezone(UTC)


def _validated_pair(metadata_path: Path) -> BackupPair | None:
    metadata = read_json(metadata_path)
    backup_id = metadata.get("backup_id")
    artifact = metadata.get("artifact")
    storage = metadata.get("storage")
    validation = metadata.get("validation")
    if (
        not isinstance(backup_id, str)
        or not _SAFE_ID.fullmatch(backup_id)
        or not isinstance(artifact, dict)
        or not isinstance(storage, dict)
        or not isinstance(validation, dict)
        or storage.get("status") != "nas_cifs_confirmed"
        or validation.get("plain_archive_list") is not True
        or validation.get("encryption_adapter_completed") is not True
        or validation.get("encrypted_artifact_fsynced") is not True
    ):
        return None
    artifact_name = artifact.get("file")
    expected_bytes = artifact.get("bytes")
    expected_sha256 = artifact.get("sha256")
    expected_base = f"{BACKUP_PREFIX}{backup_id}"
    if (
        metadata_path.name != f"{expected_base}.json"
        or not isinstance(artifact_name, str)
        or Path(artifact_name).name != artifact_name
        or artifact_name != f"{expected_base}.dump.age"
        or not isinstance(expected_bytes, int)
        or expected_bytes < 1
        or not isinstance(expected_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
    ):
        return None
    artifact_path = metadata_path.parent / artifact_name
    if (
        not artifact_path.is_file()
        or artifact_path.stat().st_size != expected_bytes
        or sha256_file(artifact_path) != expected_sha256
    ):
        return None
    recovery_point = metadata.get("recovery_point_at", metadata.get("started_at"))
    return BackupPair(
        backup_id=backup_id,
        recovery_point_at=_parse_recovery_point(recovery_point, metadata_path.name),
        artifact_path=artifact_path,
        metadata_path=metadata_path,
        bytes=expected_bytes,
    )


def retention_partition(
    pairs: list[BackupPair], now: datetime
) -> tuple[list[BackupPair], list[BackupPair]]:
    """Keep the last known good plus tiered recovery points through 365 days."""
    current = now.astimezone(UTC)
    keep: list[BackupPair] = []
    delete: list[BackupPair] = []
    newest_by_bucket: dict[tuple[object, ...], BackupPair] = {}

    ordered_pairs = sorted(
        pairs, key=lambda candidate: candidate.recovery_point_at, reverse=True
    )
    for index, pair in enumerate(ordered_pairs):
        # Never age out the newest validated pair, including during a long outage.
        if index == 0:
            keep.append(pair)
            continue
        age = current - pair.recovery_point_at
        if age < timedelta(0):
            keep.append(pair)
            continue
        if age <= timedelta(days=7):
            keep.append(pair)
            continue
        if age <= timedelta(days=30):
            bucket: tuple[object, ...] = ("day", pair.recovery_point_at.date())
        elif age <= timedelta(days=90):
            iso_year, iso_week, _ = pair.recovery_point_at.isocalendar()
            bucket = ("iso-week", iso_year, iso_week)
        elif age <= timedelta(days=365):
            bucket = (
                "month",
                pair.recovery_point_at.year,
                pair.recovery_point_at.month,
            )
        else:
            delete.append(pair)
            continue
        if bucket in newest_by_bucket:
            delete.append(pair)
        else:
            newest_by_bucket[bucket] = pair
            keep.append(pair)
    return keep, delete


def main() -> int:
    arguments = parse_arguments()
    output_dir = validate_nas_destination(arguments.output_dir)

    pairs: list[BackupPair] = []
    skipped = 0
    for metadata_path in sorted(output_dir.glob(f"{BACKUP_PREFIX}*.json")):
        pair = _validated_pair(metadata_path)
        if pair is None:
            skipped += 1
        else:
            pairs.append(pair)

    kept, eligible = retention_partition(pairs, utc_now())
    action = "delete" if arguments.apply else "would-delete"
    if arguments.apply:
        # Recheck kernel mount state after hashing/preview work and immediately before
        # the first destructive operation.
        output_dir = validate_nas_destination(arguments.output_dir)

    for pair in sorted(eligible, key=lambda candidate: candidate.recovery_point_at):
        print(
            f"{action}: {pair.backup_id} "
            f"{pair.artifact_path.name} {pair.metadata_path.name}"
        )
        if arguments.apply:
            pair.artifact_path.unlink()
            pair.metadata_path.unlink()
            fsync_directory(output_dir)

    kept_bytes = sum(pair.bytes for pair in kept)
    eligible_bytes = sum(pair.bytes for pair in eligible)
    mode = "apply" if arguments.apply else "dry-run"
    print(
        f"retention {mode}: keep={len(kept)} keep_bytes={kept_bytes} "
        f"eligible={len(eligible)} eligible_bytes={eligible_bytes} skipped={skipped}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OperatorError, OSError, UnicodeError) as error:
        fail_safely(error)
        raise SystemExit(1) from None
