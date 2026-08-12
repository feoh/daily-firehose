from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import Any, cast
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from ..feed_fetch import FeedFetchError, FetchedFeedDocument
from ..models import Article, ArticleReadState, NewsletterIssue, SavedArticle
from ..services import (
    RefreshResult,
    _retry_delay,
    refresh_active_feeds,
    refresh_feed,
    safe_feed_title,
)
from .support.base import StaticFilesTestCase
from .support.builders import (
    build_feed,
    build_user,
)


class ArticleIdentityAuditTests(TestCase):
    def test_audit_passes_without_modifying_valid_articles(self) -> None:
        feed = build_feed()
        article = Article.objects.create(
            feed=feed,
            title="Audited",
            url="https://example.com/audited",
            guid="audited-guid",
        )
        output = StringIO()

        call_command("audit_article_identity", stdout=output)

        self.assertIn("Article identity audit passed.", output.getvalue())
        self.assertTrue(Article.objects.filter(pk=article.pk).exists())


class RefreshLoggingConfigurationTests(SimpleTestCase):
    def test_success_records_are_enabled_by_default(self) -> None:
        self.assertTrue(logging.getLogger("feeds.services").isEnabledFor(logging.INFO))


class RefreshResultTests(TestCase):
    def setUp(self) -> None:
        self.feed = build_feed()
        self.retry_at = timezone.now() + timedelta(minutes=5)

    def test_positional_success_compatibility(self) -> None:
        result = RefreshResult(self.feed, 2, 3)

        self.assertEqual(
            (result.feed, result.created, result.updated), (self.feed, 2, 3)
        )
        self.assertEqual(result.status, "succeeded")

    def test_rejects_contradictory_states(self) -> None:
        invalid_arguments: list[dict[str, Any]] = [
            {"success": True, "skipped": True},
            {"success": True, "error_code": "timeout"},
            {"success": False, "created": 1},
            {"success": False, "error_code": "timeout"},
            {
                "success": False,
                "error_code": "timeout",
                "error_message": "Feed request timed out.",
            },
            {
                "success": False,
                "skipped": True,
                "error_code": "timeout",
                "error_message": "Feed request timed out.",
            },
            {"superseded": True},
            {"superseded": True, "success": True},
            {"superseded": True, "skipped": True},
            {"superseded": True, "created": 1},
            {"created": -1},
            {"duration_seconds": float("inf")},
        ]

        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                RefreshResult(feed=self.feed, **arguments)

    def test_accepts_failed_and_skipped_states_with_complete_metadata(self) -> None:
        failed = RefreshResult(
            feed=self.feed,
            success=False,
            error_code="timeout",
            error_message="Feed request timed out.",
            next_retry_at=self.retry_at,
        )
        skipped = RefreshResult(
            feed=self.feed,
            success=False,
            skipped=True,
            error_code="timeout",
            error_message="Feed request timed out.",
            next_retry_at=self.retry_at,
        )
        superseded = RefreshResult(
            feed=self.feed,
            success=False,
            superseded=True,
            error_code="superseded",
            error_message="A newer refresh owns status.",
        )

        self.assertEqual(failed.status, "failed")
        self.assertEqual(skipped.status, "skipped")
        self.assertEqual(superseded.status, "superseded")


class RefreshBackoffTests(TestCase):
    def test_backoff_progression_and_saturation_are_deterministic(self) -> None:
        expected = {
            1: timedelta(minutes=5),
            2: timedelta(minutes=10),
            3: timedelta(minutes=20),
            4: timedelta(minutes=40),
            9: timedelta(hours=21, minutes=20),
            10: timedelta(hours=24),
            100: timedelta(hours=24),
        }

        for failure_count, delay in expected.items():
            with self.subTest(failure_count=failure_count):
                self.assertEqual(_retry_delay(failure_count), delay)


class FeedRefreshServiceTests(TestCase):
    @patch("feeds.services.feedparser.parse")
    @patch(
        "feeds.services.fetch_feed_document",
        return_value=FetchedFeedDocument(
            content=b"feed",
            final_url="https://example.com/feed.xml",
            response_headers={"content-location": "https://example.com/feed.xml"},
        ),
    )
    def test_refresh_feed_prefers_article_url_over_comments_guid(
        self, mock_fetch, mock_parse
    ) -> None:
        feed = build_feed(title="Lobsters", feed_url="https://lobste.rs/rss")
        mock_parse.return_value = {
            "feed": {"title": "Lobsters"},
            "entries": [
                {
                    "id": "https://lobste.rs/s/vkoa7r",
                    "link": "https://example.com/article",
                    "links": [
                        {
                            "rel": "alternate",
                            "type": "text/html",
                            "href": "https://example.com/article",
                        }
                    ],
                    "title": "An article",
                    "summary": '<p><a href="https://lobste.rs/s/vkoa7r/story">Comments</a></p>',
                }
            ],
        }

        refresh_feed(feed)

        article = Article.objects.get(feed=feed, title="An article")
        self.assertEqual(article.url, "https://example.com/article")
        self.assertEqual(article.guid, "https://lobste.rs/s/vkoa7r")

    @patch("feeds.services.feedparser.parse")
    @patch(
        "feeds.services.fetch_feed_document",
        return_value=FetchedFeedDocument(
            content=b"feed",
            final_url="https://example.com/feed.xml",
            response_headers={"content-location": "https://example.com/feed.xml"},
        ),
    )
    def test_changed_guid_reconciliation_preserves_first_seen_and_associations(
        self, mock_fetch, mock_parse
    ) -> None:
        feed = build_feed()
        user = build_user()
        article = Article.objects.create(
            feed=feed,
            title="Original title",
            url="https://example.com/stable",
            guid="old-guid",
        )
        first_seen = article.fetched_at
        read_state = ArticleReadState.objects.create(user=user, article=article)
        saved = SavedArticle.objects.create(
            user=user,
            article=article,
            url=article.url,
            title=article.title,
            feed=feed,
        )
        issue = NewsletterIssue.objects.create(
            article=article,
            message_id="preserved-newsletter-association",
            subject="Original title",
        )
        mock_parse.return_value = {
            "feed": {"title": feed.title},
            "entries": [
                {
                    "id": "new-guid",
                    "link": article.url,
                    "title": "Updated title",
                }
            ],
        }

        result = refresh_feed(feed)

        article.refresh_from_db()
        self.assertTrue(result.success)
        self.assertEqual((result.created, result.updated), (0, 1))
        self.assertEqual(article.guid, "new-guid")
        self.assertEqual(article.title, "Updated title")
        self.assertEqual(article.fetched_at, first_seen)
        self.assertEqual(read_state.article_id, article.pk)
        self.assertEqual(saved.article_id, article.pk)
        self.assertEqual(issue.article_id, article.pk)

    @patch("feeds.services.feedparser.parse")
    @patch(
        "feeds.services.fetch_feed_document",
        return_value=FetchedFeedDocument(
            content=b"feed",
            final_url="https://example.com/feed.xml",
            response_headers={"content-location": "https://example.com/feed.xml"},
        ),
    )
    def test_split_guid_and_url_evidence_fails_without_merging_articles(
        self, mock_fetch, mock_parse
    ) -> None:
        feed = build_feed()
        by_guid = Article.objects.create(
            feed=feed,
            title="GUID match",
            url="https://example.com/guid-url",
            guid="stable-guid",
        )
        by_url = Article.objects.create(
            feed=feed,
            title="URL match",
            url="https://example.com/stable-url",
            guid="other-guid",
        )
        mock_parse.return_value = {
            "feed": {"title": feed.title},
            "entries": [
                {
                    "id": by_guid.guid,
                    "link": by_url.url,
                    "title": "Ambiguous",
                }
            ],
        }

        result = refresh_feed(feed)

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "integrity_error")
        by_guid.refresh_from_db()
        by_url.refresh_from_db()
        self.assertEqual(by_guid.url, "https://example.com/guid-url")
        self.assertEqual(by_url.guid, "other-guid")
        self.assertEqual(Article.objects.filter(feed=feed).count(), 2)

    @patch("feeds.services.feedparser.parse")
    @patch(
        "feeds.services.fetch_feed_document",
        return_value=FetchedFeedDocument(
            content=b"feed",
            final_url="https://example.com/feed.xml",
            response_headers={"content-location": "https://example.com/feed.xml"},
        ),
    )
    def test_whole_document_identity_plan_rejects_order_dependent_collision(
        self, mock_fetch, mock_parse
    ) -> None:
        feed = build_feed(title="Original feed title")
        article_a = Article.objects.create(
            feed=feed,
            title="Article A",
            guid="guid-a",
            url="https://example.com/url-a",
        )
        article_b = Article.objects.create(
            feed=feed,
            title="Article B",
            guid="guid-b",
            url="https://example.com/url-b",
        )
        before = list(
            Article.objects.filter(feed=feed)
            .order_by("pk")
            .values_list("pk", "guid", "url", "title", "fetched_at")
        )
        mock_parse.return_value = {
            "feed": {"title": "Must roll back"},
            "entries": [
                {
                    "id": "guid-new",
                    "link": article_a.url,
                    "title": "Would update A",
                },
                {
                    "id": article_a.guid,
                    "link": article_b.url,
                    "title": "Split snapshot evidence",
                },
            ],
        }

        result = refresh_feed(feed)

        feed.refresh_from_db()
        after = list(
            Article.objects.filter(feed=feed)
            .order_by("pk")
            .values_list("pk", "guid", "url", "title", "fetched_at")
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "integrity_error")
        self.assertEqual(feed.title, "Original feed title")
        self.assertEqual(after, before)

    @patch("feeds.services.feedparser.parse")
    @patch(
        "feeds.services.fetch_feed_document",
        return_value=FetchedFeedDocument(
            content=b"feed",
            final_url="https://example.com/feed.xml",
            response_headers={"content-location": "https://example.com/feed.xml"},
        ),
    )
    def test_refresh_feed_prefers_alternate_original_url_over_intermediary_link(
        self, mock_fetch, mock_parse
    ) -> None:
        feed = build_feed(
            title="Example Aggregator", feed_url="https://example.com/rss"
        )
        mock_parse.return_value = {
            "feed": {"title": "Example Aggregator"},
            "entries": [
                {
                    "id": "https://original.example.com/article",
                    "link": "https://daily-firehose.example.com/articles/123/",
                    "links": [
                        {
                            "rel": "alternate",
                            "type": "text/html",
                            "href": "https://original.example.com/article",
                        }
                    ],
                    "title": "An article",
                }
            ],
        }

        refresh_feed(feed)

        article = Article.objects.get(feed=feed, title="An article")
        self.assertEqual(article.url, "https://original.example.com/article")
        self.assertEqual(article.guid, "https://original.example.com/article")

    @patch("feeds.services.feedparser.parse", return_value={"feed": {}, "entries": []})
    @patch("feeds.services.fetch_feed_document")
    def test_success_records_attempt_and_last_success_and_clears_failure_state(
        self, mock_fetch, mock_parse
    ) -> None:
        mock_fetch.return_value = FetchedFeedDocument(
            content=b"feed",
            final_url="https://example.com/feed.xml",
            response_headers={"content-location": "https://example.com/feed.xml"},
        )
        feed = build_feed(
            consecutive_failures=3,
            last_error_code="timeout",
            last_error_message="Feed request timed out.",
            next_retry_at=timezone.now() + timedelta(hours=1),
        )

        result = refresh_feed(feed)

        feed.refresh_from_db()
        self.assertTrue(result.success)
        last_attempt_at = feed.last_attempt_at
        last_fetched_at = feed.last_fetched_at
        self.assertIsNotNone(last_attempt_at)
        self.assertIsNotNone(last_fetched_at)
        if last_attempt_at is None or last_fetched_at is None:
            self.fail("successful refresh timestamps were not recorded")
        self.assertGreaterEqual(last_fetched_at, last_attempt_at)
        self.assertEqual(feed.consecutive_failures, 0)
        self.assertEqual(feed.last_error_code, "")
        self.assertEqual(feed.last_error_message, "")
        self.assertIsNone(feed.next_retry_at)
        self.assertGreaterEqual(result.duration_seconds, 0)

    @patch(
        "feeds.services.fetch_feed_document",
        side_effect=FeedFetchError(code="timeout", message="Feed request timed out."),
    )
    def test_failure_persists_safe_state_and_bounded_exponential_backoff(
        self, mock_fetch
    ) -> None:
        previous_success = timezone.now() - timedelta(days=1)
        feed = build_feed(last_fetched_at=previous_success)

        first = refresh_feed(feed)
        feed.refresh_from_db()
        first_attempt = feed.last_attempt_at

        self.assertFalse(first.success)
        self.assertEqual(first.error_code, "timeout")
        self.assertEqual(feed.last_error_message, "Feed request timed out.")
        self.assertEqual(feed.consecutive_failures, 1)
        self.assertEqual(feed.last_fetched_at, previous_success)
        first_next_retry = feed.next_retry_at
        self.assertIsNotNone(first_attempt)
        self.assertIsNotNone(first_next_retry)
        if first_attempt is None or first_next_retry is None:
            self.fail("failure retry timestamps were not recorded")
        self.assertGreaterEqual(first_next_retry - first_attempt, timedelta(minutes=5))

        second = refresh_feed(feed)
        feed.refresh_from_db()

        self.assertFalse(second.success)
        self.assertEqual(feed.consecutive_failures, 2)
        second_attempt = feed.last_attempt_at
        second_next_retry = feed.next_retry_at
        if second_attempt is None or second_next_retry is None:
            self.fail("second failure retry timestamps were not recorded")
        second_delay = second_next_retry - second_attempt
        self.assertGreaterEqual(second_delay, timedelta(minutes=10))
        self.assertLessEqual(second_delay, timedelta(hours=24, seconds=1))
        self.assertEqual(mock_fetch.call_count, 2)

    @patch("feeds.services.feedparser.parse")
    @patch("feeds.services.fetch_feed_document")
    def test_integrity_failure_rolls_back_articles_metadata_and_last_success(
        self, mock_fetch, mock_parse
    ) -> None:
        mock_fetch.return_value = FetchedFeedDocument(
            content=b"feed",
            final_url="https://example.com/feed.xml",
            response_headers={"content-location": "https://example.com/feed.xml"},
        )
        previous_success = timezone.now() - timedelta(days=1)
        feed = build_feed(title="Original title", last_fetched_at=previous_success)
        Article.objects.create(
            feed=feed,
            title="URL match",
            url="https://example.com/conflict",
            guid="existing-guid",
        )
        Article.objects.create(
            feed=feed,
            title="GUID match",
            url="https://example.com/other",
            guid="different-guid",
        )
        mock_parse.return_value = {
            "feed": {"title": "Uncommitted title"},
            "entries": [
                {
                    "id": "new-guid",
                    "link": "https://example.com/new",
                    "title": "Must roll back",
                },
                {
                    "id": "different-guid",
                    "link": "https://example.com/conflict",
                    "title": "Conflicts",
                },
            ],
        }

        result = refresh_feed(feed)

        feed.refresh_from_db()
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "integrity_error")
        self.assertEqual(feed.title, "Original title")
        self.assertEqual(feed.last_fetched_at, previous_success)
        self.assertFalse(Article.objects.filter(guid="new-guid").exists())
        self.assertEqual(feed.consecutive_failures, 1)

    @patch(
        "feeds.services.fetch_feed_document",
        side_effect=RuntimeError("private implementation detail"),
    )
    def test_unexpected_exception_is_safe_and_logged_with_traceback(
        self, mock_fetch
    ) -> None:
        remote_title = "Failure\r\nTitle\x1f" + "x" * 200
        feed = build_feed(title=remote_title)

        with self.assertLogs("feeds.services", level="ERROR") as logs:
            result = refresh_feed(feed)

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "unexpected_error")
        self.assertNotIn("private implementation detail", result.error_message)
        self.assertIn("feed_refresh_completed", logs.output[0])
        self.assertIn("status=failed", logs.output[0])
        self.assertIn("error_code=unexpected_error", logs.output[0])
        self.assertIn("consecutive_failures=1", logs.output[0])
        self.assertIn("next_retry_at=", logs.output[0])
        self.assertIn("Traceback", logs.output[0])
        record = cast(Any, logs.records[0])
        self.assertEqual(record.feed_id, feed.pk)
        self.assertEqual(record.feed_title, safe_feed_title(remote_title))
        self.assertEqual(record.status, "failed")
        self.assertEqual(record.error_code, "unexpected_error")
        self.assertGreaterEqual(record.duration_seconds, 0)
        self.assertEqual(record.consecutive_failures, 1)
        self.assertEqual(record.next_retry_at, result.next_retry_at)
        self.assertNotIn("\r", record.getMessage())
        self.assertNotIn("\n", record.getMessage())
        self.assertNotIn("\x1f", record.getMessage())
        self.assertNotIn("\x00", safe_feed_title("Failure\x00Title"))

    @patch("feeds.services.feedparser.parse", return_value={"bozo": True})
    @patch("feeds.services.fetch_feed_document")
    def test_unusable_parse_is_classified_without_last_success(
        self, mock_fetch, mock_parse
    ) -> None:
        mock_fetch.return_value = FetchedFeedDocument(
            content=b"not a feed",
            final_url="https://example.com/feed.xml",
            response_headers={"content-location": "https://example.com/feed.xml"},
        )
        feed = build_feed()

        result = refresh_feed(feed)

        feed.refresh_from_db()
        self.assertEqual(result.error_code, "parse_error")
        self.assertIsNone(feed.last_fetched_at)

    @patch("feeds.services.time.monotonic", side_effect=[10.0, 10.25])
    @patch("feeds.services.feedparser.parse")
    @patch("feeds.services.fetch_feed_document")
    def test_success_log_contains_safe_rendered_and_structured_context(
        self, mock_fetch, mock_parse, mock_monotonic
    ) -> None:
        mock_fetch.return_value = FetchedFeedDocument(
            content=b"feed",
            final_url="https://example.com/feed.xml",
            response_headers={"content-location": "https://example.com/feed.xml"},
        )
        remote_title = "Remote\r\nTitle\x1f" + "x" * 200
        mock_parse.return_value = {
            "feed": {"title": remote_title},
            "entries": [
                {
                    "id": "new-guid",
                    "link": "https://example.com/new",
                    "title": "New article",
                }
            ],
        }
        feed = build_feed()

        with self.assertLogs("feeds.services", level="INFO") as logs:
            result = refresh_feed(feed)

        record = cast(Any, logs.records[0])
        self.assertEqual(result.duration_seconds, 0.25)
        self.assertEqual(record.feed_id, feed.pk)
        self.assertEqual(record.feed_title, safe_feed_title(remote_title))
        self.assertEqual(record.status, "succeeded")
        self.assertEqual(record.duration_seconds, 0.25)
        self.assertEqual(record.articles_created, 1)
        self.assertEqual(record.articles_updated, 0)
        self.assertEqual(record.consecutive_failures, 0)
        self.assertIsNone(record.next_retry_at)
        self.assertIn("feed_refresh_completed", record.getMessage())
        self.assertIn("status=succeeded", record.getMessage())
        self.assertIn("duration_seconds=0.250000", record.getMessage())
        self.assertIn("created=1 updated=0", record.getMessage())
        self.assertNotIn("\r", record.getMessage())
        self.assertNotIn("\n", record.getMessage())
        self.assertNotIn("\x1f", record.getMessage())
        self.assertNotIn("\x00", safe_feed_title("Remote\x00Title"))


class RefreshActiveFeedsTests(TestCase):
    @patch("feeds.services.feedparser.parse", return_value={"feed": {}, "entries": []})
    @patch("feeds.services.fetch_feed_document")
    def test_mixed_failure_continues_to_later_feed(
        self, mock_fetch, mock_parse
    ) -> None:
        failed_feed = build_feed(title="A failing feed")
        healthy_feed = build_feed(title="B healthy feed")
        document = FetchedFeedDocument(
            content=b"feed",
            final_url="https://example.com/feed.xml",
            response_headers={"content-location": "https://example.com/feed.xml"},
        )

        def fetch(url: str) -> FetchedFeedDocument:
            if url == failed_feed.feed_url:
                raise FeedFetchError(code="timeout", message="Feed request timed out.")
            return document

        mock_fetch.side_effect = fetch

        results = refresh_active_feeds()

        self.assertEqual([result.status for result in results], ["failed", "succeeded"])
        healthy_feed.refresh_from_db()
        self.assertIsNotNone(healthy_feed.last_fetched_at)
        self.assertEqual(mock_fetch.call_count, 2)

    @patch("feeds.services.refresh_feed")
    def test_backoff_feed_is_returned_as_skipped_without_starving_healthy_feed(
        self, mock_refresh_feed
    ) -> None:
        skipped_feed = build_feed(
            title="A backoff feed",
            last_error_code="timeout",
            last_error_message="Feed request timed out.",
            next_retry_at=timezone.now() + timedelta(hours=1),
        )
        healthy_feed = build_feed(title="B healthy feed")
        mock_refresh_feed.return_value = RefreshResult(feed=healthy_feed)

        results = refresh_active_feeds()

        self.assertEqual(
            [result.status for result in results], ["skipped", "succeeded"]
        )
        self.assertEqual(results[0].feed, skipped_feed)
        mock_refresh_feed.assert_called_once_with(healthy_feed)

    @patch("feeds.services.refresh_feed")
    def test_exactly_due_feed_is_eligible(self, mock_refresh_feed) -> None:
        due_at = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
        due_feed = build_feed(
            last_error_code="timeout",
            last_error_message="Feed request timed out.",
            next_retry_at=due_at,
        )
        mock_refresh_feed.return_value = RefreshResult(feed=due_feed)

        with patch("feeds.services.timezone.now", return_value=due_at):
            results = refresh_active_feeds()

        self.assertEqual([result.status for result in results], ["succeeded"])
        mock_refresh_feed.assert_called_once_with(due_feed)

    @patch("feeds.services.refresh_feed")
    def test_persisted_retry_state_controls_restart_eligibility(
        self, mock_refresh_feed
    ) -> None:
        current_time = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
        feed = build_feed(
            last_error_code="timeout",
            last_error_message="Feed request timed out.",
            next_retry_at=current_time + timedelta(minutes=5),
        )
        with patch("feeds.services.timezone.now", return_value=current_time):
            skipped_results = refresh_active_feeds()

        self.assertEqual([result.status for result in skipped_results], ["skipped"])
        mock_refresh_feed.assert_not_called()

        feed.next_retry_at = current_time - timedelta(seconds=1)
        feed.save(update_fields=["next_retry_at"])
        mock_refresh_feed.return_value = RefreshResult(feed=feed)

        with patch("feeds.services.timezone.now", return_value=current_time):
            eligible_results = refresh_active_feeds()

        self.assertEqual([result.status for result in eligible_results], ["succeeded"])
        mock_refresh_feed.assert_called_once()

    @patch("feeds.services.refresh_feed")
    def test_retry_expiring_after_earlier_work_is_evaluated_per_feed(
        self, mock_refresh_feed
    ) -> None:
        batch_start = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
        first_feed = build_feed(title="A earlier feed")
        due_later_feed = build_feed(
            title="B retry becomes due",
            last_error_code="timeout",
            last_error_message="Feed request timed out.",
            next_retry_at=batch_start + timedelta(seconds=5),
        )
        mock_refresh_feed.side_effect = lambda feed: RefreshResult(feed=feed)

        with patch(
            "feeds.services.timezone.now",
            side_effect=[batch_start, batch_start + timedelta(seconds=10)],
        ):
            results = refresh_active_feeds()

        self.assertEqual(
            [result.status for result in results], ["succeeded", "succeeded"]
        )
        self.assertEqual(
            [call.args[0] for call in mock_refresh_feed.call_args_list],
            [first_feed, due_later_feed],
        )

    @patch("feeds.services.feedparser.parse", return_value={"feed": {}, "entries": []})
    @patch("feeds.services.fetch_feed_document")
    def test_unexpected_exception_continues_to_later_healthy_feed(
        self, mock_fetch, mock_parse
    ) -> None:
        failed_feed = build_feed(title="A unexpected failure")
        healthy_feed = build_feed(title="B healthy after failure")
        document = FetchedFeedDocument(
            content=b"feed",
            final_url="https://example.com/feed.xml",
            response_headers={"content-location": "https://example.com/feed.xml"},
        )
        mock_fetch.side_effect = [RuntimeError("private detail"), document]

        with self.assertLogs("feeds.services", level="INFO"):
            results = refresh_active_feeds()

        self.assertEqual([result.status for result in results], ["failed", "succeeded"])
        self.assertEqual(
            [result.feed for result in results], [failed_feed, healthy_feed]
        )
        self.assertEqual(results[0].error_code, "unexpected_error")
        healthy_feed.refresh_from_db()
        self.assertIsNotNone(healthy_feed.last_fetched_at)
        self.assertEqual(mock_fetch.call_count, 2)


class RefreshCommandTests(TestCase):
    @patch("feeds.management.commands.refresh_feeds.refresh_active_feeds")
    def test_command_reports_all_four_states_and_fails_only_for_failure(
        self, mock_refresh
    ) -> None:
        successful = build_feed(title="Successful")
        failed = build_feed(title="Failed")
        skipped = build_feed(title="Skipped")
        superseded = build_feed(title="Superseded")
        mock_refresh.return_value = [
            RefreshResult(feed=successful, created=2),
            RefreshResult(
                feed=failed,
                success=False,
                error_code="timeout",
                error_message="Feed request timed out.",
                next_retry_at=timezone.now() + timedelta(minutes=5),
            ),
            RefreshResult(
                feed=skipped,
                success=False,
                skipped=True,
                error_code="timeout",
                error_message="Feed request timed out.",
                next_retry_at=timezone.now() + timedelta(hours=1),
            ),
            RefreshResult(
                feed=superseded,
                success=False,
                superseded=True,
                error_code="superseded",
                error_message="A newer refresh owns status.",
            ),
        ]
        stdout = StringIO()

        with self.assertRaisesMessage(CommandError, "1 feed refresh failed"):
            call_command("refresh_feeds", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("Successful: 2 created, 0 updated", output)
        self.assertIn("Failed: failed [timeout] Feed request timed out.", output)
        self.assertIn("Skipped: skipped until", output)
        self.assertIn(
            "Superseded: superseded [superseded] A newer refresh owns status.", output
        )
        self.assertIn(
            "Refresh complete: checked 4; attempted 3; succeeded 1; failed 1; "
            "skipped 1; superseded 1.",
            output,
        )

        feeds = [build_feed(title="Older"), build_feed(title="Oldest")]
        mock_refresh.return_value = [
            RefreshResult(
                feed=feed,
                success=False,
                superseded=True,
                error_code="superseded",
                error_message="A newer refresh owns status.",
            )
            for feed in feeds
        ]
        stdout = StringIO()

        call_command("refresh_feeds", stdout=stdout)

        output = stdout.getvalue()
        self.assertNotIn(": failed", output)
        self.assertIn(
            "Refresh complete: checked 2; attempted 2; succeeded 0; failed 0; "
            "skipped 0; superseded 2.",
            output,
        )

    @patch("feeds.management.commands.refresh_feeds.refresh_active_feeds")
    def test_command_sanitizes_and_bounds_remote_titles(self, mock_refresh) -> None:
        remote_title = "Remote\r\nTitle\x1f" + "x" * 200
        feed = build_feed(title=remote_title)
        mock_refresh.return_value = [RefreshResult(feed=feed)]
        stdout = StringIO()

        call_command("refresh_feeds", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn(f"{safe_feed_title(remote_title)}: 0 created, 0 updated", output)
        self.assertNotIn("\r", output)
        self.assertNotIn("\x1f", output)
        self.assertNotIn("\x00", safe_feed_title("Remote\x00Title"))
        self.assertLessEqual(len(safe_feed_title(remote_title)), 160)
        self.assertEqual(safe_feed_title("Ｆｅｅｄ"), "Feed")


class RefreshFeedsFeedbackTests(StaticFilesTestCase):
    def setUp(self) -> None:
        self.user = build_user()
        self.feed_with_new_articles = build_feed(
            title="Feed with new articles",
            feed_url="https://example.com/new.xml",
        )
        self.feed_without_new_articles = build_feed(
            title="Feed without new articles",
            feed_url="https://example.com/old.xml",
        )
        self.client.force_login(self.user)

    @patch("feeds.views.refresh_active_feeds")
    def test_refresh_feedback_includes_feeds_with_new_articles(
        self, mock_refresh_active_feeds
    ) -> None:
        mock_refresh_active_feeds.return_value = [
            RefreshResult(feed=self.feed_with_new_articles, created=3, updated=2),
            RefreshResult(feed=self.feed_without_new_articles, created=0, updated=4),
        ]

        response = self.client.post(reverse("refresh-feeds"), follow=True)

        self.assertContains(
            response,
            "Refresh complete: checked 2 feeds; attempted 2; succeeded 2; failed 0; skipped 0; superseded 0; 1 feeds had new articles; 3 new articles; 6 existing articles updated.",
        )
        mock_refresh_active_feeds.assert_called_once_with()

    @patch("feeds.views.refresh_active_feeds")
    def test_refresh_feedback_names_failed_feeds_safely(
        self, mock_refresh_active_feeds
    ) -> None:
        mock_refresh_active_feeds.return_value = [
            RefreshResult(feed=self.feed_with_new_articles, created=1),
            RefreshResult(
                feed=self.feed_without_new_articles,
                success=False,
                error_code="timeout",
                error_message="Feed request timed out.",
                next_retry_at=timezone.now() + timedelta(minutes=5),
            ),
        ]

        response = self.client.post(reverse("refresh-feeds"), follow=True)

        self.assertContains(
            response,
            "checked 2 feeds; attempted 2; succeeded 1; failed 1; skipped 0; superseded 0",
        )
        self.assertContains(response, "Failed feeds: Feed without new articles.")

        failed = build_feed(title="Failed browser feed")
        skipped = build_feed(title="Backoff browser feed")
        superseded = build_feed(title="Superseded browser feed")
        retry_at = timezone.now() + timedelta(minutes=5)
        mock_refresh_active_feeds.return_value = [
            RefreshResult(feed=self.feed_with_new_articles, created=1),
            RefreshResult(
                feed=failed,
                success=False,
                error_code="timeout",
                error_message="Feed request timed out.",
                next_retry_at=retry_at,
            ),
            RefreshResult(
                feed=skipped,
                success=False,
                skipped=True,
                error_code="timeout",
                error_message="Feed request timed out.",
                next_retry_at=retry_at,
            ),
            RefreshResult(
                feed=superseded,
                success=False,
                superseded=True,
                error_code="superseded",
                error_message="A newer refresh owns status.",
            ),
        ]

        response = self.client.post(reverse("refresh-feeds"), follow=True)

        self.assertContains(
            response,
            "checked 4 feeds; attempted 3; succeeded 1; failed 1; skipped 1; superseded 1",
        )
        self.assertContains(response, "Failed feeds: Failed browser feed.")
        self.assertContains(response, "Superseded feeds: Superseded browser feed.")
        self.assertNotContains(response, "Failed feeds: Superseded browser feed")

        mock_refresh_active_feeds.return_value = [
            RefreshResult(
                feed=feed,
                success=False,
                superseded=True,
                error_code="superseded",
                error_message="A newer refresh owns status.",
            )
            for feed in (self.feed_with_new_articles, self.feed_without_new_articles)
        ]

        response = self.client.post(reverse("refresh-feeds"), follow=True)

        self.assertContains(
            response,
            "checked 2 feeds; attempted 2; succeeded 0; failed 0; skipped 0; superseded 2",
        )
        self.assertContains(
            response,
            "Superseded feeds: Feed with new articles, Feed without new articles.",
        )
        self.assertNotContains(response, "Failed feeds:")
