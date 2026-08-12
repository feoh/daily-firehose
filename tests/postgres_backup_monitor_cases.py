"""Explicit TrueNAS PostgreSQL backup monitor cases (outside Django discovery)."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self
from unittest import mock
from unittest.mock import _patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts import postgres_backup_monitor as monitor
from scripts import postgres_backup_receiver as receiver
from scripts.postgres_backup_common import OperatorError, format_utc
from tests.postgres_backup_script_cases import BACKUP_ID, metadata_for


class MonitorCases(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.data = self.root / "data"
        self.control = self.root / "control"
        self.data.mkdir(mode=0o700)
        self.control.mkdir(mode=0o700)
        self.mountinfo = self.root / "mountinfo"
        device = receiver._device_number(self.data)
        self.mountinfo.write_text(
            f"24 23 {device} / {self.data} rw - zfs {receiver.ZFS_DATASET_SOURCE} rw\n"
            f"25 23 {device} / {self.control} rw - zfs {monitor.CONTROL_ZFS_DATASET_SOURCE} rw\n",
            encoding="utf-8",
        )
        self.patches: tuple[_patch[Any], ...] = (
            mock.patch.object(receiver, "DATA_DIRECTORY", self.data),
            mock.patch.object(receiver, "MOUNTINFO_PATH", self.mountinfo),
            mock.patch.object(monitor, "CONTROL_DIRECTORY", self.control),
            mock.patch.object(monitor.os, "geteuid", return_value=0),
        )
        for patcher in self.patches:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temporary_directory.cleanup()

    def _pair(self, received_at: datetime) -> None:
        dump = b"PGDMP-monitor-archive"
        metadata = (
            json.dumps(metadata_for(BACKUP_ID, dump), sort_keys=True) + "\n"
        ).encode()
        descriptor = receiver._open_data_directory()
        try:
            receiver._put(
                descriptor,
                BACKUP_ID,
                "dump",
                len(dump),
                hashlib.sha256(dump).hexdigest(),
                io.BytesIO(dump),
            )
            receiver._put(
                descriptor,
                BACKUP_ID,
                "metadata",
                len(metadata),
                hashlib.sha256(metadata).hexdigest(),
                io.BytesIO(metadata),
            )
        finally:
            os.close(descriptor)
        receipt_path = self.data / f"daily-firehose-postgres-{BACKUP_ID}.receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["received_at"] = format_utc(received_at)
        receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n")

    def _state(self) -> dict[str, Any]:
        return json.loads((self.control / monitor.STATE_NAME).read_text())

    def test_threshold_boundaries(self) -> None:
        cases = {
            None: "containment",
            timedelta(hours=13, minutes=59, seconds=59): "ok",
            timedelta(hours=14): "missed",
            timedelta(hours=19, minutes=59, seconds=59): "missed",
            timedelta(hours=20): "critical",
            timedelta(hours=23, minutes=59, seconds=59): "critical",
            timedelta(hours=24): "containment",
        }
        for age, expected in cases.items():
            with self.subTest(age=age):
                self.assertEqual(monitor.classify_receipt_age(age), expected)

    def test_no_receipt_is_containment_and_notifies(self) -> None:
        notices: list[tuple[str, str]] = []
        result = monitor.check(
            now=datetime(2026, 8, 12, tzinfo=UTC),
            notifier=lambda subject, text: notices.append((subject, text)),
        )
        self.assertEqual(result, monitor.CheckResult("containment", None, 0))
        self.assertEqual(len(notices), 1)
        self.assertIn("CONTAINMENT", notices[0][0])
        self.assertNotIn("dump", notices[0][1].lower())

    def test_transition_suppression_reminder_and_recovery(self) -> None:
        now = datetime(2026, 8, 12, 12, tzinfo=UTC)
        self._pair(now - timedelta(hours=14))
        notices: list[tuple[str, str]] = []
        notifier = lambda subject, text: notices.append((subject, text))

        self.assertEqual(monitor.check(now=now, notifier=notifier).severity, "missed")
        self.assertEqual(len(notices), 1)
        self.assertEqual(
            monitor.check(now=now + timedelta(hours=23), notifier=notifier).severity,
            "containment",
        )
        self.assertEqual(len(notices), 2)  # severity transition
        monitor.check(now=now + timedelta(hours=23, minutes=59), notifier=notifier)
        self.assertEqual(len(notices), 2)
        monitor.check(now=now + timedelta(hours=47), notifier=notifier)
        self.assertEqual(len(notices), 3)  # unresolved 24h reminder

        receipt = self.data / f"daily-firehose-postgres-{BACKUP_ID}.receipt.json"
        value = json.loads(receipt.read_text())
        value["received_at"] = format_utc(now + timedelta(hours=47))
        receipt.write_text(json.dumps(value) + "\n")
        self.assertEqual(
            monitor.check(now=now + timedelta(hours=47), notifier=notifier).severity,
            "ok",
        )
        self.assertEqual(len(notices), 4)
        self.assertIn("recovered", notices[-1][0])

    def test_same_severity_is_suppressed_until_exact_24h_reminder(self) -> None:
        now = datetime(2026, 8, 12, 12, tzinfo=UTC)
        self._pair(now - timedelta(hours=20))
        notices: list[tuple[str, str]] = []
        notifier = lambda subject, text: notices.append((subject, text))
        monitor.check(now=now, notifier=notifier)
        # Keep the classification critical while exercising reminder timing.
        receipt = self.data / f"daily-firehose-postgres-{BACKUP_ID}.receipt.json"
        value = json.loads(receipt.read_text())
        value["received_at"] = format_utc(now + timedelta(hours=3, minutes=59))
        receipt.write_text(json.dumps(value) + "\n")
        monitor.check(now=now + timedelta(hours=23, minutes=59), notifier=notifier)
        self.assertEqual(len(notices), 1)
        value["received_at"] = format_utc(now + timedelta(hours=4))
        receipt.write_text(json.dumps(value) + "\n")
        monitor.check(now=now + timedelta(hours=24), notifier=notifier)
        self.assertEqual(len(notices), 2)

    def test_send_failure_does_not_advance_notification_state(self) -> None:
        now = datetime(2026, 8, 12, tzinfo=UTC)
        self._pair(now - timedelta(hours=14))

        def fail(_subject: str, _text: str) -> None:
            raise OperatorError("simulated send failure")

        with self.assertRaisesRegex(OperatorError, "send failure"):
            monitor.check(now=now, notifier=fail)
        self.assertFalse((self.control / monitor.STATE_NAME).exists())

        notices: list[tuple[str, str]] = []
        monitor.check(now=now, notifier=lambda s, t: notices.append((s, t)))
        self.assertEqual(len(notices), 1)
        self.assertEqual(self._state()["severity"], "missed")

    def test_data_descriptor_closes_when_control_open_fails(self) -> None:
        real_close = monitor.os.close
        closed: list[int] = []

        def recording_close(descriptor: int) -> None:
            closed.append(descriptor)
            real_close(descriptor)

        with (
            mock.patch.object(
                monitor, "_open_control_directory", side_effect=OperatorError("closed")
            ),
            mock.patch.object(monitor.os, "close", side_effect=recording_close),
            self.assertRaisesRegex(OperatorError, "closed"),
        ):
            monitor.check(now=datetime(2026, 8, 12, tzinfo=UTC))
        self.assertEqual(len(closed), 1)

    def test_lock_and_exact_path_defenses(self) -> None:
        monitor.check(
            now=datetime(2026, 8, 12, tzinfo=UTC), notifier=lambda _s, _t: None
        )
        state = self.control / monitor.STATE_NAME
        self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o600)
        self.assertTrue((self.data / receiver._LOCK_NAME).exists())
        self.assertTrue((self.control / receiver._LOCK_NAME).exists())

        original = self.mountinfo.read_text()
        self.mountinfo.write_text(
            original.replace(monitor.CONTROL_ZFS_DATASET_SOURCE, "nas_general/wrong")
        )
        with self.assertRaisesRegex(OperatorError, "exact effective ZFS"):
            monitor._open_control_directory()
        self.mountinfo.write_text(original)

        link = self.root / "control-link"
        link.symlink_to(self.control, target_is_directory=True)
        with (
            mock.patch.object(monitor, "CONTROL_DIRECTORY", link),
            self.assertRaisesRegex(OperatorError, "symlink or alias"),
        ):
            monitor._open_control_directory()

    def _notify_with_fake_client(
        self, mail_result: Any, job_results: list[Any] | None = None
    ) -> list[tuple[str, tuple[Any, ...]]]:
        calls: list[tuple[str, tuple[Any, ...]]] = []
        jobs = iter(job_results or [])

        class FakeClient:
            def __enter__(self) -> Self:
                return self

            def __exit__(self, *_arguments: object) -> None:
                return None

            def call(self, method: str, *arguments: Any) -> Any:
                calls.append((method, arguments))
                if method == "mail.send":
                    return mail_result
                return next(jobs)

        fake_module = type("FakeModule", (), {"Client": FakeClient})
        with mock.patch.dict(sys.modules, {"truenas_api_client": fake_module}):
            monitor.notify_truenas_administrators("bounded subject", "bounded text")
        return calls

    def test_truenas_notifier_accepts_only_sync_true_or_successful_job(self) -> None:
        synchronous = self._notify_with_fake_client(True)
        self.assertEqual(
            synchronous,
            [
                (
                    "mail.send",
                    (
                        {
                            "subject": "bounded subject",
                            "text": "bounded text",
                            "queue": False,
                        },
                    ),
                )
            ],
        )
        with mock.patch.object(monitor.time, "sleep") as sleep:
            asynchronous = self._notify_with_fake_client(
                42,
                [
                    {"id": 42, "state": "WAITING"},
                    {"id": 42, "state": "RUNNING"},
                    {"id": 42, "state": "SUCCESS"},
                ],
            )
        self.assertEqual(asynchronous[1][0], "core.get_jobs")
        self.assertEqual(sleep.call_count, 2)

    def test_truenas_notifier_rejects_ambiguous_returns_and_bad_jobs(self) -> None:
        ambiguous_results: tuple[Any, ...] = (None, False, 0, -1, "42", {}, [])
        for result in ambiguous_results:
            with (
                self.subTest(result=result),
                self.assertRaisesRegex(OperatorError, "invalid result"),
            ):
                self._notify_with_fake_client(result)
        job_cases: tuple[tuple[Any, str], ...] = (
            ({"id": 42, "state": "FAILED"}, "delivery job failed"),
            ({"id": 42, "state": "ABORTED"}, "delivery job failed"),
            ({"id": 42, "state": "UNKNOWN"}, "invalid status"),
            ({"id": 43, "state": "SUCCESS"}, "missing or invalid"),
            ({"state": "SUCCESS"}, "missing or invalid"),
            (None, "missing or invalid"),
            ([], "missing or invalid"),
        )
        for job, message in job_cases:
            with self.subTest(job=job), self.assertRaisesRegex(OperatorError, message):
                self._notify_with_fake_client(42, [job])

    def test_middleware_delivery_failure_does_not_advance_state(self) -> None:
        now = datetime(2026, 8, 12, tzinfo=UTC)
        self._pair(now - timedelta(hours=14))

        def ambiguous_delivery(_subject: str, _text: str) -> None:
            self._notify_with_fake_client(None)

        with self.assertRaisesRegex(OperatorError, "invalid result"):
            monitor.check(now=now, notifier=ambiguous_delivery)
        self.assertFalse((self.control / monitor.STATE_NAME).exists())

    def test_truenas_notifier_bounds_job_poll_timeout(self) -> None:
        pending = [
            {"id": 42, "state": "RUNNING"}
            for _attempt in range(monitor.JOB_POLL_ATTEMPTS)
        ]
        with (
            mock.patch.object(monitor.time, "sleep") as sleep,
            self.assertRaisesRegex(OperatorError, "timed out"),
        ):
            self._notify_with_fake_client(42, pending)
        self.assertEqual(sleep.call_count, monitor.JOB_POLL_ATTEMPTS - 1)

    def test_main_output_and_exit_status_cover_every_severity(self) -> None:
        cases = (
            (monitor.CheckResult("ok", timedelta(hours=13), 2), 0),
            (monitor.CheckResult("missed", timedelta(hours=14), 2), 1),
            (monitor.CheckResult("critical", timedelta(hours=20), 2), 1),
            (monitor.CheckResult("containment", timedelta(hours=24), 2), 1),
            (monitor.CheckResult("containment", None, 0), 1),
        )
        for result, expected_status in cases:
            with self.subTest(result=result):
                output = io.StringIO()
                with (
                    mock.patch.object(monitor, "check", return_value=result),
                    contextlib.redirect_stdout(output),
                ):
                    self.assertEqual(monitor.main(["--check"]), expected_status)
                rendered = output.getvalue()
                self.assertLessEqual(len(rendered), 128)
                self.assertIn(f"severity={result.severity}", rendered)
                self.assertNotIn(BACKUP_ID, rendered)

    def test_cli_and_notifications_are_bounded_and_have_no_arbitrary_inputs(
        self,
    ) -> None:
        with self.assertRaises(SystemExit):
            monitor.parse_arguments([])
        with self.assertRaises(SystemExit):
            monitor.parse_arguments(["--threshold", "1"])
        self.assertTrue(monitor.parse_arguments(["--check"]).check)
        for severity in ("missed", "critical", "containment"):
            subject, text = monitor._notification_message(
                monitor.CheckResult(severity, None, 0), recovery=False
            )
            self.assertLessEqual(len(subject), 80)
            self.assertLessEqual(len(text), 512)
            self.assertNotIn(BACKUP_ID, subject + text)
        self.assertTrue(
            (REPOSITORY_ROOT / "scripts/postgres_backup_monitor.py").stat().st_mode
            & 0o111
        )


if __name__ == "__main__":
    unittest.main()
