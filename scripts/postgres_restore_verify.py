#!/usr/bin/env python3
"""Fetch one exact TrueNAS backup and verify it in disposable Docker resources."""

from __future__ import annotations

import argparse
import os
import re
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, BinaryIO

from scripts.postgres_backup_common import (
    BACKUP_ID_PATTERN,
    OperatorError,
    compose_command,
    fail_safely,
    format_utc,
    read_json_bytes,
    run,
    sha256_stream,
    utc_now,
    validate_backup_metadata,
    write_json_atomic,
)
from scripts.postgres_backup_ssh import ssh_command

POSTGRES_IMAGE = "postgres:17-alpine"
_MAX_METADATA_BYTES = 64 * 1024


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-id", required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--compose-file", type=Path, default=Path("docker-compose.yml"))
    parser.add_argument(
        "--app-image",
        help="application image ID/name; defaults to the locally built Compose web image",
    )
    arguments = parser.parse_args()
    if re.fullmatch(BACKUP_ID_PATTERN, arguments.backup_id) is None:
        parser.error("--backup-id has an invalid format")
    return arguments


def _app_image(compose_file: Path, configured: str | None) -> str:
    if configured:
        if configured.startswith("-") or any(
            character.isspace() for character in configured
        ):
            raise OperatorError("--app-image must be one image reference or ID")
        return configured
    result = run(
        compose_command(compose_file, "images", "-q", "web"), capture_output=True
    )
    images = [line for line in result.stdout.decode().splitlines() if line]
    if not images:
        raise OperatorError(
            "no local web image exists; build it or provide --app-image"
        )
    return images[0]


def _fetch_metadata(backup_id: str) -> dict[str, Any]:
    with tempfile.TemporaryFile(mode="w+b") as temporary:
        os.fchmod(temporary.fileno(), 0o600)
        run(ssh_command("read", backup_id, "metadata"), stdout=temporary)
        size = os.fstat(temporary.fileno()).st_size
        if size < 1 or size > _MAX_METADATA_BYTES:
            raise OperatorError("remote metadata is empty or exceeds its bound")
        temporary.seek(0)
        return read_json_bytes(temporary.read())


def _fetch_and_verify_dump(
    backup_id: str, metadata: dict[str, Any], archive: BinaryIO
) -> str:
    expected_size, expected_checksum, _ = validate_backup_metadata(metadata, backup_id)
    run(ssh_command("read", backup_id, "dump"), stdout=archive)
    archive.flush()
    os.fsync(archive.fileno())
    if os.fstat(archive.fileno()).st_size != expected_size:
        raise OperatorError("downloaded dump size does not match metadata")
    archive.seek(0)
    actual_checksum = sha256_stream(archive)
    if not secrets.compare_digest(actual_checksum, expected_checksum):
        raise OperatorError("downloaded dump checksum does not match metadata")
    archive.seek(0)
    return actual_checksum


def _remove_environment_file(path: Path) -> None:
    path.unlink(missing_ok=True)


def _wait_for_database(container_name: str) -> None:
    for _ in range(30):
        result = subprocess.run(
            [
                "docker",
                "exec",
                container_name,
                "pg_isready",
                "--username",
                "restore_verifier",
                "--dbname",
                "restored_daily_firehose",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise OperatorError("temporary restore database did not become ready")


def _restore_archive(archive: BinaryIO, container_name: str) -> None:
    archive.seek(0)
    run(
        [
            "docker",
            "exec",
            "-i",
            container_name,
            "pg_restore",
            "--exit-on-error",
            "--no-owner",
            "--no-acl",
            "--username",
            "restore_verifier",
            "--dbname",
            "restored_daily_firehose",
        ],
        stdin=archive,
        stdout=subprocess.DEVNULL,
    )


def _schema_verify(container_name: str) -> None:
    sql = """
SELECT concat_ws('|',
  CASE WHEN to_regclass('public.django_migrations') IS NOT NULL THEN 1 ELSE 0 END,
  CASE WHEN to_regclass('public.auth_user') IS NOT NULL THEN 1 ELSE 0 END,
  CASE WHEN to_regclass('public.feeds_feed') IS NOT NULL THEN 1 ELSE 0 END,
  CASE WHEN to_regclass('public.feeds_article') IS NOT NULL THEN 1 ELSE 0 END,
  CASE WHEN to_regclass('public.feeds_newsletterissue') IS NOT NULL THEN 1 ELSE 0 END
);
"""
    result = run(
        [
            "docker",
            "exec",
            container_name,
            "psql",
            "--username",
            "restore_verifier",
            "--dbname",
            "restored_daily_firehose",
            "--tuples-only",
            "--no-align",
            "--set",
            "ON_ERROR_STOP=1",
            "--command",
            sql,
        ],
        capture_output=True,
    )
    if result.stdout.decode("utf-8", errors="strict").strip() != "1|1|1|1|1":
        raise OperatorError("restored database is missing required application tables")


def _application_verify(image: str, network_name: str, environment_file: Path) -> None:
    base = [
        "docker",
        "run",
        "--rm",
        "--network",
        network_name,
        "--env-file",
        str(environment_file),
        image,
        "python",
        "manage.py",
    ]
    run([*base, "check", "--database", "default"])
    run([*base, "migrate", "--check"])
    semantic_read = (
        "from django.apps import apps; "
        "from django.contrib.auth import get_user_model; "
        "models=list(apps.get_app_config('feeds').get_models())+[get_user_model()]; "
        "[model._default_manager.order_by('pk').values_list('pk', flat=True).first() "
        "for model in models]"
    )
    run([*base, "shell", "--command", semantic_read])


def main() -> int:
    arguments = parse_arguments()
    backup_id: str = arguments.backup_id
    compose_file = arguments.compose_file.resolve()
    evidence_dir = arguments.evidence_dir.resolve()
    evidence_dir.mkdir(mode=0o700, parents=True, exist_ok=True)  # nosemgrep
    os.chmod(evidence_dir, 0o700)

    started_at = utc_now()
    monotonic_start = time.monotonic()
    resource_id = f"{started_at.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}"
    container_name = f"daily-firehose-restore-db-{resource_id}"
    network_name = f"daily-firehose-restore-net-{resource_id}"
    volume_name = f"daily-firehose-restore-data-{resource_id}"
    evidence_path = evidence_dir / (
        f"daily-firehose-restore-{backup_id}-{resource_id}.json"
    )
    run_label = f"com.daily-firehose.restore.run={resource_id}"
    created = {"container": False, "network": False, "volume": False}
    cleanup_status = {
        "container": "not_created",
        "network": "not_created",
        "volume": "not_created",
    }
    checks = {
        "application_check": False,
        "application_semantic_reads": False,
        "artifact_checksum": False,
        "metadata_complete": False,
        "migration_compatibility": False,
        "pg_restore": False,
        "required_schema": False,
        "transfer_complete": False,
    }
    failure: Exception | None = None
    cleanup_ok = True
    artifact_sha256: str | None = None
    environment_path: Path | None = None
    environment_cleanup_status = "not_created"

    # The selected unencrypted dump has no pathname and is removed when this context
    # closes, including every ordinary failure path.
    with tempfile.TemporaryFile(mode="w+b") as archive:
        os.fchmod(archive.fileno(), 0o600)
        try:
            run(ssh_command("health"), capture_output=True)
            metadata = _fetch_metadata(backup_id)
            validate_backup_metadata(metadata, backup_id)
            checks["metadata_complete"] = True
            artifact_sha256 = _fetch_and_verify_dump(backup_id, metadata, archive)
            checks["transfer_complete"] = True
            checks["artifact_checksum"] = True
            image = _app_image(compose_file, arguments.app_image)

            descriptor, environment_name = tempfile.mkstemp(
                prefix="daily-firehose-restore-", suffix=".env"
            )
            environment_path = Path(environment_name)
            environment_cleanup_status = "pending"
            os.chmod(environment_path, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as environment:
                environment.write("POSTGRES_DB=restored_daily_firehose\n")
                environment.write("POSTGRES_USER=restore_verifier\n")
                environment.write(f"POSTGRES_PASSWORD={secrets.token_urlsafe(48)}\n")
                environment.write(f"POSTGRES_HOST={container_name}\n")
                environment.write("POSTGRES_PORT=5432\n")
                environment.write("DJANGO_ENV=production\nDJANGO_DEBUG=false\n")
                environment.write(f"DJANGO_SECRET_KEY={secrets.token_urlsafe(64)}\n")
                environment.write("DJANGO_ALLOWED_HOSTS=restore.invalid\n")
                environment.write(
                    "DJANGO_CSRF_TRUSTED_ORIGINS=https://restore.invalid\n"
                )

            run(
                [
                    "docker",
                    "network",
                    "create",
                    "--label",
                    run_label,
                    network_name,
                ],
                stdout=subprocess.DEVNULL,
            )
            created["network"] = True
            cleanup_status["network"] = "pending"
            run(
                [
                    "docker",
                    "volume",
                    "create",
                    "--label",
                    run_label,
                    volume_name,
                ],
                stdout=subprocess.DEVNULL,
            )
            created["volume"] = True
            cleanup_status["volume"] = "pending"
            run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--name",
                    container_name,
                    "--label",
                    run_label,
                    "--network",
                    network_name,
                    "--mount",
                    f"source={volume_name},target=/var/lib/postgresql/data",
                    "--env-file",
                    str(environment_path),
                    POSTGRES_IMAGE,
                ],
                stdout=subprocess.DEVNULL,
            )
            created["container"] = True
            cleanup_status["container"] = "pending"
            _wait_for_database(container_name)
            _restore_archive(archive, container_name)
            checks["pg_restore"] = True
            _schema_verify(container_name)
            checks["required_schema"] = True
            _application_verify(image, network_name, environment_path)
            checks["application_check"] = True
            checks["migration_compatibility"] = True
            checks["application_semantic_reads"] = True
        except Exception as error:  # noqa: BLE001 - evidence every bounded failure
            failure = error
        finally:
            if environment_path is not None:
                try:
                    _remove_environment_file(environment_path)
                    environment_cleanup_status = "removed"
                except (OperatorError, OSError):
                    environment_cleanup_status = "failed"
                    cleanup_ok = False
            cleanup_commands: list[tuple[str, list[str]]] = []
            if created["container"]:
                cleanup_commands.append(
                    ("container", ["docker", "rm", "--force", container_name])
                )
            if created["volume"]:
                cleanup_commands.append(
                    ("volume", ["docker", "volume", "rm", volume_name])
                )
            if created["network"]:
                cleanup_commands.append(
                    ("network", ["docker", "network", "rm", network_name])
                )
            for kind, command in cleanup_commands:
                try:
                    run(command, stdout=subprocess.DEVNULL)
                    cleanup_status[kind] = "removed"
                except (OperatorError, OSError):
                    cleanup_status[kind] = "failed"
                    cleanup_ok = False

    completed_at = utc_now()
    evidence: dict[str, Any] = {
        "artifact_sha256": artifact_sha256,
        "backup_id": backup_id,
        "checks": checks,
        "cleanup": {
            "environment_file_status": environment_cleanup_status,
            "label": run_label,
            "only_exact_run_labeled_resources_targeted": True,
            "resources": {
                "container": {
                    "cleanup_status": cleanup_status["container"],
                    "name": container_name,
                },
                "network": {
                    "cleanup_status": cleanup_status["network"],
                    "name": network_name,
                },
                "volume": {
                    "cleanup_status": cleanup_status["volume"],
                    "name": volume_name,
                },
            },
            "succeeded": cleanup_ok,
        },
        "completed_at": format_utc(completed_at),
        "duration_seconds": round(time.monotonic() - monotonic_start, 3),
        "result": "passed" if failure is None and cleanup_ok else "failed",
        "schema_version": 1,
        "started_at": format_utc(started_at),
    }
    if failure is not None:
        evidence["failure_type"] = type(failure).__name__
    write_json_atomic(evidence_path, evidence)

    if failure is not None:
        if isinstance(failure, OperatorError):
            raise failure
        raise OperatorError("isolated restore verification failed safely") from failure
    if not cleanup_ok:
        raise OperatorError(
            "restore checks passed but temporary resource cleanup failed"
        )
    print(f"isolated restore verification passed: {backup_id}")
    print(f"duration_seconds: {evidence['duration_seconds']}")
    print(f"evidence: {evidence_path.name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OperatorError, OSError, UnicodeError) as error:
        fail_safely(error)
        raise SystemExit(1) from None
