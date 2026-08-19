# pyright: reportUninitializedInstanceVariable=false
"""Explicit fake-transport and receiver mutation tests (outside Django discovery)."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, final
from unittest import mock

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts import postgres_backup
from scripts import postgres_backup_common as common
from scripts import postgres_backup_receiver as receiver
from scripts import postgres_backup_ssh as backup_ssh
from scripts import postgres_restore_verify as restore

BACKUP_ID = "20260812T000000Z-abcdef12"


def metadata_for(
    backup_id: str, dump: bytes, *, recovery_point: datetime | None = None
) -> dict[str, Any]:
    started = recovery_point or datetime(2026, 8, 12, tzinfo=UTC)
    return {
        "artifact": {
            "bytes": len(dump),
            "file": f"daily-firehose-postgres-{backup_id}.dump",
            "sha256": hashlib.sha256(dump).hexdigest(),
        },
        "backup_id": backup_id,
        "completed_at": common.format_utc(started + timedelta(minutes=5)),
        "database": {
            "archive_format": "pg_dump-custom",
            "compression": 9,
            "ownership_included": False,
            "privileges_included": False,
        },
        "recovery_point_at": common.format_utc(started),
        "schema_version": 3,
        "source": {
            "compose_path": "/home/ubuntu/daily-firehose",
            "host": "daily-firehose",
        },
        "started_at": common.format_utc(started),
        "storage": {
            "offsite": "not_verified_by_this_script",
            "transport": "ssh_push",
        },
        "validation": {
            "dump_manifest_entries": 2,
            "local_pg_restore_list": True,
            "source_archive_fsynced": True,
        },
    }


@final
class BackupAndRestoreClientCases(unittest.TestCase):
    temporary_directory: tempfile.TemporaryDirectory[str]
    root: Path
    bin_dir: Path
    remote_dir: Path
    ssh_log: Path
    docker_log: Path
    environment: Any

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.remote_dir = self.root / "remote"
        self.remote_dir.mkdir()
        self.ssh_log = self.root / "ssh.log"
        self.docker_log = self.root / "docker.log"
        identity = self.root / "identity"
        known_hosts = self.root / "known_hosts"
        identity.write_text("fake private key", encoding="utf-8")
        known_hosts.write_text("192.168.1.2 ssh-ed25519 fake", encoding="utf-8")
        self._write_executable(
            "ssh",
            """
            import hashlib, os, pathlib, sys
            remote = pathlib.Path(os.environ["FAKE_REMOTE_DIR"])
            with pathlib.Path(os.environ["FAKE_SSH_LOG"]).open("a") as log:
                log.write(repr(sys.argv[1:]) + "\\n")
            command = sys.argv[-1].split(" ")
            if command == ["health"]:
                print("ok")
            elif command[:1] == ["put"] and len(command) == 5:
                _, backup_id, kind, size, checksum = command
                suffix = ".dump" if kind == "dump" else ".json"
                data = sys.stdin.buffer.read()
                if len(data) != int(size) or hashlib.sha256(data).hexdigest() != checksum:
                    raise SystemExit(4)
                path = remote / ("daily-firehose-postgres-" + backup_id + suffix)
                if path.exists():
                    raise SystemExit(5)
                path.write_bytes(data)
            elif command[:1] == ["read"] and len(command) == 3:
                _, backup_id, kind = command
                suffix = ".dump" if kind == "dump" else ".json"
                data = (remote / ("daily-firehose-postgres-" + backup_id + suffix)).read_bytes()
                if os.environ.get("FAKE_TRUNCATE_READ") == kind:
                    data = data[:-1]
                sys.stdout.buffer.write(data)
            else:
                raise SystemExit(9)
            """,
        )
        self._write_executable(
            "docker",
            """
            import os, pathlib, sys
            args = sys.argv[1:]
            with pathlib.Path(os.environ["FAKE_DOCKER_LOG"]).open("a") as log:
                log.write(repr(args) + "\\n")
            if args[:1] == ["compose"] and args[-2:] == ["config", "--services"]:
                print("db\\nweb\\nrefresh-feeds")
            elif args[:1] == ["compose"] and "pg_dump" in args[-1]:
                sys.stdout.buffer.write(b"PGDMP-fake-custom-archive")
            elif args[:1] == ["compose"] and args[-2:] == ["pg_restore", "--list"]:
                if not sys.stdin.buffer.read().startswith(b"PGDMP"):
                    raise SystemExit(4)
                print("; header\\n1; TABLE feeds_feed\\n2; TABLE DATA feeds_feed")
            elif args[:1] == ["exec"] and "pg_isready" in args:
                pass
            elif args[:1] == ["exec"] and "pg_restore" in args:
                data = sys.stdin.buffer.read()
                if os.environ.get("FAKE_RESTORE_FAIL") or not data.startswith(b"PGDMP"):
                    raise SystemExit(5)
            elif args[:1] == ["exec"] and "psql" in args:
                print("1|1|1|1|1")
            elif args[:2] == ["run", "--rm"]:
                pass
            elif args[:2] == ["run", "--detach"]:
                print("temporary-container")
            elif args[:2] in (["network", "create"], ["network", "rm"], ["volume", "create"], ["volume", "rm"]):
                if os.environ.get("FAKE_CLEANUP_FAIL") and args[:2] == ["volume", "rm"]:
                    raise SystemExit(7)
            elif args[:2] == ["rm", "--force"]:
                pass
            else:
                raise SystemExit("unexpected docker args: " + repr(args))
            """,
        )
        self.environment = mock.patch.dict(
            os.environ,
            {
                "BACKUP_SSH_IDENTITY_FILE": str(identity),
                "BACKUP_SSH_KNOWN_HOSTS_FILE": str(known_hosts),
                "FAKE_DOCKER_LOG": str(self.docker_log),
                "FAKE_REMOTE_DIR": str(self.remote_dir),
                "FAKE_SSH_LOG": str(self.ssh_log),
                "PATH": f"{self.bin_dir}{os.pathsep}{os.environ['PATH']}",
                "SHOULD_NOT_BE_LOGGED_SECRET": "secret-sentinel",
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

    def _write_remote_pair(
        self, backup_id: str = BACKUP_ID
    ) -> tuple[bytes, dict[str, Any]]:
        dump = b"PGDMP-fake-custom-archive"
        metadata = metadata_for(backup_id, dump)
        (self.remote_dir / f"daily-firehose-postgres-{backup_id}.dump").write_bytes(
            dump
        )
        (self.remote_dir / f"daily-firehose-postgres-{backup_id}.json").write_text(
            json.dumps(metadata), encoding="utf-8"
        )
        return dump, metadata

    def test_ssh_is_pinned_noninteractive_and_only_allows_protocol_commands(
        self,
    ) -> None:
        command = backup_ssh.ssh_command("read", BACKUP_ID, "dump")
        joined = " ".join(command)
        self.assertIn("BatchMode=yes", joined)
        self.assertIn("IdentitiesOnly=yes", joined)
        self.assertIn("StrictHostKeyChecking=yes", joined)
        self.assertIn("UserKnownHostsFile=", joined)
        self.assertEqual(command[-2], "daily-firehose-backup@192.168.1.2")
        self.assertEqual(command[-1], f"read {BACKUP_ID} dump")
        for arguments in (
            ("read", "../../etc/shadow", "dump"),
            ("retention",),
            ("retention", BACKUP_ID),
            ("delete", BACKUP_ID),
            ("put", BACKUP_ID, "dump", "-1", "0" * 64),
            ("put", BACKUP_ID, "dump", str(1024**3 + 1), "0" * 64),
            ("put", BACKUP_ID, "dump;id", "1", "0" * 64),
        ):
            with (
                self.subTest(arguments=arguments),
                self.assertRaises(common.OperatorError),
            ):
                backup_ssh.ssh_command(*arguments)

    def test_local_compose_allowlist_is_exact(self) -> None:
        command = common.production_compose_command("config", "--services")
        self.assertEqual(
            command[2:6],
            [
                "--project-directory",
                "/home/ubuntu/daily-firehose",
                "-f",
                "/home/ubuntu/daily-firehose/docker-compose.yml",
            ],
        )
        with self.assertRaisesRegex(common.OperatorError, "unsupported"):
            common.production_compose_command("exec", "db", "env")

    def test_backup_uses_anonymous_dump_validates_locally_and_pushes_complete_pair(
        self,
    ) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(postgres_backup.main(), 0)
        dumps = list(self.remote_dir.glob("*.dump"))
        metadata_files = list(self.remote_dir.glob("*.json"))
        self.assertEqual((len(dumps), len(metadata_files)), (1, 1))
        metadata = json.loads(metadata_files[0].read_text())
        self.assertEqual(
            metadata["artifact"]["sha256"],
            hashlib.sha256(dumps[0].read_bytes()).hexdigest(),
        )
        self.assertEqual(
            metadata["storage"],
            {"transport": "ssh_push", "offsite": "not_verified_by_this_script"},
        )
        self.assertEqual(metadata["recovery_point_at"], metadata["started_at"])
        self.assertEqual(
            metadata["source"]["compose_path"], "/home/ubuntu/daily-firehose"
        )
        self.assertNotIn("secret-sentinel", stdout.getvalue())
        ssh_lines = self.ssh_log.read_text().splitlines()
        self.assertEqual(
            len(ssh_lines), 3
        )  # health, dump, metadata; no delete operation
        self.assertTrue(all("StrictHostKeyChecking=yes" in line for line in ssh_lines))
        docker = self.docker_log.read_text()
        self.assertIn("'config', '--services'", docker)
        self.assertIn("'pg_restore', '--list'", docker)

    def test_restore_fetches_exact_pair_to_anonymous_temp_and_cleans_exact_resources(
        self,
    ) -> None:
        self._write_remote_pair()
        evidence_dir = self.root / "evidence"
        argv = [
            "restore",
            "--backup-id",
            BACKUP_ID,
            "--evidence-dir",
            str(evidence_dir),
            "--app-image",
            "fake-app-image",
        ]
        with mock.patch.object(sys, "argv", argv):
            self.assertEqual(restore.main(), 0)
        evidence = json.loads(next(evidence_dir.glob("*.json")).read_text())
        self.assertEqual(evidence["result"], "passed")
        self.assertTrue(all(evidence["checks"].values()))
        cleanup = evidence["cleanup"]
        self.assertTrue(cleanup["only_exact_run_labeled_resources_targeted"])
        self.assertTrue(cleanup["label"].startswith("com.daily-firehose.restore.run="))
        self.assertEqual(
            {value["cleanup_status"] for value in cleanup["resources"].values()},
            {"removed"},
        )
        self.assertIn(
            "daily-firehose-restore-db-", cleanup["resources"]["container"]["name"]
        )
        self.assertIn(
            "daily-firehose-restore-net-", cleanup["resources"]["network"]["name"]
        )
        self.assertIn(
            "daily-firehose-restore-data-", cleanup["resources"]["volume"]["name"]
        )
        docker = self.docker_log.read_text()
        self.assertGreaterEqual(docker.count(cleanup["label"]), 3)
        self.assertIn("'rm', '--force', 'daily-firehose-restore-db-", docker)
        self.assertIn("'volume', 'rm', 'daily-firehose-restore-data-", docker)
        self.assertIn("'network', 'rm', 'daily-firehose-restore-net-", docker)
        self.assertNotIn("compose', 'down", docker)
        self.assertNotIn("postgres-data", docker)

    def test_restore_transfer_metadata_hash_and_pg_restore_failures_are_evidenced(
        self,
    ) -> None:
        _dump, metadata = self._write_remote_pair()
        cases = [
            ("metadata", "metadata_complete"),
            ("dump", "transfer_complete"),
            ("pg_restore", "pg_restore"),
        ]
        for case, failed_check in cases:
            with self.subTest(case=case):
                evidence_dir = self.root / f"evidence-{case}"
                if case == "metadata":
                    path = self.remote_dir / f"daily-firehose-postgres-{BACKUP_ID}.json"
                    broken = dict(metadata)
                    broken["storage"] = {"transport": "wrong"}
                    path.write_text(json.dumps(broken))
                elif case == "dump":
                    os.environ["FAKE_TRUNCATE_READ"] = "dump"
                else:
                    os.environ["FAKE_RESTORE_FAIL"] = "1"
                argv = [
                    "restore",
                    "--backup-id",
                    BACKUP_ID,
                    "--evidence-dir",
                    str(evidence_dir),
                    "--app-image",
                    "fake-app-image",
                ]
                with (
                    mock.patch.object(sys, "argv", argv),
                    self.assertRaises(common.OperatorError),
                ):
                    restore.main()
                evidence = json.loads(next(evidence_dir.glob("*.json")).read_text())
                self.assertEqual(evidence["result"], "failed")
                self.assertFalse(evidence["checks"][failed_check])
                self.assertNotIn("secret-sentinel", json.dumps(evidence))
                os.environ.pop("FAKE_TRUNCATE_READ", None)
                os.environ.pop("FAKE_RESTORE_FAIL", None)
                (
                    self.remote_dir / f"daily-firehose-postgres-{BACKUP_ID}.json"
                ).write_text(json.dumps(metadata))

    def test_restore_cleanup_failure_records_exact_resource_and_label(self) -> None:
        self._write_remote_pair()
        evidence_dir = self.root / "evidence-cleanup"
        argv = [
            "restore",
            "--backup-id",
            BACKUP_ID,
            "--evidence-dir",
            str(evidence_dir),
            "--app-image",
            "fake-app-image",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.dict(os.environ, {"FAKE_CLEANUP_FAIL": "1"}),
            self.assertRaisesRegex(common.OperatorError, "cleanup failed"),
        ):
            restore.main()
        evidence = json.loads(next(evidence_dir.glob("*.json")).read_text())
        self.assertEqual(evidence["result"], "failed")
        self.assertFalse(evidence["cleanup"]["succeeded"])
        self.assertEqual(
            evidence["cleanup"]["resources"]["volume"]["cleanup_status"],
            "failed",
        )
        self.assertTrue(
            evidence["cleanup"]["label"].startswith("com.daily-firehose.restore.run=")
        )

    def test_restore_cleanup_continues_after_environment_and_oserror_failures(
        self,
    ) -> None:
        self._write_remote_pair()
        evidence_dir = self.root / "evidence-cleanup-oserror"
        argv = [
            "restore",
            "--backup-id",
            BACKUP_ID,
            "--evidence-dir",
            str(evidence_dir),
            "--app-image",
            "fake-app-image",
        ]
        real_run = restore.run

        def fail_volume_cleanup(command: list[str], **kwargs: Any) -> Any:
            if command[:3] == ["docker", "volume", "rm"]:
                raise OSError("bounded simulated cleanup failure")
            return real_run(command, **kwargs)

        def unlink_then_fail(path: Path) -> None:
            path.unlink(missing_ok=True)
            raise OSError("bounded simulated unlink failure")

        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(restore, "run", side_effect=fail_volume_cleanup),
            mock.patch.object(
                restore, "_remove_environment_file", side_effect=unlink_then_fail
            ),
            self.assertRaisesRegex(common.OperatorError, "cleanup failed"),
        ):
            restore.main()
        evidence = json.loads(next(evidence_dir.glob("*.json")).read_text())
        self.assertEqual(evidence["cleanup"]["environment_file_status"], "failed")
        self.assertEqual(
            evidence["cleanup"]["resources"]["volume"]["cleanup_status"],
            "failed",
        )
        self.assertEqual(
            evidence["cleanup"]["resources"]["network"]["cleanup_status"],
            "removed",
        )
        self.assertEqual(
            evidence["cleanup"]["resources"]["container"]["cleanup_status"],
            "removed",
        )
        self.assertIn(
            "['network', 'rm', 'daily-firehose-restore-net-",
            self.docker_log.read_text(),
        )

    def test_manifest_count_rejects_bool_and_evidence_publish_is_race_safe(
        self,
    ) -> None:
        metadata = metadata_for(BACKUP_ID, b"PGDMP")
        metadata["validation"]["dump_manifest_entries"] = True
        with self.assertRaisesRegex(common.OperatorError, "complete verified"):
            common.validate_backup_metadata(metadata, BACKUP_ID)

        evidence = self.root / "same-evidence.json"
        barrier = threading.Barrier(2)
        outcomes: list[str] = []

        def publish(value: int) -> None:
            barrier.wait(timeout=2)
            try:
                common.write_json_atomic(evidence, {"writer": value})
            except common.OperatorError:
                outcomes.append("refused")
            else:
                outcomes.append("published")

        threads = [
            threading.Thread(target=publish, args=(index,)) for index in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
        self.assertCountEqual(outcomes, ["published", "refused"])
        self.assertIn(json.loads(evidence.read_text())["writer"], {0, 1})
        self.assertFalse(list(self.root.glob(".same-evidence.json.*.part")))

    def test_restore_rejects_backup_id_traversal_before_ssh(self) -> None:
        argv = [
            "restore",
            "--backup-id",
            "../../etc/passwd",
            "--evidence-dir",
            str(self.root / "e"),
        ]
        with mock.patch.object(sys, "argv", argv), self.assertRaises(SystemExit):
            restore.parse_arguments()
        self.assertFalse(self.ssh_log.exists())


@final
class ReceiverCases(unittest.TestCase):
    temporary_directory: tempfile.TemporaryDirectory[str]
    root: Path
    directory_fd: int

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.root.chmod(0o700)
        self.mountinfo = self.root.parent / f"{self.root.name}-mountinfo"
        device = receiver._device_number(self.root)
        self.mountinfo.write_text(
            f"24 23 {device} / {self.root} rw - zfs {receiver.ZFS_DATASET_SOURCE} rw\n",
            encoding="utf-8",
        )
        self.directory_patch = mock.patch.object(receiver, "DATA_DIRECTORY", self.root)
        self.mountinfo_patch = mock.patch.object(
            receiver, "MOUNTINFO_PATH", self.mountinfo
        )
        self.directory_patch.start()
        self.mountinfo_patch.start()
        self.directory_fd = receiver._open_data_directory()

    def tearDown(self) -> None:
        os.close(self.directory_fd)
        self.mountinfo_patch.stop()
        self.directory_patch.stop()
        self.mountinfo.unlink(missing_ok=True)
        self.temporary_directory.cleanup()

    def _put(
        self,
        backup_id: str,
        kind: str,
        value: bytes,
        *,
        size: int | None = None,
        checksum: str | None = None,
    ) -> None:
        receiver._put(
            self.directory_fd,
            backup_id,
            kind,
            len(value) if size is None else size,
            hashlib.sha256(value).hexdigest() if checksum is None else checksum,
            io.BytesIO(value),
        )

    def _pair(
        self, backup_id: str = BACKUP_ID, *, recovery_point: datetime | None = None
    ) -> tuple[Path, Path]:
        dump = b"PGDMP-receiver-archive"
        self._put(backup_id, "dump", dump)
        metadata = (
            json.dumps(
                metadata_for(backup_id, dump, recovery_point=recovery_point),
                sort_keys=True,
            )
            + "\n"
        ).encode()
        self._put(backup_id, "metadata", metadata)
        return (
            self.root / f"daily-firehose-postgres-{backup_id}.dump",
            self.root / f"daily-firehose-postgres-{backup_id}.json",
        )

    def test_atomic_put_validates_pair_fsyncs_and_refuses_overwrite(self) -> None:
        dump_path, metadata_path = self._pair()
        self.assertEqual(stat.S_IMODE(dump_path.stat().st_mode), 0o600)
        self.assertTrue(metadata_path.is_file())
        self.assertEqual(
            receiver._validated_pair(self.directory_fd, BACKUP_ID).backup_id, BACKUP_ID
        )
        with self.assertRaisesRegex(common.OperatorError, "overwrite"):
            self._put(BACKUP_ID, "dump", b"replacement")
        self.assertEqual(dump_path.read_bytes(), b"PGDMP-receiver-archive")

    def test_partial_checksum_trailing_and_invalid_metadata_fail_without_temp_residue(
        self,
    ) -> None:
        failures = [
            (b"short", 9, hashlib.sha256(b"short").hexdigest()),
            (b"trailing", 3, hashlib.sha256(b"tra").hexdigest()),
            (b"bad-hash", 8, "0" * 64),
        ]
        for index, (value, size, checksum) in enumerate(failures):
            backup_id = f"20260812T00000{index}Z-abcdef1{index}"
            with self.subTest(index=index), self.assertRaises(common.OperatorError):
                self._put(backup_id, "dump", value, size=size, checksum=checksum)
            self.assertFalse(list(self.root.glob("*.part")))
            self.assertFalse(list(self.root.glob(f"*{backup_id}*")))

        dump = b"PGDMP"
        self._put(BACKUP_ID, "dump", dump)
        wrong = metadata_for(BACKUP_ID, b"other")
        value = json.dumps(wrong).encode()
        with self.assertRaisesRegex(common.OperatorError, "does not match"):
            self._put(BACKUP_ID, "metadata", value)
        self.assertFalse(
            (self.root / f"daily-firehose-postgres-{BACKUP_ID}.json").exists()
        )
        self.assertFalse(list(self.root.glob("*.part")))

        fsync_id = "20260812T000003Z-abcdef13"
        real_fsync = receiver.os.fsync

        def fail_directory_fsync(descriptor: int) -> None:
            if descriptor == self.directory_fd:
                raise OSError("simulated directory fsync failure")
            real_fsync(descriptor)

        with (
            mock.patch.object(receiver.os, "fsync", side_effect=fail_directory_fsync),
            self.assertRaises(OSError),
        ):
            self._put(fsync_id, "dump", b"PGDMP-fsync-failure")
        self.assertFalse(list(self.root.glob(f"*{fsync_id}*")))
        self.assertFalse(list(self.root.glob("*.part")))

    def test_traversal_shell_tokens_and_arbitrary_delete_commands_are_refused(
        self,
    ) -> None:
        invalid = [
            "read ../../etc/shadow dump",
            f"read {BACKUP_ID} dump;id",
            f"put {BACKUP_ID} dump 1 {'0' * 64} trailing",
            f"put {BACKUP_ID} dump {'9' * 10000} {'0' * 64}",
            f"retention {BACKUP_ID}",
            f"delete {BACKUP_ID}",
            "health  ",
        ]
        for command in invalid:
            with self.subTest(command=command), self.assertRaises(common.OperatorError):
                receiver._parse_original_command(command)

    def test_data_path_requires_exact_effective_zfs_dataset_mount(self) -> None:
        descriptor = receiver._open_data_directory()
        self.assertIsInstance(descriptor, int)
        os.close(descriptor)
        device = receiver._device_number(self.root)
        cases = {
            "ordinary directory on mounted parent": (
                f"24 23 {device} / {self.root.parent} rw - zfs "
                f"nas_general/homes/backups rw\n"
            ),
            "masked by non-zfs mount": (
                f"25 24 {device} / {self.root} rw - ext4 /dev/masked rw\n"
            ),
            "wrong zfs source": (
                f"26 24 {device} / {self.root} rw - zfs nas_general/wrong rw\n"
            ),
            "bind-mounted dataset subdirectory": (
                f"27 24 {device} /subdirectory {self.root} rw - zfs "
                f"{receiver.ZFS_DATASET_SOURCE} rw\n"
            ),
        }
        original = self.mountinfo.read_text(encoding="utf-8")
        for name, content in cases.items():
            with self.subTest(case=name):
                self.mountinfo.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(
                    common.OperatorError, "exact effective ZFS"
                ):
                    receiver._open_data_directory()
        self.mountinfo.write_text(original, encoding="utf-8")

    def test_symlink_and_dataset_path_escape_defenses(self) -> None:
        dump_name = f"daily-firehose-postgres-{BACKUP_ID}.dump"
        (self.root / "outside").write_bytes(b"PGDMP")
        (self.root / dump_name).symlink_to(self.root / "outside")
        with self.assertRaises(OSError):
            receiver._hash_regular(self.directory_fd, dump_name, 5)
        link = self.root.parent / f"{self.root.name}-link"
        link.symlink_to(self.root, target_is_directory=True)
        with (
            mock.patch.object(receiver, "DATA_DIRECTORY", link),
            self.assertRaisesRegex(common.OperatorError, "symlink or alias"),
        ):
            receiver._open_data_directory()
        link.unlink()

    def test_retention_fixed_policy_preserves_newest_and_cannot_choose_target(
        self,
    ) -> None:
        now = datetime(2026, 8, 12, tzinfo=UTC)
        pairs = [
            receiver.StoredPair("newest", now - timedelta(days=500), "a", "b", "r1", 3),
            receiver.StoredPair("middle", now - timedelta(days=600), "c", "d", "r2", 2),
            receiver.StoredPair("oldest", now - timedelta(days=700), "e", "f", "r3", 1),
        ]
        kept, deleted = receiver.retention_partition(pairs, now)
        self.assertEqual([pair.backup_id for pair in kept], ["newest"])
        self.assertEqual({pair.backup_id for pair in deleted}, {"middle", "oldest"})
        with self.assertRaises(common.OperatorError):
            receiver._parse_original_command("retention")
        with self.assertRaises(common.OperatorError):
            receiver._parse_original_command("retention oldest")

    def test_later_forged_receipts_cannot_preempt_genuine_bucket_points(self) -> None:
        now = datetime(2026, 8, 12, 23, tzinfo=UTC)

        def pair(name: str, received: datetime, sequence: int) -> receiver.StoredPair:
            return receiver.StoredPair(name, received, "d", "m", "r", sequence)

        genuine_month = pair("genuine-month", datetime(2026, 4, 1, tzinfo=UTC), 1)
        forged_month = pair("forged-month", datetime(2026, 4, 20, tzinfo=UTC), 2)
        genuine_week = pair("genuine-week", datetime(2026, 7, 1, tzinfo=UTC), 3)
        forged_week = pair("forged-week", datetime(2026, 7, 2, tzinfo=UTC), 4)
        genuine_day = pair("genuine-day", datetime(2026, 8, 2, 1, tzinfo=UTC), 5)
        forged_day = pair("forged-day", datetime(2026, 8, 2, 20, tzinfo=UTC), 6)
        newest = pair("newest-lkg", now - timedelta(hours=1), 7)
        kept, deleted = receiver.retention_partition(
            [
                forged_day,
                forged_week,
                forged_month,
                genuine_day,
                genuine_week,
                genuine_month,
                newest,
            ],
            now,
        )
        kept_ids = {candidate.backup_id for candidate in kept}
        deleted_ids = {candidate.backup_id for candidate in deleted}
        self.assertTrue(
            {"genuine-day", "genuine-week", "genuine-month", "newest-lkg"} <= kept_ids
        )
        self.assertTrue({"forged-day", "forged-week", "forged-month"} <= deleted_ids)

    def test_retention_boundaries_and_projection_are_56_to_60_pairs(self) -> None:
        now = datetime(2026, 8, 12, tzinfo=UTC)
        points = [
            receiver.StoredPair(
                str(index),
                now - timedelta(hours=12 * index),
                "d",
                "m",
                "r",
                1000 - index,
            )
            for index in range(801)
        ]
        kept, _ = receiver.retention_partition(points, now)
        self.assertGreaterEqual(len(kept), 56)
        self.assertLessEqual(len(kept), 60)
        only = receiver.StoredPair("only", now - timedelta(days=900), "d", "m", "r", 1)
        self.assertEqual(receiver.retention_partition([only], now), ([only], []))

    def test_invalid_pairs_are_never_retention_delete_candidates(self) -> None:
        self._pair()
        invalid_id = "20250812T000000Z-deadbeef"
        bad_dump = self.root / f"daily-firehose-postgres-{invalid_id}.dump"
        bad_dump.write_bytes(b"bad")
        bad_meta = metadata_for(
            invalid_id, b"different", recovery_point=datetime(2025, 8, 12, tzinfo=UTC)
        )
        (self.root / f"daily-firehose-postgres-{invalid_id}.json").write_text(
            json.dumps(bad_meta)
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            receiver._maintenance_retention(self.directory_fd)
        self.assertTrue(bad_dump.exists())
        self.assertIn("orphans_deleted=0", output.getvalue())

    def test_receipt_time_not_client_recovery_time_controls_retention(self) -> None:
        old_id = "20200101T000000Z-abcdef12"
        before = datetime.now(UTC)
        self._pair(
            old_id,
            recovery_point=datetime(2020, 1, 1, tzinfo=UTC),
        )
        pair = receiver._validated_pair(self.directory_fd, old_id)
        self.assertGreaterEqual(pair.retention_at, before.replace(microsecond=0))
        metadata = json.loads(
            (self.root / f"daily-firehose-postgres-{old_id}.json").read_text()
        )
        self.assertNotEqual(
            common.format_utc(pair.retention_at), metadata["recovery_point_at"]
        )

    def test_local_maintenance_cleans_stale_orphans_but_preserves_valid_newest(
        self,
    ) -> None:
        self._pair()
        orphan_id = "20260810T000000Z-deadbeef"
        orphan_dump = self.root / f"daily-firehose-postgres-{orphan_id}.dump"
        orphan_dump.write_bytes(b"orphan")
        partial = self.root / (
            f".daily-firehose-postgres-{orphan_id}.dump.1234.deadbeef.part"
        )
        partial.write_bytes(b"partial")
        old = datetime.now(UTC) - timedelta(days=3)
        os.utime(orphan_dump, (old.timestamp(), old.timestamp()))
        os.utime(partial, (old.timestamp(), old.timestamp()))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            receiver._maintenance_retention(self.directory_fd, now=datetime.now(UTC))
        self.assertFalse(orphan_dump.exists())
        self.assertFalse(partial.exists())
        self.assertTrue(
            (self.root / f"daily-firehose-postgres-{BACKUP_ID}.receipt.json").exists()
        )
        self.assertIn("orphans_deleted=2", output.getvalue())

    def test_one_gib_object_and_twenty_gib_physical_quota_allow_small_file_flood(
        self,
    ) -> None:
        self.assertEqual(common.MAX_DUMP_BYTES, 1024**3)
        self.assertEqual(receiver.DATASET_QUOTA_BYTES, 20 * 1024**3)
        with self.assertRaises(common.OperatorError):
            receiver._parse_original_command(
                f"put {BACKUP_ID} dump {1024**3 + 1} {'0' * 64}"
            )
        now = datetime(2026, 8, 12, tzinfo=UTC)
        flood = [
            receiver.StoredPair(str(index), now, "d", "m", "r", index + 1)
            for index in range(21)
        ]
        kept, deleted = receiver.retention_partition(flood, now)
        self.assertEqual(len(kept), 21)
        self.assertEqual(deleted, [])

        flood_ids = [f"20260812T0000{index:02d}Z-feedf00d" for index in range(21)]
        for backup_id in flood_ids:
            self._put(backup_id, "dump", b"bounded-flood")
        self.assertEqual(len(list(self.root.glob("*.dump"))), 21)
        future = datetime.now(UTC) + receiver.ORPHAN_SAFE_AGE + timedelta(seconds=1)
        with contextlib.redirect_stdout(io.StringIO()):
            receiver._maintenance_retention(self.directory_fd, now=future)
        self.assertFalse(list(self.root.glob("*.dump")))

    def test_receiver_advisory_lock_serializes_concurrent_operations(self) -> None:
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()

        def first() -> None:
            with receiver._receiver_lock(self.directory_fd):
                first_entered.set()
                release_first.wait(timeout=2)

        def second() -> None:
            first_entered.wait(timeout=2)
            with receiver._receiver_lock(self.directory_fd):
                second_entered.set()

        first_thread = threading.Thread(target=first)
        second_thread = threading.Thread(target=second)
        first_thread.start()
        second_thread.start()
        self.assertTrue(first_entered.wait(timeout=2))
        time.sleep(0.05)
        self.assertFalse(second_entered.is_set())
        release_first.set()
        first_thread.join(timeout=2)
        second_thread.join(timeout=2)
        self.assertTrue(second_entered.is_set())

    def test_maintenance_is_local_only_and_refuses_every_ssh_context(self) -> None:
        with (
            mock.patch.object(sys, "argv", ["receiver", "--maintenance-retention"]),
            mock.patch.dict(os.environ, {}, clear=True),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(receiver.main(), 0)
        self.assertIn("maintenance fixed-policy", output.getvalue())

        for variable in receiver._SSH_ENVIRONMENT:
            with (
                self.subTest(variable=variable),
                mock.patch.object(sys, "argv", ["receiver", "--maintenance-retention"]),
                mock.patch.dict(os.environ, {variable: "present"}, clear=True),
                self.assertRaisesRegex(common.OperatorError, "forbidden"),
            ):
                receiver.main()

    @unittest.skipUnless(
        os.environ.get("DAILY_FIREHOSE_RECEIVER_INTEGRATION") == "1",
        "guarded local receiver integration is opt-in",
    )
    def test_guarded_local_receiver_main_in_temporary_dataset(self) -> None:
        with (
            mock.patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "health"}),
            mock.patch.object(sys, "argv", ["receiver"]),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(receiver.main(), 0)
        self.assertIn("receiver ok", output.getvalue())


@final
class DeploymentArtifactCases(unittest.TestCase):
    def test_systemd_runs_on_canonical_host_path_with_credentials_and_stays_dormant(
        self,
    ) -> None:
        service = (
            REPOSITORY_ROOT / "deploy/systemd/daily-firehose-postgresql-backup.service"
        ).read_text()
        timer = (
            REPOSITORY_ROOT / "deploy/systemd/daily-firehose-postgresql-backup.timer"
        ).read_text()
        self.assertIn("WorkingDirectory=/home/ubuntu/daily-firehose", service)
        self.assertIn("LoadCredential=ssh-private-key:", service)
        self.assertIn("LoadCredential=ssh-known-hosts:", service)
        self.assertNotIn("EnvironmentFile", service)
        self.assertNotIn("/nas/", service)
        # A transient receiver outage must be retried inside the +2h completion
        # objective instead of forfeiting the cycle until the next activation.
        self.assertIn("Restart=on-failure", service)
        self.assertIn("RestartSec=10min", service)
        self.assertIn("StartLimitIntervalSec=4h", service)
        self.assertIn("StartLimitBurst=6", service)
        self.assertIn("TimeoutStartSec=30min", service)
        # Type=oneshot rejects these, and both would retry without any bound.
        self.assertNotIn("Restart=always", service)
        self.assertNotIn("Restart=on-success", service)
        self.assertIn("OnCalendar=*-*-* 00,12:00:00 UTC", timer)
        self.assertIn("Persistent=true", timer)
        self.assertNotIn("enable --now", service + timer)

    def test_authorized_key_is_restrict_plus_exact_root_owned_launcher(self) -> None:
        key = (REPOSITORY_ROOT / "deploy/truenas/authorized_keys.example").read_text()
        launcher = (
            REPOSITORY_ROOT / "deploy/truenas/daily-firehose-backup-receiver"
        ).read_text()
        monitor_launcher = (
            REPOSITORY_ROOT / "deploy/truenas/daily-firehose-backup-monitor"
        ).read_text()
        exact_launcher = (
            "/mnt/nas_general/homes/backups/daily-firehose-control/"
            "daily-firehose-backup-receiver"
        )
        self.assertIn(f'restrict,command="{exact_launcher}"', key)
        self.assertIn("/usr/bin/python3 -I -c", launcher)
        self.assertIn("/mnt/nas_general/homes/backups/daily-firehose-control", launcher)
        self.assertIn("PYTHON[A-Za-z0-9_]*", launcher)
        self.assertIn('unset "$variable"', launcher)
        self.assertIn("scripts.postgres_backup_monitor", monitor_launcher)
        self.assertIn("--check", monitor_launcher)
        self.assertNotIn("$@", monitor_launcher)
        self.assertNotIn("/usr/local", key + launcher + monitor_launcher)

    def test_runbook_pins_middleware_payloads_quota_local_maintenance_and_credentials(
        self,
    ) -> None:
        runbook = (
            REPOSITORY_ROOT / "docs/operations/postgresql-backups.md"
        ).read_text()
        self.assertIn('"name":"nas_general/homes/backups/daily-firehose"', runbook)
        self.assertIn('"quota":21474836480', runbook)
        self.assertIn('"exec":"OFF"', runbook)
        self.assertIn('"username":"daily-firehose-backup"', runbook)
        self.assertIn("filesystem.setperm", runbook)
        self.assertIn("--maintenance-retention", runbook)
        self.assertIn(
            "systemctl start daily-firehose-postgresql-backup.service", runbook
        )
        self.assertIn("--property=Result --property=ExecMainStatus", runbook)
        self.assertIn(
            "journalctl --unit=daily-firehose-postgresql-backup.service", runbook
        )
        self.assertIn("systemd-run", runbook)
        self.assertIn("LoadCredential=ssh-private-key:", runbook)
        self.assertIn("recovery-key health/read/restore drill", runbook)
        self.assertIn("11.345 seconds", runbook)
        self.assertIn("20260812T011026Z-4c03472d", runbook)
        self.assertIn("Two valid receipt-backed backups", runbook)
        self.assertIn("scheduled service failure", runbook)
        self.assertIn('"user":"root"', runbook)
        self.assertIn('"minute":"37"', runbook)
        self.assertIn("Fastmail", runbook)
        self.assertIn("installed and\nactive in production", runbook)
        self.assertIn("less than one hour after", runbook)
        self.assertIn("**14 hours**", runbook)
        self.assertIn("**20 hours**", runbook)
        self.assertIn("**24-hour**", runbook)
        self.assertNotIn("recovery-offline", runbook)
        self.assertNotIn("at most twenty", runbook)


if __name__ == "__main__":
    unittest.main()
