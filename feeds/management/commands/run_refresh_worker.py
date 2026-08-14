"""Supervised feed-refresh worker with overlap locking and graceful stop."""

from __future__ import annotations

import logging
import signal
import threading
import time
from argparse import ArgumentParser
from collections.abc import Callable
from types import FrameType
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

from daily_firehose.observability import (
    bind_correlation_id,
    new_correlation_id,
    reset_correlation_id,
)
from feeds.jobs import (
    REFRESH_JOB,
    JobAlreadyRunning,
    acquire_job,
    heartbeat,
    finish_job,
)
from feeds.models import JobRun
from feeds.services import iter_refresh_active_feeds

logger = logging.getLogger("daily_firehose.worker")

_STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)


class Command(BaseCommand):
    help = "Run feed refresh cycles until stopped, with heartbeats and locking."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run a single cycle and exit instead of looping.",
        )
        parser.add_argument(
            "--interval",
            type=float,
            default=None,
            help="Seconds to wait between cycles. Defaults to FEED_REFRESH_SECONDS.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        stop = threading.Event()
        previous = {
            number: signal.signal(number, _stop_handler(stop))
            for number in _STOP_SIGNALS
        }
        interval = options["interval"]
        if interval is None:
            interval = float(settings.FEED_REFRESH_SECONDS)
        try:
            while True:
                self._cycle(stop)
                if options["once"] or stop.is_set():
                    break
                if stop.wait(interval):
                    break
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
        logger.info("refresh_worker_stopped")

    def _cycle(self, stop: threading.Event) -> None:
        correlation_id = new_correlation_id()
        token = bind_correlation_id(correlation_id)
        try:
            try:
                run = acquire_job(REFRESH_JOB, correlation_id=correlation_id)
            except JobAlreadyRunning:
                logger.warning("refresh_cycle_skipped", extra={"reason": "overlap"})
                return
            logger.info(
                "refresh_cycle_started", extra={"job_run_id": run.pk, "owner": run.owner}
            )
            started = time.monotonic()
            counts = dict.fromkeys(
                ("checked", "attempted", "succeeded", "failed", "skipped", "superseded"),
                0,
            )
            interrupted = False
            beat_every = float(settings.JOB_HEARTBEAT_SECONDS)
            last_beat = time.monotonic()
            try:
                for result in iter_refresh_active_feeds():
                    counts["checked"] += 1
                    if result.status != "skipped":
                        counts["attempted"] += 1
                    counts[result.status] += 1
                    if time.monotonic() - last_beat >= beat_every:
                        heartbeat(run)
                        last_beat = time.monotonic()
                    if stop.is_set():
                        interrupted = True
                        break
            except Exception:
                logger.exception("refresh_cycle_failed", extra={"job_run_id": run.pk})
                finish_job(
                    run,
                    status=JobRun.Status.FAILED,
                    error_code="unexpected_error",
                    error_message="The refresh cycle raised an unexpected error.",
                    **counts,
                )
                raise
            # A cycle that evaluated every Feed made progress even when some
            # Feeds failed: per-Feed failure is what backoff and the failing-feed
            # counts are for. Reserving FAILED for a cycle that could not finish
            # keeps worker staleness meaning "ingestion stopped".
            if interrupted:
                status = JobRun.Status.INTERRUPTED
                error_code, error_message = "stopped", "The worker was asked to stop."
            else:
                status = JobRun.Status.SUCCEEDED
                error_code, error_message = "", ""
            finish_job(
                run,
                status=status,
                error_code=error_code,
                error_message=error_message,
                **counts,
            )
            logger.info(
                "refresh_cycle_completed",
                extra={
                    "job_run_id": run.pk,
                    "status": status,
                    "duration_seconds": round(time.monotonic() - started, 6),
                    **counts,
                },
            )
        finally:
            reset_correlation_id(token)


def _stop_handler(stop: threading.Event) -> Callable[[int, FrameType | None], None]:
    def handler(signal_number: int, frame: FrameType | None) -> None:
        stop.set()

    return handler
