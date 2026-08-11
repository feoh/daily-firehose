# pyright: reportUninitializedInstanceVariable=false
# unittest initializes fixture attributes in setUp before each case.
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shlex
import socket
import sys
import tempfile
import textwrap
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, final
from unittest import mock

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts import postgres_backup
from scripts import postgres_backup_common as common
from scripts import postgres_backup_retention as retention
from scripts import postgres_restore_verify as restore


@final
class PostgresBackupScriptCases(unittest.TestCase):
    """Explicit-only mutation cases; this module is not named test_*.py."""

    temporary_directory: tempfile.TemporaryDirectory[str]
    root: Path
    bin_dir: Path
    ssh_log: Path
    docker_log: Path
    plaintext_spool: Path
    output_dir: Path
    encryptor: Path
    decryptor: Path
    environment: Any

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.ssh_log = self.root / "ssh.log"
        self.docker_log = self.root / "docker.log"
        self.plaintext_spool = self.root / "plaintext-spool"
        self.plaintext_spool.mkdir(mode=0o700)
        self.output_dir = self.root / "nas-backups"
        self.output_dir.mkdir(mode=0o700)
        self._write_executable(
            "ssh",
            """
            import os
            import pathlib
            import shlex
            import sys

            log = pathlib.Path(os.environ["FAKE_SSH_LOG"])
            with log.open("a", encoding="utf-8") as output:
                output.write(repr(sys.argv[1:]) + "\\n")
            args = shlex.split(sys.argv[-1])
            if args[-2:] == ["config", "--services"]:
                print("db\\nweb\\nrefresh-feeds")
            elif "pg_dump" in args[-1]:
                sys.stdout.buffer.write(b"PGDMP-fake-custom-archive")
            elif args[-2:] == ["pg_restore", "--list"]:
                data = sys.stdin.buffer.read()
                if not data.startswith(b"PGDMP"):
                    raise SystemExit(4)
                print("; archive header")
                print("1; 0 1 TABLE public feeds_feed owner")
                print("2; 0 2 TABLE DATA public feeds_feed owner")
            else:
                raise SystemExit("unexpected fake ssh arguments: " + repr(args))
            """,
        )
        self._write_executable(
            "docker",
            """
            import os
            import pathlib
            import sys

            args = sys.argv[1:]
            log = pathlib.Path(os.environ["FAKE_DOCKER_LOG"])
            with log.open("a", encoding="utf-8") as output:
                output.write(" ".join(args) + "\\n")
            if args[:1] == ["compose"] and "images" in args:
                print("fake-app-image")
            elif args[:1] == ["exec"] and "pg_isready" in args:
                pass
            elif args[:1] == ["exec"] and "pg_restore" in args:
                data = sys.stdin.buffer.read()
                if not data.startswith(b"PGDMP"):
                    raise SystemExit(5)
            elif args[:1] == ["exec"] and "psql" in args:
                print("1|1|1|1|1")
            elif args[:2] == ["run", "--rm"]:
                pass
            elif args[:2] == ["run", "--detach"]:
                print("temporary-restore-container")
            elif args[:2] in (
                ["network", "create"], ["network", "rm"],
                ["volume", "create"], ["volume", "rm"],
            ):
                pass
            elif args[:2] == ["rm", "--force"]:
                pass
            else:
                raise SystemExit("unexpected fake docker arguments: " + repr(args))
            """,
        )
        self.encryptor = self._write_executable(
            "encrypt-for-backup",
            """
            import os
            import pathlib
            import stat
            import sys

            spool = pathlib.Path(os.environ["FAKE_PLAINTEXT_SPOOL"])
            if list(spool.iterdir()):
                raise SystemExit("plaintext dump had a directory entry")
            if stat.S_IMODE(os.fstat(sys.stdin.fileno()).st_mode) != 0o600:
                raise SystemExit("plaintext descriptor was not mode 0600")
            pathlib.Path(sys.argv[1]).write_bytes(b"AGE" + sys.stdin.buffer.read())
            """,
        )
        self.decryptor = self._write_executable(
            "decrypt-for-backup",
            """
            import pathlib
            import sys
            data = pathlib.Path(sys.argv[1]).read_bytes()
            if not data.startswith(b"AGE"):
                raise SystemExit(3)
            sys.stdout.buffer.write(data[3:])
            """,
        )
        self.environment = mock.patch.dict(
            os.environ,
            {
                "BACKUP_DECRYPTOR": str(self.decryptor),
                "BACKUP_ENCRYPTOR": str(self.encryptor),
                "FAKE_DOCKER_LOG": str(self.docker_log),
                "FAKE_PLAINTEXT_SPOOL": str(self.plaintext_spool),
                "FAKE_SSH_LOG": str(self.ssh_log),
                "PATH": f"{self.bin_dir}{os.pathsep}{os.environ['PATH']}",
                "SHOULD_NOT_BE_LOGGED_SECRET": "top-secret-test-sentinel",
            },
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary_directory.cleanup()

    def _write_executable(self, name: str, body: str) -> Path:
        path = self.bin_dir / name
        path.write_text(
            "#!/usr/bin/env python3\n" + textwrap.dedent(body), encoding="utf-8"
        )
        path.chmod(0o755)
        return path

    def _run_backup(self) -> tuple[int, str]:
        stdout = io.StringIO()
        argv = [
            "postgres_backup.py",
            "--output-dir",
            str(self.output_dir),
            "--temporary-dir",
            str(self.plaintext_spool),
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(
                postgres_backup,
                "validate_nas_destination",
                return_value=self.output_dir,
            ),
            contextlib.redirect_stdout(stdout),
        ):
            status = postgres_backup.main()
        return status, stdout.getvalue()

    def test_pull_backup_uses_batchmode_remote_compose_and_confirms_nas_pair(
        self,
    ) -> None:
        status, output = self._run_backup()

        self.assertEqual(status, 0)
        artifacts = list(self.output_dir.glob("*.dump.age"))
        metadata_files = list(self.output_dir.glob("*.json"))
        self.assertEqual((len(artifacts), len(metadata_files)), (1, 1))
        self.assertTrue(artifacts[0].read_bytes().startswith(b"AGEPGDMP"))
        metadata = json.loads(metadata_files[0].read_text(encoding="utf-8"))
        self.assertEqual(metadata["source"]["host"], "daily-firehose")
        self.assertEqual(
            metadata["source"]["compose_path"], "/home/ubuntu/daily-firehose"
        )
        self.assertEqual(metadata["storage"]["status"], "nas_cifs_confirmed")
        self.assertEqual(
            metadata["storage"]["offsite_backup"], "not_verified_by_this_script"
        )
        self.assertTrue(metadata["validation"]["encrypted_artifact_fsynced"])
        self.assertEqual(metadata["recovery_point_at"], metadata["started_at"])
        self.assertNotIn("top-secret-test-sentinel", output)
        self.assertFalse(list(self.plaintext_spool.iterdir()))

        ssh_calls = [line for line in self.ssh_log.read_text().splitlines() if line]
        self.assertEqual(len(ssh_calls), 3)
        self.assertTrue(all("BatchMode=yes" in line for line in ssh_calls))
        self.assertTrue(
            all("/home/ubuntu/daily-firehose" in line for line in ssh_calls)
        )
        self.assertTrue(
            all("top-secret-test-sentinel" not in line for line in ssh_calls)
        )

    def test_anonymous_plaintext_descriptor_leaves_no_name_when_encryption_fails(
        self,
    ) -> None:
        failing = self._write_executable("failing-encryptor", "raise SystemExit(9)")
        os.environ["BACKUP_ENCRYPTOR"] = str(failing)
        with self.assertRaises(common.OperatorError) as caught:
            self._run_backup()
        self.assertEqual(
            str(caught.exception), "command failed safely: failing-encryptor"
        )
        self.assertFalse(list(self.plaintext_spool.iterdir()))
        self.assertFalse(list(self.output_dir.iterdir()))
        self.assertNotIn("top-secret-test-sentinel", str(caught.exception))

    def test_metadata_confirmation_compares_every_critical_field(self) -> None:
        def corrupt_confirmation(path: Path) -> dict[str, Any]:
            metadata = common.read_json(path)
            validation = metadata["validation"]
            assert isinstance(validation, dict)
            validation["encrypted_artifact_fsynced"] = False
            return metadata

        with (
            mock.patch.object(
                postgres_backup, "read_json", side_effect=corrupt_confirmation
            ),
            self.assertRaisesRegex(common.OperatorError, "confirmation failed"),
        ):
            self._run_backup()

    def test_remote_command_rejects_unapproved_host_path_and_arguments(self) -> None:
        with self.assertRaisesRegex(common.OperatorError, "canonical 'daily-firehose'"):
            common.remote_compose_command(
                "daily-firehose;echo-owned",
                common.CANONICAL_SOURCE_PATH,
                "config",
                "--services",
            )
        with self.assertRaisesRegex(common.OperatorError, "canonical .* checkout"):
            common.remote_compose_command(
                common.CANONICAL_SOURCE_HOST,
                Path("/tmp/checkout;echo-owned"),
                "config",
                "--services",
            )
        with self.assertRaisesRegex(common.OperatorError, "unsupported"):
            common.remote_compose_command(
                common.CANONICAL_SOURCE_HOST,
                common.CANONICAL_SOURCE_PATH,
                "exec",
                "db",
                "env",
            )

        command = common.remote_compose_command(
            common.CANONICAL_SOURCE_HOST,
            common.CANONICAL_SOURCE_PATH,
            "config",
            "--services",
        )
        self.assertEqual(
            command[:5], ["ssh", "-o", "BatchMode=yes", "--", "daily-firehose"]
        )
        remote = shlex.split(command[5])
        self.assertEqual(remote[-2:], ["config", "--services"])

    def test_nas_validation_requires_approved_resolved_path_and_active_cifs(
        self,
    ) -> None:
        mount = self.root / "nas" / "homes"
        approved = mount / "backups" / "daily-firehose"
        approved.mkdir(parents=True)
        destination = approved / "selected"
        destination.mkdir()
        mountinfo = self.root / "mountinfo"
        mountinfo.write_text(
            f"23 1 0:19 / {mount} rw,relatime - autofs systemd-1 rw\n"
            f"24 23 0:20 / {mount} rw,relatime - cifs //nas/homes rw\n",
            encoding="utf-8",
        )
        with mock.patch.object(common, "_device_number", return_value="0:20"):
            self.assertEqual(
                common.validate_nas_destination(
                    destination,
                    approved_root=approved,
                    mount_point=mount,
                    mountinfo_path=mountinfo,
                ),
                destination,
            )

        mountinfo.write_text(
            f"23 1 0:19 / {mount} rw,relatime - autofs systemd-1 rw\n"
            f"24 23 0:20 / {mount} rw,relatime - cifs //nas/homes rw\n"
            f"25 24 0:21 / {mount} rw,relatime - ext4 /dev/fake rw\n",
            encoding="utf-8",
        )
        with (
            mock.patch.object(common, "_device_number", return_value="0:21"),
            self.assertRaisesRegex(common.OperatorError, "effective active CIFS"),
        ):
            common.validate_nas_destination(
                destination,
                approved_root=approved,
                mount_point=mount,
                mountinfo_path=mountinfo,
            )
        outside = self.root / "outside"
        outside.mkdir()
        with self.assertRaisesRegex(common.OperatorError, "outside"):
            common.validate_nas_destination(
                outside,
                approved_root=approved,
                mount_point=mount,
                mountinfo_path=mountinfo,
            )

    def test_exact_loki_nas_path_is_read_only_validated_when_present(self) -> None:
        if socket.gethostname().split(".", maxsplit=1)[0] != "loki":
            self.skipTest("read-only local check runs only on the exact loki host")
        if not common.NAS_MOUNT_POINT.is_dir() or not common.NAS_BACKUP_ROOT.is_dir():
            self.skipTest("approved loki NAS paths are not present")
        filesystems = common._effective_mount_filesystems(
            common.NAS_BACKUP_ROOT.resolve(strict=True),
            Path("/proc/self/mountinfo"),
        )
        if not filesystems or not filesystems.issubset({"cifs", "smb3"}):
            self.skipTest("approved loki path is not effectively backed by CIFS")

        validated = common.validate_nas_destination(common.NAS_BACKUP_ROOT)

        self.assertEqual(validated, common.NAS_BACKUP_ROOT.resolve(strict=True))

    def test_retention_tier_boundaries_choose_newest_utc_bucket_points(self) -> None:
        now = datetime(2026, 8, 12, 0, 0, tzinfo=UTC)
        moments = [
            now - timedelta(days=7),
            now - timedelta(days=7, seconds=1),
            now - timedelta(days=7, hours=1),
            now - timedelta(days=30),
            now - timedelta(days=30, seconds=1),
            now - timedelta(days=31),
            now - timedelta(days=90),
            now - timedelta(days=90, seconds=1),
            now - timedelta(days=100),
            now - timedelta(days=365),
            now - timedelta(days=365, seconds=1),
        ]
        pairs = [self._pair(str(index), moment) for index, moment in enumerate(moments)]

        kept, deleted = retention.retention_partition(pairs, now)

        kept_ids = {pair.backup_id for pair in kept}
        deleted_ids = {pair.backup_id for pair in deleted}
        self.assertIn("0", kept_ids)  # exact seven-day boundary keeps every point
        self.assertIn("1", kept_ids)  # newest in its UTC day
        self.assertIn("2", deleted_ids)  # older same-day point
        self.assertIn("3", kept_ids)  # exact 30-day boundary is daily
        self.assertIn("4", kept_ids)  # just past 30 days enters ISO-week tier
        self.assertIn("6", kept_ids)  # exact 90-day boundary is weekly
        self.assertIn("7", kept_ids)  # just past 90 days enters monthly tier
        self.assertIn("9", kept_ids)  # exact 365-day boundary is monthly
        self.assertIn("10", deleted_ids)  # older than 365 days expires

    def test_retention_age_uses_recovery_point_not_later_completion(self) -> None:
        now = datetime(2026, 8, 12, 0, 0, tzinfo=UTC)
        current = self._pair("current", now - timedelta(hours=1))
        slow_old = self._pair("slow-old", now - timedelta(days=366))

        kept, deleted = retention.retention_partition([current, slow_old], now)

        self.assertEqual([pair.backup_id for pair in kept], ["current"])
        self.assertEqual([pair.backup_id for pair in deleted], ["slow-old"])

    def test_retention_always_preserves_one_old_last_known_good_pair(self) -> None:
        now = datetime(2026, 8, 12, 0, 0, tzinfo=UTC)
        only_pair = self._pair("only-old", now - timedelta(days=900))

        kept, deleted = retention.retention_partition([only_pair], now)

        self.assertEqual(kept, [only_pair])
        self.assertEqual(deleted, [])

    def test_retention_long_outage_preserves_newest_of_multiple_old_pairs(self) -> None:
        now = datetime(2026, 8, 12, 0, 0, tzinfo=UTC)
        pairs = [
            self._pair("newest-old", now - timedelta(days=500)),
            self._pair("middle-old", now - timedelta(days=600)),
            self._pair("oldest-old", now - timedelta(days=700)),
        ]

        kept, deleted = retention.retention_partition(pairs, now)

        self.assertEqual([pair.backup_id for pair in kept], ["newest-old"])
        self.assertEqual(
            {pair.backup_id for pair in deleted}, {"middle-old", "oldest-old"}
        )

    def test_modeled_retention_count_stays_in_projection_across_calendars(self) -> None:
        calendar_points = [
            datetime(2024, 3, 1, 0, 0, tzinfo=UTC),  # leap-day boundary
            datetime(2025, 1, 1, 0, 0, tzinfo=UTC),  # ISO-year boundary
            datetime(2026, 8, 31, 12, 0, tzinfo=UTC),  # month boundary
            datetime(2026, 12, 31, 12, 0, tzinfo=UTC),  # year boundary
        ]
        for now in calendar_points:
            with self.subTest(now=now):
                pairs = [
                    self._pair(str(index), now - timedelta(hours=12 * index))
                    for index in range(801)
                ]
                kept, _ = retention.retention_partition(pairs, now)
                self.assertGreaterEqual(len(kept), 56)
                self.assertLessEqual(len(kept), 60)

    def test_retention_dry_run_is_idempotent_and_apply_deletes_valid_pairs_only(
        self,
    ) -> None:
        now = datetime(2026, 8, 12, 0, 0, tzinfo=UTC)
        current_artifact, current_metadata = self._write_backup_pair(
            "current", now - timedelta(hours=12), confirmed=True
        )
        old_artifact, old_metadata = self._write_backup_pair(
            "old", now - timedelta(days=366), confirmed=True
        )
        invalid_artifact, invalid_metadata = self._write_backup_pair(
            "invalid", now - timedelta(days=500), confirmed=False
        )

        dry_outputs: list[str] = []
        for _ in range(2):
            output = io.StringIO()
            with (
                mock.patch.object(
                    sys, "argv", ["retention", "--output-dir", str(self.output_dir)]
                ),
                mock.patch.object(
                    retention, "validate_nas_destination", return_value=self.output_dir
                ),
                mock.patch.object(retention, "utc_now", return_value=now),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(retention.main(), 0)
            dry_outputs.append(output.getvalue())
        self.assertEqual(dry_outputs[0], dry_outputs[1])
        self.assertTrue(old_artifact.exists() and old_metadata.exists())
        self.assertIn("eligible=1", dry_outputs[0])
        self.assertIn("skipped=1", dry_outputs[0])

        with (
            mock.patch.object(
                sys,
                "argv",
                ["retention", "--output-dir", str(self.output_dir), "--apply"],
            ),
            mock.patch.object(
                retention, "validate_nas_destination", return_value=self.output_dir
            ) as validate_destination,
            mock.patch.object(retention, "fsync_directory") as fsync_directory,
            mock.patch.object(retention, "utc_now", return_value=now),
        ):
            self.assertEqual(retention.main(), 0)
        self.assertEqual(validate_destination.call_count, 2)
        fsync_directory.assert_called_once_with(self.output_dir)
        self.assertFalse(old_artifact.exists() or old_metadata.exists())
        self.assertTrue(current_artifact.exists() and current_metadata.exists())
        self.assertTrue(invalid_artifact.exists() and invalid_metadata.exists())

    def test_scheduler_is_loki_pull_twice_daily_and_dormant(self) -> None:
        service = (
            REPOSITORY_ROOT / "deploy/systemd/daily-firehose-postgresql-backup.service"
        ).read_text(encoding="utf-8")
        timer = (
            REPOSITORY_ROOT / "deploy/systemd/daily-firehose-postgresql-backup.timer"
        ).read_text(encoding="utf-8")
        self.assertIn("User=feoh", service)
        self.assertIn("Group=feoh", service)
        self.assertIn("force-owned by feoh", service)
        self.assertIn("RequiresMountsFor=/nas/homes", service)
        self.assertNotIn("/usr/bin/ssh", service)
        self.assertIn(
            "require_remote_compose_db", postgres_backup.main.__code__.co_names
        )
        self.assertIn("daily-firehose", service)
        self.assertIn("OnCalendar=*-*-* 00,12:00:00 UTC", timer)
        self.assertIn("Persistent=true", timer)
        self.assertNotIn("enable --now", service + timer)
        self.assertNotIn("BACKUP_UPLOADER", service + timer)
        self.assertNotIn("down -v", service + timer)

    def test_restore_uses_disposable_loki_resources_for_selected_nas_pair(self) -> None:
        artifact, metadata = self._write_restore_pair("restore")
        evidence_dir = self.root / "evidence"
        argv = [
            "restore",
            "--artifact",
            str(artifact),
            "--metadata",
            str(metadata),
            "--evidence-dir",
            str(evidence_dir),
            "--app-image",
            "fake-app-image",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(
                restore, "validate_nas_destination", return_value=self.output_dir
            ),
        ):
            self.assertEqual(restore.main(), 0)
        evidence = json.loads(next(evidence_dir.glob("*.json")).read_text())
        self.assertEqual(evidence["result"], "passed")
        self.assertTrue(all(evidence["checks"].values()))
        commands = self.docker_log.read_text(encoding="utf-8")
        self.assertIn("rm --force daily-firehose-restore-db-", commands)
        self.assertIn("volume rm daily-firehose-restore-data-", commands)
        self.assertNotIn("compose down", commands)
        self.assertNotIn("postgres-data", commands)
        self.assertNotIn("top-secret-test-sentinel", commands)

    def test_restore_failures_preserve_bounded_evidence_and_exact_cleanup(self) -> None:
        cases = {
            "readiness": (
                "_wait_for_database",
                "temporary database not ready",
                "pg_restore",
            ),
            "schema": (
                "_schema_verify",
                "schema verification failed",
                "required_schema",
            ),
            "application": (
                "_application_verify",
                "application verification failed",
                "application_check",
            ),
        }
        for case_name, (target, message, expected_failed_check) in cases.items():
            with self.subTest(case=case_name):
                artifact, metadata = self._write_restore_pair(case_name)
                self.docker_log.write_text("", encoding="utf-8")
                with mock.patch.object(
                    restore, target, side_effect=common.OperatorError(message)
                ):
                    evidence = self._run_failed_restore(
                        artifact, metadata, evidence_name=case_name
                    )
                self.assertEqual(evidence["result"], "failed")
                self.assertEqual(evidence["failure_type"], "OperatorError")
                self.assertFalse(evidence["checks"][expected_failed_check])
                self.assertNotIn(message, json.dumps(evidence))
                self._assert_cleanup_stays_bounded()

        artifact, metadata = self._write_restore_pair("decrypt")
        failing_decryptor = self._write_executable(
            "failing-decryptor", "raise SystemExit(9)"
        )
        os.environ["BACKUP_DECRYPTOR"] = str(failing_decryptor)
        self.docker_log.write_text("", encoding="utf-8")
        evidence = self._run_failed_restore(artifact, metadata, evidence_name="decrypt")
        self.assertEqual(evidence["result"], "failed")
        self.assertEqual(evidence["failure_type"], "OperatorError")
        self.assertFalse(evidence["checks"]["pg_restore"])
        self._assert_cleanup_stays_bounded()
        os.environ["BACKUP_DECRYPTOR"] = str(self.decryptor)

    def test_restore_cleanup_failure_is_evidenced_without_broadening_targets(
        self,
    ) -> None:
        artifact, metadata = self._write_restore_pair("cleanup")
        real_run = restore.run

        def fail_exact_volume_cleanup(command: list[str], **kwargs: Any) -> Any:
            if command[:3] == ["docker", "volume", "rm"]:
                raise common.OperatorError("simulated exact volume cleanup failure")
            return real_run(command, **kwargs)

        self.docker_log.write_text("", encoding="utf-8")
        with mock.patch.object(restore, "run", side_effect=fail_exact_volume_cleanup):
            evidence = self._run_failed_restore(
                artifact, metadata, evidence_name="cleanup"
            )
        self.assertEqual(evidence["result"], "failed")
        self.assertTrue(all(evidence["checks"].values()))
        self.assertFalse(evidence["cleanup"]["succeeded"])
        self.assertTrue(
            evidence["cleanup"]["only_temporary_labeled_resources_targeted"]
        )
        self._assert_cleanup_stays_bounded()

    def _run_failed_restore(
        self, artifact: Path, metadata: Path, *, evidence_name: str
    ) -> dict[str, Any]:
        evidence_dir = self.root / f"evidence-{evidence_name}"
        argv = [
            "restore",
            "--artifact",
            str(artifact),
            "--metadata",
            str(metadata),
            "--evidence-dir",
            str(evidence_dir),
            "--app-image",
            "fake-app-image",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(
                restore, "validate_nas_destination", return_value=self.output_dir
            ),
            self.assertRaises(common.OperatorError),
        ):
            restore.main()
        evidence_files = list(evidence_dir.glob("*.json"))
        self.assertEqual(len(evidence_files), 1)
        value = json.loads(evidence_files[0].read_text(encoding="utf-8"))
        self.assertIsInstance(value, dict)
        return value

    def _assert_cleanup_stays_bounded(self) -> None:
        commands = self.docker_log.read_text(encoding="utf-8")
        self.assertNotIn("compose down", commands)
        self.assertNotIn("system prune", commands)
        self.assertNotIn("postgres-data", commands)
        for line in commands.splitlines():
            if line.startswith("rm --force "):
                self.assertIn("daily-firehose-restore-db-", line)
            if line.startswith("volume rm "):
                self.assertIn("daily-firehose-restore-data-", line)
            if line.startswith("network rm "):
                self.assertIn("daily-firehose-restore-net-", line)

    def _pair(
        self, backup_id: str, recovery_point_at: datetime
    ) -> retention.BackupPair:
        return retention.BackupPair(
            backup_id=backup_id,
            recovery_point_at=recovery_point_at,
            artifact_path=self.output_dir / f"{backup_id}.dump.age",
            metadata_path=self.output_dir / f"{backup_id}.json",
            bytes=1,
        )

    def _write_backup_pair(
        self, backup_id: str, completed_at: datetime, *, confirmed: bool
    ) -> tuple[Path, Path]:
        artifact = self.output_dir / f"daily-firehose-postgres-{backup_id}.dump.age"
        artifact.write_bytes(b"encrypted")
        metadata = self.output_dir / f"daily-firehose-postgres-{backup_id}.json"
        metadata.write_text(
            json.dumps(
                {
                    "artifact": {
                        "bytes": artifact.stat().st_size,
                        "file": artifact.name,
                        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    },
                    "backup_id": backup_id,
                    "completed_at": (completed_at + timedelta(minutes=5))
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "recovery_point_at": completed_at.isoformat().replace(
                        "+00:00", "Z"
                    ),
                    "storage": {
                        "status": "nas_cifs_confirmed" if confirmed else "failed"
                    },
                    "validation": {
                        "encrypted_artifact_fsynced": True,
                        "encryption_adapter_completed": True,
                        "plain_archive_list": True,
                    },
                }
            ),
            encoding="utf-8",
        )
        return artifact, metadata

    def _write_restore_pair(self, backup_id: str) -> tuple[Path, Path]:
        artifact = self.output_dir / f"daily-firehose-postgres-{backup_id}.dump.age"
        artifact.write_bytes(b"AGEPGDMP-fake-custom-archive")
        metadata = self.output_dir / f"daily-firehose-postgres-{backup_id}.json"
        metadata.write_text(
            json.dumps(
                {
                    "artifact": {
                        "file": artifact.name,
                        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    },
                    "backup_id": backup_id,
                    "validation": {
                        "encryption_adapter_completed": True,
                        "plain_archive_list": True,
                    },
                }
            ),
            encoding="utf-8",
        )
        return artifact, metadata


if __name__ == "__main__":
    unittest.main()
