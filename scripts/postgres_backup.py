#!/usr/bin/env python3
"""Pull, validate, encrypt, and confirm a PostgreSQL backup on the loki NAS mount."""

from __future__ import annotations

import argparse
import os
import secrets
import tempfile
from pathlib import Path
from typing import BinaryIO

from scripts.postgres_backup_common import (
    BACKUP_PREFIX,
    CANONICAL_SOURCE_HOST,
    CANONICAL_SOURCE_PATH,
    NAS_BACKUP_ROOT,
    REMOTE_DUMP_COMMAND,
    OperatorError,
    fail_safely,
    format_utc,
    fsync_directory,
    fsync_file,
    read_json,
    remote_compose_command,
    require_remote_compose_db,
    resolve_adapter,
    run,
    sha256_file,
    utc_now,
    validate_nas_destination,
    validate_source,
    write_json_atomic,
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-host", default=CANONICAL_SOURCE_HOST)
    parser.add_argument("--source-path", type=Path, default=CANONICAL_SOURCE_PATH)
    parser.add_argument("--output-dir", type=Path, default=NAS_BACKUP_ROOT)
    parser.add_argument(
        "--temporary-dir",
        type=Path,
        help="private loki plaintext spool; defaults to the OS temporary directory",
    )
    return parser.parse_args()


def _validate_custom_archive(
    source_host: str, source_path: Path, source: BinaryIO
) -> int:
    result = run(
        remote_compose_command(
            source_host,
            source_path,
            "exec",
            "-T",
            "db",
            "pg_restore",
            "--list",
        ),
        stdin=source,
        capture_output=True,
    )
    return sum(
        1
        for line in result.stdout.decode("utf-8", errors="strict").splitlines()
        if line and not line.startswith(";")
    )


def main() -> int:
    arguments = parse_arguments()
    source_host, source_path = validate_source(
        arguments.source_host, arguments.source_path
    )
    output_dir = validate_nas_destination(arguments.output_dir)
    temporary_dir = (
        arguments.temporary_dir.resolve(strict=True)
        if arguments.temporary_dir is not None
        else None
    )
    if temporary_dir is not None and not temporary_dir.is_dir():
        raise OperatorError("--temporary-dir must be an existing private directory")

    encryptor = resolve_adapter("BACKUP_ENCRYPTOR")
    require_remote_compose_db(source_host, source_path)

    started_at = utc_now()
    stamp = started_at.strftime("%Y%m%dT%H%M%SZ")
    backup_id = f"{stamp}-{secrets.token_hex(4)}"
    base_name = f"{BACKUP_PREFIX}{backup_id}"
    artifact_path = output_dir / f"{base_name}.dump.age"
    partial_artifact_path = output_dir / f".{base_name}.dump.age.part"
    metadata_path = output_dir / f"{base_name}.json"

    if artifact_path.exists() or metadata_path.exists():
        raise OperatorError("refusing to overwrite an existing backup artifact")

    try:
        # TemporaryFile is anonymous (or immediately unlinked) on supported Unix
        # platforms, so even SIGKILL cannot strand a named plaintext dump.
        with tempfile.TemporaryFile(mode="w+b", dir=temporary_dir) as plain_archive:
            os.fchmod(plain_archive.fileno(), 0o600)
            run(
                remote_compose_command(
                    source_host,
                    source_path,
                    "exec",
                    "-T",
                    "db",
                    "sh",
                    "-eu",
                    "-c",
                    REMOTE_DUMP_COMMAND,
                ),
                stdout=plain_archive,
            )
            plain_archive.flush()
            os.fsync(plain_archive.fileno())
            if os.fstat(plain_archive.fileno()).st_size == 0:
                raise OperatorError("pg_dump produced an empty archive")

            plain_archive.seek(0)
            manifest_entries = _validate_custom_archive(
                source_host, source_path, plain_archive
            )
            if manifest_entries == 0:
                raise OperatorError("pg_restore listed no archive entries")

            plain_archive.seek(0)
            run([encryptor, str(partial_artifact_path)], stdin=plain_archive)
        fsync_file(partial_artifact_path)
        partial_artifact_path.replace(artifact_path)
        fsync_file(artifact_path)
        fsync_directory(output_dir)
        validate_nas_destination(output_dir)

        completed_at = utc_now()
        metadata = {
            "artifact": {
                "bytes": artifact_path.stat().st_size,
                "file": artifact_path.name,
                "sha256": sha256_file(artifact_path),
            },
            "backup_id": backup_id,
            "completed_at": format_utc(completed_at),
            "recovery_point_at": format_utc(started_at),
            "database": {
                "archive_format": "pg_dump-custom",
                "compression": 9,
                "ownership_included": False,
                "privileges_included": False,
            },
            "schema_version": 2,
            "source": {
                "compose_path": str(source_path),
                "host": source_host,
            },
            "started_at": format_utc(started_at),
            "storage": {
                "offsite_backup": "not_verified_by_this_script",
                "root": str(NAS_BACKUP_ROOT),
                "status": "nas_cifs_confirmed",
            },
            "validation": {
                "dump_manifest_entries": manifest_entries,
                "encryption_adapter_completed": True,
                "encrypted_artifact_fsynced": True,
                "plain_archive_list": True,
            },
        }
        write_json_atomic(metadata_path, metadata)
        fsync_file(metadata_path)
        validate_nas_destination(output_dir)
        confirmed_metadata = read_json(metadata_path)
        if confirmed_metadata != metadata:
            raise OperatorError("written metadata confirmation failed")

        print(f"backup complete on loki NAS: {backup_id}")
        print(f"encrypted artifact: {artifact_path.name}")
        print(f"metadata: {metadata_path.name}")
        return 0
    finally:
        partial_artifact_path.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OperatorError, OSError, UnicodeError) as error:
        fail_safely(error)
        raise SystemExit(1) from None
