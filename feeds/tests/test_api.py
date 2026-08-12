from __future__ import annotations

import hmac
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from ..feed_fetch import FeedFetchError
from ..models import (
    ArticleReadState,
    BulkReadMarker,
    Feed,
    ReadScope,
    SavedArticle,
    UserPreference,
)
from ..services import RefreshResult
from .support.base import (
    StaticFilesTestCase,
    model_id,
)
from .support.builders import (
    build_api_token,
    build_article,
    build_feed,
    build_user,
)


@override_settings(LINKDING_TOKEN="")
class ApiTests(StaticFilesTestCase):
    def setUp(self) -> None:
        self.user = build_user(username="api-reader")
        self.token, self.key = build_api_token(user=self.user)
        self.feed = build_feed()
        self.article = build_article(
            feed=self.feed,
            title="Morning article",
            url="https://example.com/morning",
            guid="morning",
            published_at=timezone.now(),
        )

    def auth_headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.key}"}

    @patch(
        "feeds.api.discover_feed_metadata",
        side_effect=FeedFetchError(code="timeout", message="Feed request timed out."),
    )
    def test_feed_creation_returns_structured_fetch_error_without_writes(
        self, mock_discover
    ) -> None:
        feed_url = "https://feeds.example/blocked.xml"

        response = self.client.post(
            reverse("api-feeds"),
            data={"feed_url": feed_url},
            content_type="application/json",
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "timeout",
                    "message": "Feed request timed out.",
                }
            },
        )
        self.assertFalse(Feed.objects.filter(feed_url=feed_url).exists())
        mock_discover.assert_called_once_with(feed_url)

    def test_api_requires_token(self) -> None:
        response = self.client.get(reverse("api-morning-briefing"))

        self.assertEqual(response.status_code, 401)

    def test_morning_briefing_lists_actionable_articles(self) -> None:
        response = self.client.get(
            reverse("api-morning-briefing"), headers=self.auth_headers()
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["date"], timezone.localdate().isoformat())
        self.assertEqual(payload["articles"][0]["title"], "Morning article")
        self.assertFalse(payload["articles"][0]["is_read"])
        self.assertFalse(payload["articles"][0]["is_saved"])

    def test_api_can_mark_article_read(self) -> None:
        response = self.client.post(
            reverse("api-article-read", args=[model_id(self.article)]),
            data={"is_read": True},
            content_type="application/json",
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            ArticleReadState.objects.get(user=self.user, article=self.article).is_read
        )
        self.assertTrue(response.json()["article"]["is_read"])

    def test_api_can_save_article(self) -> None:
        response = self.client.post(
            reverse("api-article-saved", args=[model_id(self.article)]),
            data={"is_saved": True, "notes": "Brief me on this."},
            content_type="application/json",
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        saved = SavedArticle.objects.get(user=self.user, article=self.article)
        self.assertEqual(saved.notes, "Brief me on this.")
        self.assertTrue(response.json()["article"]["is_saved"])

    @patch("feeds.api.refresh_active_feeds")
    def test_refresh_api_reports_partial_failures_and_backoff(
        self, mock_refresh_active_feeds
    ) -> None:
        failed_feed = build_feed(title="Failed API feed")
        skipped_feed = build_feed(title="Backoff API feed")
        superseded_feed = build_feed(title="Superseded API feed")
        next_retry = timezone.now()
        mock_refresh_active_feeds.return_value = [
            RefreshResult(feed=self.feed, created=2, duration_seconds=0.25),
            RefreshResult(
                feed=failed_feed,
                success=False,
                duration_seconds=0.5,
                error_code="timeout",
                error_message="Feed request timed out.",
                next_retry_at=next_retry,
            ),
            RefreshResult(
                feed=skipped_feed,
                success=False,
                skipped=True,
                error_code="timeout",
                error_message="Feed request timed out.",
                next_retry_at=next_retry,
            ),
            RefreshResult(
                feed=superseded_feed,
                success=False,
                superseded=True,
                error_code="superseded",
                error_message="A newer refresh owns status.",
            ),
        ]

        response = self.client.post(reverse("api-refresh"), headers=self.auth_headers())

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            {
                key: payload[key]
                for key in (
                    "checked",
                    "attempted",
                    "succeeded",
                    "failed",
                    "skipped",
                    "superseded",
                )
            },
            {
                "checked": 4,
                "attempted": 3,
                "succeeded": 1,
                "failed": 1,
                "skipped": 1,
                "superseded": 1,
            },
        )
        self.assertEqual(
            [feed["status"] for feed in payload["feeds"]],
            ["succeeded", "failed", "skipped", "superseded"],
        )
        self.assertEqual(
            payload["feeds"][1]["error"],
            {"code": "timeout", "message": "Feed request timed out."},
        )
        self.assertIsNone(payload["feeds"][0]["error"])

        superseded_feeds = [
            build_feed(title="Older API attempt"),
            build_feed(title="Oldest API attempt"),
        ]
        mock_refresh_active_feeds.return_value = [
            RefreshResult(
                feed=feed,
                success=False,
                superseded=True,
                error_code="superseded",
                error_message="A newer refresh owns status.",
            )
            for feed in superseded_feeds
        ]

        response = self.client.post(reverse("api-refresh"), headers=self.auth_headers())

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["checked"], 2)
        self.assertEqual(payload["attempted"], 2)
        self.assertEqual(payload["failed"], 0)
        self.assertEqual(payload["superseded"], 2)
        self.assertEqual(
            [feed["status"] for feed in payload["feeds"]],
            ["superseded", "superseded"],
        )

    def test_api_can_update_focus_mode_preference(self) -> None:
        response = self.client.patch(
            reverse("api-preferences"),
            data={"theme": "dracula", "compact": True, "focus_mode": True},
            content_type="application/json",
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["preferences"]
        self.assertEqual(
            payload,
            {"theme": "dracula", "compact": True, "focus_mode": True},
        )
        preferences = UserPreference.objects.get(user=self.user)
        self.assertEqual(preferences.theme, "dracula")
        self.assertTrue(preferences.compact)
        self.assertTrue(preferences.focus_mode)

    @override_settings(
        AGENT_LINK_SECRET="test-secret",
        AGENT_LINK_USERNAME="api-reader",
    )
    def test_signed_save_and_go_link_saves_and_redirects(self) -> None:
        article_id = model_id(self.article)
        signature = hmac.new(
            b"test-secret",
            f"save-and-go:{article_id}".encode(),
            "sha256",
        ).hexdigest()

        response = self.client.get(
            reverse("api-article-save-and-go", args=[article_id]),
            {"sig": signature},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], self.article.url)
        self.assertTrue(
            SavedArticle.objects.filter(user=self.user, article=self.article).exists()
        )

    @override_settings(
        AGENT_LINK_SECRET="test-secret",
        AGENT_LINK_USERNAME="api-reader",
    )
    def test_signed_mark_period_read_link_marks_today_read(self) -> None:
        signature = hmac.new(
            b"test-secret",
            b"mark-period-read:day",
            "sha256",
        ).hexdigest()

        response = self.client.get(
            reverse("api-mark-period-read-and-go"),
            {"scope": "day", "sig": signature},
        )

        today = timezone.localdate()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], reverse("today"))
        self.assertTrue(
            BulkReadMarker.objects.filter(
                user=self.user,
                scope=ReadScope.DAY,
                feed=None,
                period_start=today,
                period_end=today,
            ).exists()
        )
