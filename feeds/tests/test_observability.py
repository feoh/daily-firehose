from __future__ import annotations

import json
import logging
import sys
from typing import Any, cast

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from daily_firehose.observability import (
    CORRELATION_ID_HEADER,
    JsonLogFormatter,
    get_correlation_id,
    sanitize_correlation_id,
)

from .support.base import StaticFilesTestCase
from .support.builders import build_user

SECRET_KEY = "production-secret-key-value-that-is-long-enough-to-redact"
LINKDING_TOKEN = "linkding-token-abcdef123456"
POSTMARK_SECRET = "postmark-inbound-secret-abcdef"


def _record(**extra: Any) -> logging.LogRecord:
    record = logging.LogRecord(
        name="feeds.services",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=extra.pop("message", "feed_refresh_completed"),
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


@override_settings(
    SECRET_KEY=SECRET_KEY,
    LINKDING_TOKEN=LINKDING_TOKEN,
    POSTMARK_INBOUND_SECRET=POSTMARK_SECRET,
)
class JsonLogFormatterTests(SimpleTestCase):
    def test_record_is_one_json_object_with_context_and_correlation_id(self) -> None:
        formatted = JsonLogFormatter().format(
            _record(feed_id=7, status="succeeded", correlation_id="abc123")
        )

        payload = json.loads(formatted)
        self.assertNotIn("\n", formatted)
        self.assertEqual(payload["event"], "feed_refresh_completed")
        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(payload["logger"], "feeds.services")
        self.assertEqual(payload["feed_id"], 7)
        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(payload["correlation_id"], "abc123")
        self.assertTrue(payload["timestamp"].endswith("+00:00"))

    def test_sensitive_field_names_are_dropped_from_context(self) -> None:
        payload = json.loads(
            JsonLogFormatter().format(
                _record(
                    linkding_token="value",
                    api_key="value",
                    authorization="Bearer value",
                    password="value",
                    inbound_secret="value",
                    feed_id=3,
                )
            )
        )

        self.assertEqual(payload["feed_id"], 3)
        for dropped in (
            "linkding_token",
            "api_key",
            "authorization",
            "password",
            "inbound_secret",
        ):
            self.assertNotIn(dropped, payload)

    def test_configured_secret_values_are_scrubbed_from_message_and_context(
        self,
    ) -> None:
        formatted = JsonLogFormatter().format(
            _record(
                message=f"fetch_failed url=https://linkding/?token={LINKDING_TOKEN}",
                webhook_path=f"/api/postmark/inbound/{POSTMARK_SECRET}/",
                signing_material=SECRET_KEY,
            )
        )

        self.assertNotIn(LINKDING_TOKEN, formatted)
        self.assertNotIn(POSTMARK_SECRET, formatted)
        self.assertNotIn(SECRET_KEY, formatted)
        self.assertIn("[redacted]", formatted)

    def test_unserializable_context_never_breaks_a_record(self) -> None:
        payload = json.loads(JsonLogFormatter().format(_record(feed=object())))

        self.assertIn("feed", payload)
        self.assertIsInstance(payload["feed"], str)

    def test_exception_records_carry_a_traceback_field(self) -> None:
        try:
            raise ValueError("boom")
        except ValueError:
            record = _record(message="refresh_cycle_failed")
            record.exc_info = sys.exc_info()

        payload = json.loads(JsonLogFormatter().format(record))

        self.assertIn("ValueError: boom", payload["traceback"])


class CorrelationIdSanitizationTests(SimpleTestCase):
    def test_only_bounded_safe_characters_survive(self) -> None:
        self.assertEqual(sanitize_correlation_id(" abc-123_x.y "), "abc-123_x.y")
        self.assertEqual(sanitize_correlation_id("abc def"), "abc")
        self.assertEqual(sanitize_correlation_id("\n\"injected\""), "")
        self.assertEqual(sanitize_correlation_id(""), "")
        self.assertEqual(len(sanitize_correlation_id("a" * 200)), 64)

    def test_no_correlation_id_is_bound_outside_a_request(self) -> None:
        self.assertEqual(get_correlation_id(), "")


class RequestCorrelationTests(StaticFilesTestCase):
    def setUp(self) -> None:
        self.user = build_user()
        self.client.force_login(self.user)

    def test_response_echoes_a_generated_correlation_id(self) -> None:
        response = self.client.get(reverse("today"))

        correlation_id = response.headers[CORRELATION_ID_HEADER]
        self.assertEqual(len(correlation_id), 32)

    def test_supplied_correlation_id_is_sanitized_and_reused(self) -> None:
        response = self.client.get(
            reverse("today"), headers={"x-correlation-id": "trace-7 injected value"}
        )

        self.assertEqual(response.headers[CORRELATION_ID_HEADER], "trace-7")

    def test_completion_record_names_the_view_and_omits_the_raw_path(self) -> None:
        with self.assertLogs("daily_firehose.request", level="INFO") as logs:
            self.client.get(reverse("feed-detail", args=[4]))

        record = cast(Any, logs.records[0])
        self.assertEqual(record.getMessage(), "http_request_completed")
        self.assertEqual(record.route, "feed-detail")
        self.assertEqual(record.method, "GET")
        self.assertEqual(record.status_code, 404)
        self.assertTrue(record.authenticated)
        self.assertNotIn("4", str(getattr(record, "path", "")))


@override_settings(POSTMARK_INBOUND_SECRET=POSTMARK_SECRET)
class WebhookLoggingTests(TestCase):
    def test_webhook_record_names_the_route_without_its_path_secret(self) -> None:
        with self.assertLogs("daily_firehose.request", level="INFO") as logs:
            self.client.post(
                reverse("postmark-inbound", args=[POSTMARK_SECRET]),
                data="{}",
                content_type="application/json",
            )

        record = cast(Any, logs.records[0])
        self.assertEqual(record.route, "postmark-inbound")
        formatted = JsonLogFormatter().format(record)
        self.assertNotIn(POSTMARK_SECRET, formatted)
