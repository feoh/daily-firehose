#!/usr/bin/env python3
"""Restore an encrypted backup into disposable Docker resources and verify it."""

from __future__ import annotations

import argparse
import os
import re
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from scripts.postgres_backup_common import (
    BACKUP_PREFIX,
    NAS_BACKUP_ROOT,
    OperatorError,
    compose_command,
    fail_safely,
    format_utc,
    read_json,
    resolve_adapter,
    run,
    sha256_file,
    utc_now,
    validate_nas_destination,
    write_json_atomic,
)

POSTGRES_IMAGE = "postgres:17-alpine"  # Match the canonical Compose database major.
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact",
        type=Path,
        required=True,
        help=f"selected encrypted artifact below {NAS_BACKUP_ROOT}",
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        required=True,
        help=f"matching metadata below {NAS_BACKUP_ROOT}",
    )
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--compose-file", type=Path, default=Path("docker-compose.yml"))
    parser.add_argument(
        "--app-image",
        help="application image ID/name; defaults to the locally built Compose web image",
    )
    return parser.parse_args()


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


def _restore_archive(decryptor: str, artifact: Path, container_name: str) -> None:
    try:
        decrypt = subprocess.Popen([decryptor, str(artifact)], stdout=subprocess.PIPE)
        if decrypt.stdout is None:
            decrypt.kill()
            raise OperatorError("decryption adapter stdout was unavailable")
        restore = subprocess.Popen(
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
            stdin=decrypt.stdout,
            stdout=subprocess.DEVNULL,
        )
        decrypt.stdout.close()
        restore_status = restore.wait()
        decrypt_status = decrypt.wait()
    except FileNotFoundError as error:
        raise OperatorError("restore verification executable was not found") from error
    if decrypt_status != 0 or restore_status != 0:
        raise OperatorError("encrypted archive failed isolated pg_restore")


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


def _validate_metadata(artifact: Path, metadata_path: Path) -> tuple[str, str]:
    metadata = read_json(metadata_path)
    backup_id = metadata.get("backup_id")
    artifact_metadata = metadata.get("artifact")
    validation = metadata.get("validation")
    if (
        not isinstance(backup_id, str)
        or not _SAFE_ID.fullmatch(backup_id)
        or not isinstance(artifact_metadata, dict)
        or not isinstance(validation, dict)
        or artifact_metadata.get("file") != artifact.name
        or validation.get("plain_archive_list") is not True
        or validation.get("encryption_adapter_completed") is not True
    ):
        raise OperatorError("metadata does not describe a verified backup artifact")
    expected_base = f"{BACKUP_PREFIX}{backup_id}"
    if artifact.name != f"{expected_base}.dump.age" or metadata_path.name != (
        f"{expected_base}.json"
    ):
        raise OperatorError("artifact and metadata filenames do not match backup ID")
    expected_sha256 = artifact_metadata.get("sha256")
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise OperatorError("metadata has no valid encrypted artifact checksum")
    actual_sha256 = sha256_file(artifact)
    if not secrets.compare_digest(actual_sha256, expected_sha256):
        raise OperatorError("encrypted artifact checksum does not match metadata")
    return backup_id, actual_sha256


def main() -> int:
    arguments = parse_arguments()
    artifact = arguments.artifact.resolve()
    metadata_path = arguments.metadata.resolve()
    compose_file = arguments.compose_file.resolve()
    evidence_dir = arguments.evidence_dir.resolve()
    if not artifact.is_file() or not metadata_path.is_file():
        raise OperatorError("artifact and metadata must be existing files")
    artifact_parent = validate_nas_destination(artifact.parent)
    metadata_parent = validate_nas_destination(metadata_path.parent)
    if artifact_parent != metadata_parent:
        raise OperatorError(
            "artifact and metadata must be selected from one NAS directory"
        )
    decryptor = resolve_adapter("BACKUP_DECRYPTOR")
    backup_id, artifact_sha256 = _validate_metadata(artifact, metadata_path)
    image = _app_image(compose_file, arguments.app_image)

    # Private restore evidence must not be readable by other local users.
    evidence_dir.mkdir(mode=0o700, parents=True, exist_ok=True)  # nosemgrep
    os.chmod(evidence_dir, 0o700)
    started_at = utc_now()
    monotonic_start = time.monotonic()
    resource_id = f"{started_at.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}"
    container_name = f"daily-firehose-restore-db-{resource_id}"
    network_name = f"daily-firehose-restore-net-{resource_id}"
    volume_name = f"daily-firehose-restore-data-{resource_id}"
    evidence_path = evidence_dir / (
        f"daily-firehose-restore-{backup_id}-{started_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    created = {"container": False, "network": False, "volume": False}
    checks: dict[str, bool] = {
        "application_check": False,
        "application_semantic_reads": False,
        "artifact_checksum": True,
        "migration_compatibility": False,
        "pg_restore": False,
        "required_schema": False,
    }
    failure: Exception | None = None
    cleanup_ok = True
    environment_path: Path | None = None

    try:
        descriptor, environment_name = tempfile.mkstemp(
            prefix="daily-firehose-restore-", suffix=".env"
        )
        environment_path = Path(environment_name)
        os.chmod(environment_path, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as environment:
            environment.write("POSTGRES_DB=restored_daily_firehose\n")
            environment.write("POSTGRES_USER=restore_verifier\n")
            environment.write(f"POSTGRES_PASSWORD={secrets.token_urlsafe(48)}\n")
            environment.write(f"POSTGRES_HOST={container_name}\n")
            environment.write("POSTGRES_PORT=5432\n")
            environment.write("DJANGO_ENV=production\n")
            environment.write("DJANGO_DEBUG=false\n")
            environment.write(f"DJANGO_SECRET_KEY={secrets.token_urlsafe(64)}\n")
            environment.write("DJANGO_ALLOWED_HOSTS=restore.invalid\n")
            environment.write("DJANGO_CSRF_TRUSTED_ORIGINS=https://restore.invalid\n")

        run(
            [
                "docker",
                "network",
                "create",
                "--label",
                "com.daily-firehose.restore=temporary",
                network_name,
            ],
            stdout=subprocess.DEVNULL,
        )
        created["network"] = True
        run(
            [
                "docker",
                "volume",
                "create",
                "--label",
                "com.daily-firehose.restore=temporary",
                volume_name,
            ],
            stdout=subprocess.DEVNULL,
        )
        created["volume"] = True
        run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                container_name,
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
        _wait_for_database(container_name)
        _restore_archive(decryptor, artifact, container_name)
        checks["pg_restore"] = True
        _schema_verify(container_name)
        checks["required_schema"] = True

        _application_verify(image, network_name, environment_path)
        checks["application_check"] = True
        checks["migration_compatibility"] = True
        checks["application_semantic_reads"] = True
    # Evidence and cleanup must survive any verifier failure before re-raising safely.
    except Exception as error:  # noqa: BLE001
        failure = error
    finally:
        if environment_path is not None:
            environment_path.unlink(missing_ok=True)
        cleanup_commands: list[list[str]] = []
        if created["container"]:
            cleanup_commands.append(["docker", "rm", "--force", container_name])
        if created["volume"]:
            cleanup_commands.append(["docker", "volume", "rm", volume_name])
        if created["network"]:
            cleanup_commands.append(["docker", "network", "rm", network_name])
        for command in cleanup_commands:
            try:
                run(command, stdout=subprocess.DEVNULL)
            except OperatorError:
                cleanup_ok = False

    completed_at = utc_now()
    evidence: dict[str, Any] = {
        "artifact_sha256": artifact_sha256,
        "backup_id": backup_id,
        "checks": checks,
        "cleanup": {
            "only_temporary_labeled_resources_targeted": True,
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
