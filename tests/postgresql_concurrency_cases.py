"""Bounded-deadline regressions for the PostgreSQL concurrency harness."""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import final

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "daily_firehose.settings")

import django

django.setup()

from feeds.tests.support.concurrency import run_concurrently


@final
class PostgreSQLConcurrencyHarnessCases(unittest.TestCase):
    def test_timeout_returns_without_waiting_forever_or_leaking_worker(self) -> None:
        release = threading.Event()
        worker_started = threading.Event()

        def blocked_worker() -> bool:
            worker_started.set()
            return release.wait()

        started_at = time.monotonic()
        try:
            with self.assertRaisesRegex(TimeoutError, "concurrent workers exceeded"):
                run_concurrently([blocked_worker, worker_started.wait], timeout=0.05)
        finally:
            release.set()
        elapsed = time.monotonic() - started_at

        self.assertLess(elapsed, 1.0)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and any(
            thread.name.startswith("postgres-race") for thread in threading.enumerate()
        ):
            time.sleep(0.01)
        self.assertFalse(
            any(
                thread.name.startswith("postgres-race")
                for thread in threading.enumerate()
            )
        )


if __name__ == "__main__":
    unittest.main()
