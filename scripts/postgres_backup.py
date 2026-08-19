#!/usr/bin/env python3
"""Create, locally validate, and push a PostgreSQL dump to restricted TrueNAS SSH."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import tempfile
from typing import BinaryIO

from scripts.postgres_backup_common import (
    BACKUP_PREFIX,
    CANONICAL_SOURCE_HOST,
    CANONICAL_SOURCE_PATH,
    DUMP_CONTAINER_COMMAND,
    MAX_DUMP_BYTES,
    OperatorError,
    fail_safely,
    format_utc,
    production_compose_command,
    require_production_compose_db,
    run,
    sha256_stream,
    utc_now,
)
from scripts.postgres_backup_ssh import ssh_command


def _archive_manifest_entries(source: BinaryIO) -> int:
    result = run(
        production_compose_command("exec", "-T", "db", "pg_restore", "--list"),
        stdin=source,
        capture_output=True,
    )
    return sum(
        1
        for line in result.stdout.decode("utf-8", errors="strict").splitlines()
        if line and not line.startswith(";")
    )


def _push(kind: str, backup_id: str, source: BinaryIO) -> None:
    size = os.fstat(source.fileno()).st_size
    source.seek(0)
    checksum = sha256_stream(source)
    source.seek(0)
    run(ssh_command("put", backup_id, kind, str(size), checksum), stdin=source)


def main() -> int:
    require_production_compose_db()
    # Discard the receiver's banner but leave ssh's stderr on the inherited
    # descriptor, so a transport failure is diagnosable from the journal.
    run(ssh_command("health"), stdout=subprocess.DEVNULL)

    started_at = utc_now()
    backup_id = f"{started_at.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}"
    artifact_name = f"{BACKUP_PREFIX}{backup_id}.dump"

    # The plaintext archive never receives a pathname. On supported Unix,
    # TemporaryFile is anonymous or immediately unlinked.
    with tempfile.TemporaryFile(mode="w+b") as archive:
        os.fchmod(archive.fileno(), 0o600)
        run(
            production_compose_command(
                "exec", "-T", "db", "sh", "-eu", "-c", DUMP_CONTAINER_COMMAND
            ),
            stdout=archive,
        )
        archive.flush()
        os.fsync(archive.fileno())
        archive_size = os.fstat(archive.fileno()).st_size
        if archive_size < 1:
            raise OperatorError("pg_dump produced an empty archive")
        if archive_size > MAX_DUMP_BYTES:
            raise OperatorError("pg_dump archive exceeds the 1 GiB safety bound")

        archive.seek(0)
        manifest_entries = _archive_manifest_entries(archive)
        if manifest_entries < 1:
            raise OperatorError("pg_restore listed no archive entries")
        archive.seek(0)
        archive_sha256 = sha256_stream(archive)

        completed_at = utc_now()
        metadata = {
            "artifact": {
                "bytes": archive_size,
                "file": artifact_name,
                "sha256": archive_sha256,
            },
            "backup_id": backup_id,
            "completed_at": format_utc(completed_at),
            "database": {
                "archive_format": "pg_dump-custom",
                "compression": 9,
                "ownership_included": False,
                "privileges_included": False,
            },
            "recovery_point_at": format_utc(started_at),
            "schema_version": 3,
            "source": {
                "compose_path": str(CANONICAL_SOURCE_PATH),
                "host": CANONICAL_SOURCE_HOST,
            },
            "started_at": format_utc(started_at),
            "storage": {
                "offsite": "not_verified_by_this_script",
                "transport": "ssh_push",
            },
            "validation": {
                "dump_manifest_entries": manifest_entries,
                "local_pg_restore_list": True,
                "source_archive_fsynced": True,
            },
        }
        metadata_bytes = (
            json.dumps(metadata, indent=2, sort_keys=True) + "\n"
        ).encode()

        archive.seek(0)
        _push("dump", backup_id, archive)
        with tempfile.TemporaryFile(mode="w+b") as metadata_file:
            os.fchmod(metadata_file.fileno(), 0o600)
            metadata_file.write(metadata_bytes)
            metadata_file.flush()
            os.fsync(metadata_file.fileno())
            _push("metadata", backup_id, metadata_file)

    print(f"backup pushed to TrueNAS: {backup_id}")
    print(f"artifact: {artifact_name}")
    print(f"metadata: {BACKUP_PREFIX}{backup_id}.json")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OperatorError, OSError, UnicodeError) as error:
        fail_safely(error)
        raise SystemExit(1) from None
