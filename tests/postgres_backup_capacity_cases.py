"""Explicit PostgreSQL backup capacity cases (outside Django discovery)."""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts import postgres_backup_capacity as capacity
from scripts import postgres_backup_receiver as receiver


class CapacityModelCases(unittest.TestCase):
    def test_current_and_ten_x_regression_envelopes(self) -> None:
        current = capacity.build_model()
        self.assertEqual(current.point_bytes, 15_175_127)
        self.assertAlmostEqual(current.live_min_bytes / capacity.MIB, 810.44, places=2)
        self.assertAlmostEqual(current.live_max_bytes / capacity.MIB, 868.33, places=2)
        self.assertLessEqual(current.snapshot_delta_max_bytes / capacity.MIB, 405.22)
        self.assertAlmostEqual(current.local_min_bytes / capacity.GIB, 1.19, places=2)
        self.assertAlmostEqual(current.local_max_bytes / capacity.GIB, 1.24, places=2)
        self.assertAlmostEqual(current.data_quota_percent_max, 6.22, places=2)

        ten_x = capacity.build_model(10)
        self.assertAlmostEqual(ten_x.live_min_bytes / capacity.GIB, 7.91, places=2)
        self.assertAlmostEqual(ten_x.live_max_bytes / capacity.GIB, 8.48, places=2)
        self.assertAlmostEqual(
            ten_x.snapshot_delta_max_bytes / capacity.GIB, 3.96, places=2
        )
        self.assertAlmostEqual(ten_x.local_min_bytes / capacity.GIB, 11.87, places=2)
        self.assertAlmostEqual(ten_x.local_max_bytes / capacity.GIB, 12.44, places=2)
        self.assertAlmostEqual(ten_x.data_quota_percent_max, 62.18, places=2)

    def test_calendar_retention_reuses_receiver_policy_and_stays_56_to_60(self) -> None:
        now = datetime(2026, 8, 12, tzinfo=UTC)
        points = [
            receiver.StoredPair(
                str(index),
                now - timedelta(hours=12 * index),
                "dump",
                "metadata",
                "receipt",
                1000 - index,
            )
            for index in range(801)
        ]
        kept, _deleted = receiver.retention_partition(points, now)
        self.assertGreaterEqual(len(kept), capacity.RETAINED_POINTS_MIN)
        self.assertLessEqual(len(kept), capacity.RETAINED_POINTS_MAX)
        model = capacity.build_model()
        self.assertEqual(
            model.live_min_bytes,
            capacity.RETAINED_POINTS_MIN * model.point_bytes,
        )
        self.assertEqual(
            model.live_max_bytes,
            capacity.RETAINED_POINTS_MAX * model.point_bytes,
        )

    def test_snapshot_delta_does_not_duplicate_unchanged_hourly_snapshots(self) -> None:
        model = capacity.build_model()
        deleted_points = capacity.BACKUPS_PER_DAY * capacity.LOCAL_SNAPSHOT_PIN_DAYS
        self.assertEqual(deleted_points, 28)
        self.assertEqual(
            model.snapshot_delta_max_bytes,
            deleted_points * model.point_bytes,
        )
        naive_duplicate = (
            model.live_max_bytes
            * capacity.LOCAL_SNAPSHOT_PIN_DAYS
            * capacity.LOCAL_SNAPSHOTS_PER_DAY
        )
        self.assertLess(model.snapshot_delta_max_bytes, naive_duplicate)

    def test_threshold_boundaries_and_checker_exit_status(self) -> None:
        expected = {
            59.99: "ok",
            60.0: "planning-warning",
            79.99: "planning-warning",
            80.0: "warning",
            94.99: "warning",
            95.0: "critical",
        }
        for percent, prefix in expected.items():
            with self.subTest(percent=percent):
                self.assertTrue(capacity.threshold_status(percent).startswith(prefix))

        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(capacity.main([]), 0)
            self.assertEqual(capacity.main(["--scale", "10", "--fail-at", "60"]), 1)
            self.assertEqual(capacity.main(["--scale", "13", "--fail-at", "80"]), 1)
            self.assertEqual(capacity.main(["--scale", "16", "--fail-at", "95"]), 1)

    def test_optional_replication_is_labeled_as_planning_not_attestation(self) -> None:
        report = capacity.render(capacity.build_model(), replicated_copies=1)
        self.assertIn("Replicated-copy planning envelope", report)
        self.assertIn("planning-only, not attested remote usage", report)
        self.assertIn("Control quota: 1 GiB", report)
        self.assertIn("control usage and percentage are unmeasured", report)

    def test_runbook_preserves_evidence_and_bounds_remote_capacity_claims(self) -> None:
        runbook = (
            REPOSITORY_ROOT / "docs/operations/postgresql-backups.md"
        ).read_text()
        self.assertIn("15,173,740 bytes", runbook)
        self.assertIn("11.345 seconds", runbook)
        self.assertIn("approximately **14.6 MiB**", runbook)
        self.assertIn("**810.44–868.33 MiB**", runbook)
        self.assertIn("**11.87–12.44 GiB**", runbook)
        self.assertIn("Replication to `thought.feoh.org` is configured", runbook)
        self.assertIn("**Cannot yet be asserted.**", runbook)
        self.assertIn("**20 GiB data quota is binding**", runbook)

    def test_cli_is_executable_and_rejects_mutated_inputs(self) -> None:
        script = REPOSITORY_ROOT / "scripts/postgres_backup_capacity.py"
        self.assertTrue(script.stat().st_mode & 0o111)
        completed = subprocess.run(
            [str(script), "--scale", "10", "--replicated-copies", "1"],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("7.91-8.48 GiB", completed.stdout)
        self.assertIn("11.87-12.44 GiB", completed.stdout)

        mutations = (
            ("--scale", "0"),
            ("--scale", "01"),
            ("--scale", "-1"),
            ("--scale", "9" * 10_000),
            ("--replicated-copies", "01"),
            ("--replicated-copies", "-1"),
            ("--fail-at", "70"),
        )
        for arguments in mutations:
            with self.subTest(arguments=arguments):
                rejected = subprocess.run(
                    [str(script), *arguments],
                    cwd=REPOSITORY_ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(rejected.returncode, 2)
                self.assertIn("error:", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
