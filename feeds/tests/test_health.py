from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, cast
from unittest.mock import patch

from django.db import OperationalError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from feeds.jobs import REFRESH_JOB
from feeds.models import Feed, JobRun

from .support.builders import build_api_token, build_feed, build_user


def _payload(response: Any) -> dict:
    return json.loads(response.content.decode("utf-8"))


class LivenessTests(TestCase):
    def test_liveness_succeeds_without_touching_the_database(self) -> None:
        with patch("feeds.health.connection.cursor") as cursor:
            response = self.client.get(reverse("health-live"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(_payload(response), {"status": "live"})
        cursor.assert_not_called()

    def test_liveness_is_anonymous_and_uncacheable(self) -> None:
        response = self.client.get(reverse("health-live"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_write_methods_are_rejected(self) -> None:
        response = self.client.post(reverse("health-live"))

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers["Allow"], "GET, HEAD")


class ReadinessTests(TestCase):
    def test_readiness_reports_database_and_migration_checks(self) -> None:
        response = self.client.get(reverse("health-ready"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            _payload(response),
            {
                "status": "ready",
                "checks": {"database": True, "migrations": True},
            },
        )

    def test_unreachable_database_is_not_ready_and_leaks_no_detail(self) -> None:
        with patch(
            "feeds.health.connection.cursor",
            side_effect=OperationalError("password authentication failed for user"),
        ):
            with self.assertLogs("daily_firehose.health", level="ERROR"):
                response = self.client.get(reverse("health-ready"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            _payload(response),
            {
                "status": "not_ready",
                "checks": {"database": False, "migrations": False},
            },
        )
        self.assertNotIn(b"password", response.content)

    def test_pending_migrations_are_not_ready(self) -> None:
        with patch(
            "feeds.health.MigrationExecutor.migration_plan",
            return_value=[("feeds", "0011_pending")],
        ):
            with self.assertLogs("daily_firehose.health", level="WARNING"):
                response = self.client.get(reverse("health-ready"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            _payload(response)["checks"], {"database": True, "migrations": False}
        )

    def test_write_methods_are_rejected(self) -> None:
        response = self.client.post(reverse("health-ready"))

        self.assertEqual(response.status_code, 405)


@override_settings(SECURE_SSL_REDIRECT=True)
class HealthTransportTests(TestCase):
    def test_probe_paths_answer_plain_http_while_pages_still_redirect(self) -> None:
        for name in ("health-live", "health-ready"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

        self.assertEqual(self.client.get(reverse("today")).status_code, 301)

    def test_operator_status_is_not_exempt_from_https(self) -> None:
        self.assertEqual(self.client.get(reverse("health-status")).status_code, 301)


class OperatorStatusTests(TestCase):
    def setUp(self) -> None:
        self.user = build_user()
        _, self.key = build_api_token(user=self.user)

    def _get(self) -> Any:
        return cast(Any, self.client.get(
            reverse("health-status"),
            headers={"authorization": f"Bearer {self.key}"},
        ))

    def test_status_requires_a_bearer_token(self) -> None:
        response = self.client.get(reverse("health-status"))

        self.assertEqual(response.status_code, 401)

    def test_never_run_worker_is_reported_stale(self) -> None:
        response = self._get()

        self.assertEqual(response.status_code, 503)
        worker = _payload(response)["worker"]
        self.assertEqual(worker["status"], "never_run")
        self.assertTrue(worker["stale"])
        self.assertIsNone(worker["last_success_at"])

    def test_recent_success_reports_healthy_worker_and_feed_counts(self) -> None:
        build_feed()
        build_feed(consecutive_failures=3, next_retry_at=timezone.now())
        build_feed(is_active=False)
        finished = timezone.now() - timedelta(minutes=5)
        JobRun.objects.create(
            name=REFRESH_JOB,
            status=JobRun.Status.SUCCEEDED,
            correlation_id="cycle-1",
            started_at=finished,
            heartbeat_at=finished,
            finished_at=finished,
            checked=2,
        )

        response = self._get()

        self.assertEqual(response.status_code, 200)
        payload = _payload(response)
        self.assertEqual(payload["worker"]["status"], "succeeded")
        self.assertFalse(payload["worker"]["stale"])
        self.assertEqual(payload["worker"]["consecutive_failures"], 0)
        self.assertEqual(
            payload["feeds"], {"active": 2, "failing": 1, "backing_off": 1}
        )

    def test_consecutive_failed_cycles_are_counted_and_reported_stale(self) -> None:
        base = timezone.now() - timedelta(hours=6)
        JobRun.objects.create(
            name=REFRESH_JOB,
            status=JobRun.Status.SUCCEEDED,
            started_at=base,
            heartbeat_at=base,
            finished_at=base,
        )
        for offset in (1, 2):
            moment = base + timedelta(hours=offset)
            JobRun.objects.create(
                name=REFRESH_JOB,
                status=JobRun.Status.FAILED,
                started_at=moment,
                heartbeat_at=moment,
                finished_at=moment,
                error_code="feed_failures",
            )

        response = self._get()

        self.assertEqual(response.status_code, 503)
        worker = _payload(response)["worker"]
        self.assertEqual(worker["consecutive_failures"], 2)
        self.assertTrue(worker["stale"])

    def test_stalled_running_cycle_without_heartbeats_is_stale(self) -> None:
        started = timezone.now() - timedelta(hours=2)
        JobRun.objects.create(
            name=REFRESH_JOB,
            status=JobRun.Status.RUNNING,
            started_at=started,
            heartbeat_at=started,
        )

        response = self._get()

        self.assertEqual(response.status_code, 503)
        worker = _payload(response)["worker"]
        self.assertEqual(worker["status"], "running")
        self.assertTrue(worker["stale"])
        self.assertGreater(worker["heartbeat_age_seconds"], 3600)

    def test_running_cycle_with_a_recent_heartbeat_is_healthy(self) -> None:
        JobRun.objects.create(
            name=REFRESH_JOB,
            status=JobRun.Status.RUNNING,
            started_at=timezone.now() - timedelta(minutes=10),
            heartbeat_at=timezone.now(),
        )

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(_payload(response)["worker"]["stale"])

    def test_status_exposes_no_feed_identity(self) -> None:
        build_feed(title="Private Internal Feed", feed_url="https://secret.example/x")

        body = self._get().content.decode("utf-8")

        self.assertNotIn("Private Internal Feed", body)
        self.assertNotIn("secret.example", body)
        self.assertEqual(Feed.objects.count(), 1)
