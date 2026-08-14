from __future__ import annotations

import os
import signal
from collections.abc import Iterator
from datetime import timedelta
from io import StringIO
from typing import Any, cast
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from feeds.jobs import (
    REFRESH_JOB,
    JobAlreadyRunning,
    acquire_job,
    finish_job,
    heartbeat,
    job_health,
)
from feeds.models import Feed, JobRun
from feeds.services import RefreshResult

from .support.builders import build_feed

WORKER = "feeds.management.commands.run_refresh_worker"


def _results(feed: Feed, *statuses: str) -> list[RefreshResult]:
    retry_at = timezone.now() + timedelta(minutes=5)
    results = []
    for status in statuses:
        if status == "succeeded":
            results.append(RefreshResult(feed=feed, created=1))
        elif status == "superseded":
            results.append(
                RefreshResult(
                    feed=feed,
                    success=False,
                    superseded=True,
                    error_code="superseded",
                    error_message="A newer refresh attempt owns the status.",
                )
            )
        else:
            results.append(
                RefreshResult(
                    feed=feed,
                    success=False,
                    skipped=status == "skipped",
                    error_code="fetch_failed",
                    error_message="unreachable",
                    next_retry_at=retry_at,
                )
            )
    return results


class SteppingClock:
    """A monotonic clock that advances a fixed amount per reading."""

    def __init__(self, step: float) -> None:
        self.step = step
        self.reading = 0.0

    def monotonic(self) -> float:
        self.reading += self.step
        return self.reading


def _run_worker(results: list[RefreshResult], **options: Any) -> None:
    with patch(f"{WORKER}.iter_refresh_active_feeds", return_value=iter(results)):
        call_command("run_refresh_worker", once=True, stdout=StringIO(), **options)


class JobOwnershipTests(TestCase):
    def test_acquiring_records_a_running_owner_with_a_heartbeat(self) -> None:
        run = acquire_job(REFRESH_JOB, correlation_id="cycle-1", owner="host:1")

        self.assertEqual(run.status, JobRun.Status.RUNNING)
        self.assertEqual(run.owner, "host:1")
        self.assertEqual(run.correlation_id, "cycle-1")
        self.assertIsNone(run.finished_at)

    def test_second_worker_cannot_overlap_a_live_owner(self) -> None:
        acquire_job(REFRESH_JOB, correlation_id="cycle-1", owner="host:1")

        with self.assertRaises(JobAlreadyRunning):
            acquire_job(REFRESH_JOB, correlation_id="cycle-2", owner="host:2")

        self.assertEqual(
            JobRun.objects.filter(status=JobRun.Status.RUNNING).count(), 1
        )

    def test_finished_owner_releases_the_job(self) -> None:
        run = acquire_job(REFRESH_JOB, correlation_id="cycle-1")
        finish_job(run, status=JobRun.Status.SUCCEEDED, checked=3, succeeded=3)

        successor = acquire_job(REFRESH_JOB, correlation_id="cycle-2")

        self.assertEqual(successor.status, JobRun.Status.RUNNING)
        run.refresh_from_db()
        self.assertEqual(run.checked, 3)
        self.assertIsNotNone(run.finished_at)

    @override_settings(JOB_LEASE_SECONDS=60)
    def test_expired_lease_is_reclaimed_and_the_owner_marked_interrupted(self) -> None:
        stale = acquire_job(REFRESH_JOB, correlation_id="cycle-1", owner="dead:1")
        JobRun.objects.filter(pk=stale.pk).update(
            heartbeat_at=timezone.now() - timedelta(seconds=600)
        )

        successor = acquire_job(REFRESH_JOB, correlation_id="cycle-2", owner="live:2")

        stale.refresh_from_db()
        self.assertEqual(stale.status, JobRun.Status.INTERRUPTED)
        self.assertEqual(stale.error_code, "lease_expired")
        self.assertEqual(successor.owner, "live:2")
        self.assertEqual(
            JobRun.objects.filter(status=JobRun.Status.RUNNING).count(), 1
        )

    @override_settings(JOB_LEASE_SECONDS=60)
    def test_a_heartbeat_renews_the_lease_against_reclaim(self) -> None:
        run = acquire_job(REFRESH_JOB, correlation_id="cycle-1", owner="live:1")
        JobRun.objects.filter(pk=run.pk).update(
            heartbeat_at=timezone.now() - timedelta(seconds=600)
        )
        run.refresh_from_db()

        heartbeat(run)

        with self.assertRaises(JobAlreadyRunning):
            acquire_job(REFRESH_JOB, correlation_id="cycle-2", owner="other:2")

    def test_heartbeat_does_not_revive_a_finished_run(self) -> None:
        run = acquire_job(REFRESH_JOB, correlation_id="cycle-1")
        finish_job(run, status=JobRun.Status.SUCCEEDED)
        finished_at = JobRun.objects.get(pk=run.pk).heartbeat_at

        heartbeat(run)

        self.assertEqual(JobRun.objects.get(pk=run.pk).heartbeat_at, finished_at)

    def test_jobs_with_different_names_do_not_block_each_other(self) -> None:
        acquire_job(REFRESH_JOB, correlation_id="cycle-1")

        other = acquire_job("other_job", correlation_id="cycle-2")

        self.assertEqual(other.status, JobRun.Status.RUNNING)


class RefreshWorkerCycleTests(TestCase):
    def setUp(self) -> None:
        self.feed = build_feed()

    def test_clean_cycle_records_every_outcome_count_and_succeeds(self) -> None:
        _run_worker(
            _results(self.feed, "succeeded", "succeeded", "skipped", "superseded")
        )

        run = JobRun.objects.get()
        self.assertEqual(run.status, JobRun.Status.SUCCEEDED)
        self.assertEqual(run.checked, 4)
        self.assertEqual(run.attempted, 3)
        self.assertEqual(run.succeeded, 2)
        self.assertEqual(run.skipped, 1)
        self.assertEqual(run.superseded, 1)
        self.assertEqual(run.failed, 0)
        self.assertEqual(run.error_code, "")
        self.assertIsNotNone(run.finished_at)

    def test_feed_failures_are_recorded_without_stalling_the_worker(self) -> None:
        _run_worker(_results(self.feed, "succeeded", "failed"))

        run = JobRun.objects.get()
        self.assertEqual(run.status, JobRun.Status.SUCCEEDED)
        self.assertEqual(run.failed, 1)
        self.assertEqual(run.succeeded, 1)
        self.assertEqual(run.error_code, "")
        health = job_health()
        self.assertEqual(health.consecutive_failures, 0)
        self.assertFalse(health.stale)

    def test_every_feed_failing_still_counts_as_worker_progress(self) -> None:
        _run_worker(_results(self.feed, "failed", "failed", "failed"))

        run = JobRun.objects.get()
        self.assertEqual(run.status, JobRun.Status.SUCCEEDED)
        self.assertEqual(run.failed, 3)
        self.assertFalse(job_health().stale)

    def test_cycle_completion_is_logged_with_one_correlation_id(self) -> None:
        with self.assertLogs("daily_firehose.worker", level="INFO") as logs:
            _run_worker(_results(self.feed, "succeeded"))

        cycle_records: dict[str, Any] = {
            record.getMessage(): cast(Any, record)
            for record in logs.records
            if record.getMessage().startswith("refresh_cycle_")
        }
        self.assertEqual(
            sorted(cycle_records), ["refresh_cycle_completed", "refresh_cycle_started"]
        )
        self.assertEqual(
            {record.correlation_id for record in cycle_records.values()},
            {JobRun.objects.get().correlation_id},
        )
        self.assertEqual(cycle_records["refresh_cycle_completed"].succeeded, 1)

    def test_overlapping_cycle_is_skipped_without_a_second_run_row(self) -> None:
        acquire_job(REFRESH_JOB, correlation_id="held", owner="other:1")

        with self.assertLogs("daily_firehose.worker", level="WARNING") as logs:
            _run_worker(_results(self.feed, "succeeded"))

        skipped = cast(Any, logs.records[0])
        self.assertEqual(skipped.getMessage(), "refresh_cycle_skipped")
        self.assertEqual(skipped.reason, "overlap")
        self.assertEqual(JobRun.objects.count(), 1)
        self.assertEqual(JobRun.objects.get().correlation_id, "held")

    def test_unexpected_error_fails_the_cycle_and_releases_the_job(self) -> None:
        def explode() -> Iterator[RefreshResult]:
            yield RefreshResult(feed=self.feed, created=1)
            raise RuntimeError("database went away")

        with patch(f"{WORKER}.iter_refresh_active_feeds", side_effect=explode):
            with self.assertLogs("daily_firehose.worker", level="ERROR") as logs:
                with self.assertRaises(RuntimeError):
                    call_command("run_refresh_worker", once=True, stdout=StringIO())

        run = JobRun.objects.get()
        self.assertEqual(run.status, JobRun.Status.FAILED)
        self.assertEqual(run.error_code, "unexpected_error")
        self.assertEqual(run.succeeded, 1)
        self.assertIn("database went away", logs.output[0])
        self.assertEqual(
            acquire_job(REFRESH_JOB, correlation_id="next").status,
            JobRun.Status.RUNNING,
        )

    @override_settings(JOB_HEARTBEAT_SECONDS=60)
    def test_long_cycles_heartbeat_between_feeds(self) -> None:
        observed: list[Any] = []

        def record_heartbeat(run: JobRun) -> None:
            observed.append(run.pk)

        with patch(f"{WORKER}.time", SteppingClock(step=100)):
            with patch(f"{WORKER}.heartbeat", side_effect=record_heartbeat):
                _run_worker(_results(self.feed, "succeeded", "succeeded", "succeeded"))

        self.assertEqual(observed, [JobRun.objects.get().pk] * 3)

    @override_settings(JOB_HEARTBEAT_SECONDS=600)
    def test_short_cycles_do_not_heartbeat_on_every_feed(self) -> None:
        with patch(f"{WORKER}.time", SteppingClock(step=1)):
            with patch(f"{WORKER}.heartbeat") as beat:
                _run_worker(_results(self.feed, "succeeded", "succeeded"))

        beat.assert_not_called()


class GracefulStopTests(TestCase):
    def test_a_stop_signal_ends_the_cycle_between_feeds(self) -> None:
        feed = build_feed()

        def interrupted() -> Iterator[RefreshResult]:
            yield RefreshResult(feed=feed, created=1)
            os.kill(os.getpid(), signal.SIGTERM)
            yield RefreshResult(feed=feed, created=1)
            raise AssertionError("the worker kept refreshing after a stop signal")

        with patch(f"{WORKER}.iter_refresh_active_feeds", side_effect=interrupted):
            call_command("run_refresh_worker", stdout=StringIO())

        run = JobRun.objects.get()
        self.assertEqual(run.status, JobRun.Status.INTERRUPTED)
        self.assertEqual(run.error_code, "stopped")
        self.assertEqual(run.checked, 2)
        self.assertIsNotNone(run.finished_at)

    def test_a_clean_stop_does_not_count_as_a_consecutive_failure(self) -> None:
        feed = build_feed()

        def interrupted() -> Iterator[RefreshResult]:
            os.kill(os.getpid(), signal.SIGTERM)
            yield RefreshResult(feed=feed, created=1)

        with patch(f"{WORKER}.iter_refresh_active_feeds", side_effect=interrupted):
            call_command("run_refresh_worker", stdout=StringIO())

        self.assertEqual(job_health().consecutive_failures, 0)

    def test_default_signal_handling_is_restored_after_the_command(self) -> None:
        before = signal.getsignal(signal.SIGTERM)

        _run_worker([])

        self.assertIs(signal.getsignal(signal.SIGTERM), before)


class WorkerHealthCommandTests(TestCase):
    def test_never_run_worker_exits_nonzero(self) -> None:
        with self.assertRaises(CommandError):
            call_command("check_refresh_worker", stdout=StringIO())

    def test_recent_success_exits_zero_and_prints_status(self) -> None:
        output = StringIO()
        _run_worker(_results(build_feed(), "succeeded"))

        call_command("check_refresh_worker", stdout=output)

        self.assertIn('"status": "succeeded"', output.getvalue())
        self.assertIn('"stale": false', output.getvalue())

    @override_settings(JOB_MAX_SUCCESS_AGE_SECONDS=1)
    def test_worker_with_no_recent_success_exits_nonzero(self) -> None:
        _run_worker(_results(build_feed(), "succeeded"))
        JobRun.objects.update(finished_at=timezone.now() - timedelta(hours=3))

        with self.assertRaises(CommandError):
            call_command("check_refresh_worker", stdout=StringIO())
