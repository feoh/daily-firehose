"""Every adapter writes read state through one validated, atomic command.

The session HTML controllers, the bearer API, and the signed links used to carry
their own copies of the same write. These tests hold the surfaces to a single
shared contract: the same validation, the same transaction boundary, and the
same resulting marker.
"""

from __future__ import annotations

import hmac
from datetime import timedelta
from unittest.mock import patch

from django.db import IntegrityError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from ..models import ArticleReadState, BulkReadMarker, ReadScope
from ..services import RefreshResult
from .support.base import StaticFilesTestCase, model_id
from .support.builders import build_api_token, build_article, build_feed, build_user


def marker_write_fails():
    return patch(
        "feeds.commands.BulkReadMarker.objects.update_or_create",
        side_effect=IntegrityError("forced marker failure"),
    )


class SessionBulkMarkAtomicityTests(StaticFilesTestCase):
    """READ-INV-006: materialized read state and its marker commit together."""

    def setUp(self) -> None:
        self.user = build_user(username="session-reader")
        self.feed = build_feed()
        self.article = build_article(
            feed=self.feed,
            title="Unread article",
            url="https://example.com/unread",
            guid="unread",
            published_at=timezone.now(),
        )
        self.client.force_login(self.user)

    def test_session_period_mark_rolls_back_read_states_when_the_marker_fails(
        self,
    ) -> None:
        today = timezone.localdate()

        with marker_write_fails(), self.assertRaises(IntegrityError):
            self.client.post(
                reverse("mark-period-read"),
                {
                    "scope": ReadScope.DAY,
                    "period_start": today.isoformat(),
                    "period_end": today.isoformat(),
                },
            )

        # Before the shared command the browser wrote these outside any
        # transaction, leaving articles read with no marker to explain it.
        self.assertFalse(ArticleReadState.objects.exists())
        self.assertFalse(BulkReadMarker.objects.exists())

    def test_session_feed_mark_rolls_back_read_states_when_the_marker_fails(
        self,
    ) -> None:
        with marker_write_fails(), self.assertRaises(IntegrityError):
            self.client.post(reverse("mark-feed-read", args=[model_id(self.feed)]))

        self.assertFalse(ArticleReadState.objects.exists())
        self.assertFalse(BulkReadMarker.objects.exists())

    def test_session_period_mark_rejects_unknown_scope_without_writing(self) -> None:
        today = timezone.localdate()

        response = self.client.post(
            reverse("mark-period-read"),
            {
                "scope": "century",
                "period_start": today.isoformat(),
                "period_end": today.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(ArticleReadState.objects.exists())
        self.assertFalse(BulkReadMarker.objects.exists())

    def test_session_period_mark_rejects_absent_and_malformed_dates_without_writing(
        self,
    ) -> None:
        today = timezone.localdate()
        cases = (
            {"scope": ReadScope.DAY},
            {"scope": ReadScope.DAY, "period_start": today.isoformat()},
            {
                "scope": ReadScope.DAY,
                "period_start": "not-a-date",
                "period_end": today.isoformat(),
            },
            {
                "scope": ReadScope.DAY,
                "period_start": today.isoformat(),
                "period_end": "2026-02-30",
            },
        )

        for payload in cases:
            with self.subTest(payload=payload):
                response = self.client.post(reverse("mark-period-read"), payload)

                # Missing and unparseable dates used to raise out of the view.
                self.assertEqual(response.status_code, 400)
        self.assertFalse(ArticleReadState.objects.exists())
        self.assertFalse(BulkReadMarker.objects.exists())

    def test_session_period_mark_rejects_reversed_dates_without_writing(self) -> None:
        today = timezone.localdate()

        response = self.client.post(
            reverse("mark-period-read"),
            {
                "scope": ReadScope.DAY,
                "period_start": today.isoformat(),
                "period_end": (today - timedelta(days=1)).isoformat(),
            },
        )

        # A shape the database check constraint rejects is now refused before
        # any write is attempted, not surfaced as a server error.
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ArticleReadState.objects.exists())
        self.assertFalse(BulkReadMarker.objects.exists())


@override_settings(
    LINKDING_TOKEN="",
    AGENT_LINK_SECRET="test-secret",
    AGENT_LINK_USERNAME="shared-reader",
)
class AdapterAgreementTests(StaticFilesTestCase):
    """The three surfaces are adapters over one command, not three writers."""

    def setUp(self) -> None:
        self.user = build_user(username="shared-reader")
        self.token, self.key = build_api_token(user=self.user)
        self.feed = build_feed()
        self.article = build_article(
            feed=self.feed,
            title="Shared article",
            url="https://example.com/shared",
            guid="shared",
            published_at=timezone.now(),
        )
        self.client.force_login(self.user)

    def auth_headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.key}"}

    def test_every_period_adapter_advances_one_shared_marker(self) -> None:
        today = timezone.localdate()
        signature = hmac.new(
            b"test-secret", b"mark-period-read:day", "sha256"
        ).hexdigest()

        self.client.post(
            reverse("mark-period-read"),
            {
                "scope": ReadScope.DAY,
                "period_start": today.isoformat(),
                "period_end": today.isoformat(),
            },
        )
        session_marker = BulkReadMarker.objects.get()
        self.client.post(
            reverse("api-mark-period-read"),
            {"scope": "day"},
            content_type="application/json",
            headers=self.auth_headers(),
        )
        self.client.get(
            reverse("api-mark-period-read-and-go"),
            {"scope": "day", "sig": signature},
        )

        marker = BulkReadMarker.objects.get()
        self.assertEqual(model_id(marker), model_id(session_marker))
        self.assertEqual(marker.scope, ReadScope.DAY)
        self.assertIsNone(marker.feed)
        self.assertEqual(marker.period_start, today)
        self.assertEqual(marker.period_end, today)
        self.assertTrue(
            ArticleReadState.objects.filter(
                user=self.user, article=self.article, is_read=True
            ).exists()
        )

    def test_every_feed_adapter_advances_one_shared_marker(self) -> None:
        self.client.post(reverse("mark-feed-read", args=[model_id(self.feed)]))
        session_marker = BulkReadMarker.objects.get()

        self.client.post(
            reverse("api-feed-mark-read", args=[model_id(self.feed)]),
            headers=self.auth_headers(),
        )

        marker = BulkReadMarker.objects.get()
        self.assertEqual(model_id(marker), model_id(session_marker))
        self.assertEqual(marker.scope, ReadScope.FEED)
        self.assertEqual(marker.feed, self.feed)
        self.assertIsNone(marker.period_start)
        self.assertIsNone(marker.period_end)

    def test_both_article_adapters_write_the_same_explicit_state(self) -> None:
        self.client.post(
            reverse("mark-article", args=[model_id(self.article)]), {"state": "read"}
        )
        self.assertTrue(ArticleReadState.objects.get().is_read)

        self.client.post(
            reverse("api-article-read", args=[model_id(self.article)]),
            {"is_read": False},
            content_type="application/json",
            headers=self.auth_headers(),
        )

        state = ArticleReadState.objects.get()
        self.assertFalse(state.is_read)
        self.assertEqual(state.article, self.article)
        self.assertEqual(state.user, self.user)


class RefreshTallyAgreementTests(StaticFilesTestCase):
    """Both refresh adapters report one tally computed in one place."""

    def setUp(self) -> None:
        self.user = build_user(username="refresh-reader")
        self.token, self.key = build_api_token(user=self.user)
        self.client.force_login(self.user)

    def _results(self) -> list[RefreshResult]:
        succeeded = build_feed(title="Succeeded", feed_url="https://a.example/feed")
        failed = build_feed(title="Failed", feed_url="https://b.example/feed")
        skipped = build_feed(title="Skipped", feed_url="https://c.example/feed")
        next_retry = timezone.now()
        return [
            RefreshResult(feed=succeeded, created=2, updated=1),
            RefreshResult(
                feed=failed,
                success=False,
                error_code="timeout",
                error_message="Feed request timed out.",
                next_retry_at=next_retry,
            ),
            RefreshResult(
                feed=skipped,
                success=False,
                skipped=True,
                error_code="timeout",
                error_message="Feed request timed out.",
                next_retry_at=next_retry,
            ),
        ]

    def test_browser_summary_and_api_payload_agree_on_every_aggregate(self) -> None:
        results = self._results()

        with patch("feeds.commands.refresh_active_feeds", return_value=results):
            browser = self.client.post(reverse("refresh-feeds"), follow=True)
            api = self.client.post(
                reverse("api-refresh"),
                headers={"authorization": f"Bearer {self.key}"},
            )

        payload = api.json()
        self.assertEqual(payload["checked"], 3)
        self.assertEqual(payload["attempted"], 2)
        self.assertEqual(payload["succeeded"], 1)
        self.assertEqual(payload["failed"], 1)
        self.assertEqual(payload["skipped"], 1)
        self.assertEqual(payload["feeds_with_new_articles"], 1)
        self.assertEqual(payload["created"], 2)
        self.assertEqual(payload["updated"], 1)
        summary = " ".join(str(message) for message in browser.context["messages"])
        self.assertIn("checked 3 feeds", summary)
        self.assertIn("attempted 2", summary)
        self.assertIn("succeeded 1", summary)
        self.assertIn("failed 1", summary)
        self.assertIn("skipped 1", summary)
        self.assertIn("1 feeds had new articles", summary)
        self.assertIn("2 new articles", summary)
        self.assertIn("1 existing articles updated", summary)
        self.assertIn("Failed feeds: Failed.", summary)
