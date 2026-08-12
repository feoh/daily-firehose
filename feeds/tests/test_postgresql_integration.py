from __future__ import annotations

import threading
from collections.abc import Sequence
from datetime import date
from typing import Any
from unittest import expectedFailure, skipUnless
from unittest.mock import patch

from django.db import IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from django.db.models.query import QuerySet
from django.test import TransactionTestCase

from ..feed_fetch import FeedFetchError, FetchedFeedDocument
from ..models import (
    Article,
    BulkReadMarker,
    Category,
    Feed,
    NewsletterIssue,
    ReadScope,
    SavedArticle,
    UserPreference,
)
from ..services import (
    NEWSLETTER_FEED_URL,
    _category_from_name,
    import_opml,
    import_postmark_newsletter,
    newsletter_feed,
    refresh_feed,
)
from ..views import _preferences
from .support.builders import (
    build_article,
    build_feed,
    build_user,
    newsletter_payload,
)
from .support.concurrency import ConcurrentOutcome, run_concurrently

_POSTGRES_ONLY = connection.vendor == "postgresql"
_DOCUMENT = FetchedFeedDocument(
    content=b"feed",
    final_url="https://example.com/feed.xml",
    response_headers={"content-location": "https://example.com/feed.xml"},
)
_RACE_TIMEOUT = 10.0


def _errors[T](outcomes: Sequence[ConcurrentOutcome[T]]) -> list[BaseException]:
    return [outcome.error for outcome in outcomes if outcome.error is not None]


@skipUnless(_POSTGRES_ONLY, "PostgreSQL integration lane only")
class PostgreSQLIntegrationTests(TransactionTestCase):
    """Real PostgreSQL constraints, transactions, locks, and race contracts."""

    reset_sequences = True

    def test_lane_runs_against_postgresql_17(self) -> None:
        with connection.cursor() as cursor:
            cursor.execute("SHOW server_version_num")
            version_number = int(cursor.fetchone()[0])
            cursor.execute("SHOW statement_timeout")
            statement_timeout = cursor.fetchone()[0]
            cursor.execute("SHOW lock_timeout")
            lock_timeout = cursor.fetchone()[0]

        self.assertEqual(connection.vendor, "postgresql")
        self.assertGreaterEqual(version_number, 170000)
        self.assertLess(version_number, 180000)
        self.assertEqual(statement_timeout, "5s")
        self.assertEqual(lock_timeout, "5s")

    def test_disk_migrations_and_durable_unique_constraints_are_applied(self) -> None:
        loader = MigrationLoader(connection)
        applied = MigrationRecorder(connection).applied_migrations()
        self.assertTrue(set(loader.graph.leaf_nodes("feeds")).issubset(applied))

        expected_named_constraints = {
            "feeds_article": {
                "unique_article_guid_per_feed",
                "unique_article_url_per_feed",
            },
            "feeds_savedarticle": {"unique_saved_article"},
        }
        with connection.cursor() as cursor:
            for table, expected in expected_named_constraints.items():
                constraints = connection.introspection.get_constraints(cursor, table)
                self.assertTrue(expected.issubset(constraints))
                self.assertTrue(all(constraints[name]["unique"] for name in expected))

            for table, columns in (
                ("feeds_category", {"name", "slug"}),
                ("feeds_feed", {"feed_url"}),
                (
                    "feeds_newsletterissue",
                    {"article_id", "message_id", "public_id"},
                ),
                ("feeds_userpreference", {"user_id"}),
            ):
                constraints = connection.introspection.get_constraints(cursor, table)
                unique_columns = {
                    column
                    for details in constraints.values()
                    if details["unique"]
                    for column in details["columns"]
                }
                self.assertTrue(columns.issubset(unique_columns))

    @expectedFailure
    def test_bulk_marker_shapes_are_rejected_by_database_constraints(self) -> None:
        user = build_user()
        feed = build_feed()
        shapes: dict[str, dict[str, Any]] = {
            "feed_without_feed": {
                "scope": ReadScope.FEED,
                "feed": None,
                "period_start": None,
                "period_end": None,
            },
            "period_without_dates": {
                "scope": ReadScope.DAY,
                "feed": None,
                "period_start": None,
                "period_end": None,
            },
            "period_with_feed": {
                "scope": ReadScope.DAY,
                "feed": feed,
                "period_start": date(2026, 1, 5),
                "period_end": date(2026, 1, 5),
            },
            "reversed_period": {
                "scope": ReadScope.WEEK,
                "feed": None,
                "period_start": date(2026, 1, 11),
                "period_end": date(2026, 1, 5),
            },
        }
        accepted: set[str] = set()
        for name, fields in shapes.items():
            try:
                with transaction.atomic():
                    BulkReadMarker.objects.create(user=user, **fields)
            except IntegrityError:
                continue
            accepted.add(name)

        if accepted:
            self.assertEqual(accepted, set(shapes))
        self.assertEqual(accepted, set())

    @expectedFailure
    def test_nullable_period_marker_duplicate_race_commits_one_row(self) -> None:
        user = build_user()
        fields = {
            "user_id": user.pk,
            "scope": ReadScope.DAY,
            "feed": None,
            "period_start": date(2026, 1, 5),
            "period_end": date(2026, 1, 5),
        }

        outcomes = run_concurrently(
            [lambda: BulkReadMarker.objects.create(**fields) for _ in range(2)],
            timeout=_RACE_TIMEOUT,
        )
        errors = _errors(outcomes)
        count = BulkReadMarker.objects.filter(**fields).count()

        if count != 1:
            self.assertEqual(errors, [])
            self.assertEqual(count, 2)
        self.assertEqual(count, 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], IntegrityError)

    @expectedFailure
    def test_nullable_feed_marker_duplicate_race_commits_one_row(self) -> None:
        user = build_user()
        feed = build_feed()
        fields = {
            "user_id": user.pk,
            "scope": ReadScope.FEED,
            "feed_id": feed.pk,
            "period_start": None,
            "period_end": None,
        }

        outcomes = run_concurrently(
            [lambda: BulkReadMarker.objects.create(**fields) for _ in range(2)],
            timeout=_RACE_TIMEOUT,
        )
        errors = _errors(outcomes)
        count = BulkReadMarker.objects.filter(**fields).count()

        if count != 1:
            self.assertEqual(errors, [])
            self.assertEqual(count, 2)
        self.assertEqual(count, 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], IntegrityError)

    def test_saved_article_duplicate_race_is_stopped_by_unique_constraint(self) -> None:
        user = build_user()
        article = build_article()
        fields = {
            "user_id": user.pk,
            "article_id": article.pk,
            "url": article.url,
            "title": article.title,
            "feed_id": article.feed.pk,
        }

        outcomes = run_concurrently(
            [lambda: SavedArticle.objects.create(**fields) for _ in range(2)],
            timeout=_RACE_TIMEOUT,
        )
        errors = _errors(outcomes)

        self.assertEqual(
            SavedArticle.objects.filter(user=user, article=article).count(), 1
        )
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], IntegrityError)

    def test_concurrent_postmark_replay_returns_one_issue_without_errors(self) -> None:
        newsletter_feed()
        payload = newsletter_payload(message_id="concurrent-postmark-message")
        lookup_barrier = threading.Barrier(2, timeout=_RACE_TIMEOUT)
        original_first = QuerySet.first

        def synchronized_first(queryset: QuerySet):
            result = original_first(queryset)
            if queryset.model is NewsletterIssue:
                lookup_barrier.wait()
            return result

        with patch.object(QuerySet, "first", new=synchronized_first):
            outcomes = run_concurrently(
                [
                    lambda: import_postmark_newsletter(
                        payload=payload,
                        base_url="https://daily-firehose.example/",
                    )
                    for _ in range(2)
                ],
                timeout=_RACE_TIMEOUT,
            )

        errors = _errors(outcomes)
        self.assertEqual(errors, [])
        results = [outcome.value for outcome in outcomes if outcome.value is not None]
        issue_ids = {result.issue.pk for result in results}
        self.assertEqual(len(issue_ids), 1)
        self.assertEqual(sorted(result.created for result in results), [False, True])
        self.assertEqual(
            NewsletterIssue.objects.filter(message_id=payload["MessageID"]).count(), 1
        )
        self.assertEqual(Article.objects.filter(guid=payload["MessageID"]).count(), 1)

    def test_postmark_issue_failure_rolls_back_feed_and_article(self) -> None:
        payload = newsletter_payload(message_id="postgres-rollback-message")
        with (
            patch.object(
                NewsletterIssue.objects,
                "create",
                side_effect=RuntimeError("post-article write failed"),
            ),
            self.assertRaisesRegex(RuntimeError, "post-article write failed"),
        ):
            import_postmark_newsletter(
                payload=payload,
                base_url="https://daily-firehose.example/",
            )

        leaked_feed = Feed.objects.filter(feed_url=NEWSLETTER_FEED_URL).exists()
        leaked_article = Article.objects.filter(guid=payload["MessageID"]).exists()
        self.assertFalse(leaked_feed)
        self.assertFalse(leaked_article)

    def test_concurrent_same_guid_refresh_has_one_create_and_one_update(self) -> None:
        feed = build_feed()
        write_barrier = threading.Barrier(2, timeout=_RACE_TIMEOUT)
        original_upsert = Article.objects.update_or_create

        def synchronized_upsert(*args, **kwargs):
            write_barrier.wait()
            return original_upsert(*args, **kwargs)

        parsed = {
            "feed": {"title": feed.title},
            "entries": [
                {
                    "id": "same-guid",
                    "link": "https://example.com/same-article",
                    "title": "Same logical article",
                }
            ],
        }
        with (
            patch("feeds.services.fetch_feed_document", return_value=_DOCUMENT),
            patch("feeds.services.feedparser.parse", return_value=parsed),
            patch.object(
                Article.objects,
                "update_or_create",
                side_effect=synchronized_upsert,
            ),
        ):
            outcomes = run_concurrently(
                [lambda: refresh_feed(Feed.objects.get(pk=feed.pk)) for _ in range(2)],
                timeout=_RACE_TIMEOUT,
            )

        self.assertEqual(_errors(outcomes), [])
        results = [outcome.value for outcome in outcomes]
        self.assertTrue(
            all(result is not None and result.success for result in results)
        )
        self.assertEqual(sorted(result.created for result in results if result), [0, 1])
        self.assertEqual(sorted(result.updated for result in results if result), [0, 1])
        self.assertEqual(Article.objects.filter(feed=feed, guid="same-guid").count(), 1)

    @expectedFailure
    def test_concurrent_changed_guids_for_one_url_are_reconciled(self) -> None:
        feed = build_feed()
        local = threading.local()
        write_barrier = threading.Barrier(2, timeout=_RACE_TIMEOUT)
        original_upsert = Article.objects.update_or_create

        def fetch_document(_url: str) -> FetchedFeedDocument:
            return FetchedFeedDocument(
                content=local.guid.encode(),
                final_url=_DOCUMENT.final_url,
                response_headers=_DOCUMENT.response_headers,
            )

        def parse_document(content: bytes, **_kwargs):
            guid = content.decode()
            return {
                "feed": {"title": feed.title},
                "entries": [
                    {
                        "id": guid,
                        "link": "https://example.com/stable-race-url",
                        "title": f"Article {guid}",
                    }
                ],
            }

        def synchronized_upsert(*args, **kwargs):
            write_barrier.wait()
            return original_upsert(*args, **kwargs)

        def refresh(guid: str):
            local.guid = guid
            return refresh_feed(Feed.objects.get(pk=feed.pk))

        with (
            patch("feeds.services.fetch_feed_document", side_effect=fetch_document),
            patch("feeds.services.feedparser.parse", side_effect=parse_document),
            patch.object(
                Article.objects,
                "update_or_create",
                side_effect=synchronized_upsert,
            ),
        ):
            outcomes = run_concurrently(
                [lambda: refresh("guid-a"), lambda: refresh("guid-b")],
                timeout=_RACE_TIMEOUT,
            )

        self.assertEqual(_errors(outcomes), [])
        results = [outcome.value for outcome in outcomes]
        failures = [
            result for result in results if result is not None and not result.success
        ]
        if failures:
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0].error_code, "integrity_error")
        self.assertTrue(
            all(result is not None and result.success for result in results)
        )
        self.assertEqual(
            Article.objects.filter(
                feed=feed,
                url="https://example.com/stable-race-url",
            ).count(),
            1,
        )

    @expectedFailure
    def test_older_refresh_failure_cannot_overwrite_newer_success_status(self) -> None:
        feed = build_feed()
        local = threading.local()
        failure_started = threading.Event()
        release_failure = threading.Event()

        def fetch_document(_url: str) -> FetchedFeedDocument:
            if local.mode == "failure":
                failure_started.set()
                if not release_failure.wait(_RACE_TIMEOUT):
                    raise AssertionError("timed out staging refresh failure")
                raise FeedFetchError(code="timeout", message="Feed request timed out.")
            if not failure_started.wait(_RACE_TIMEOUT):
                raise AssertionError("failure attempt did not start")
            return _DOCUMENT

        def failed_refresh():
            local.mode = "failure"
            return refresh_feed(Feed.objects.get(pk=feed.pk))

        def successful_refresh():
            local.mode = "success"
            try:
                return refresh_feed(Feed.objects.get(pk=feed.pk))
            finally:
                release_failure.set()

        with (
            patch("feeds.services.fetch_feed_document", side_effect=fetch_document),
            patch(
                "feeds.services.feedparser.parse",
                return_value={"feed": {"title": feed.title}, "entries": []},
            ),
        ):
            outcomes = run_concurrently(
                [failed_refresh, successful_refresh], timeout=_RACE_TIMEOUT
            )

        self.assertEqual(_errors(outcomes), [])
        feed.refresh_from_db()
        if feed.last_error_code:
            self.assertEqual(feed.last_error_code, "timeout")
            self.assertEqual(feed.consecutive_failures, 1)
        self.assertEqual(feed.last_error_code, "")
        self.assertEqual(feed.consecutive_failures, 0)
        self.assertIsNone(feed.next_retry_at)
        self.assertIsNotNone(feed.last_fetched_at)

    def test_concurrent_preference_get_or_create_returns_one_row(self) -> None:
        user = build_user()
        outcomes = run_concurrently(
            [
                lambda: _preferences(type(user).objects.get(pk=user.pk))
                for _ in range(2)
            ],
            timeout=_RACE_TIMEOUT,
        )

        self.assertEqual(_errors(outcomes), [])
        preference_ids = {
            outcome.value.pk for outcome in outcomes if outcome.value is not None
        }
        self.assertEqual(len(preference_ids), 1)
        self.assertEqual(UserPreference.objects.filter(user=user).count(), 1)

    @expectedFailure
    def test_concurrent_category_upsert_returns_one_row_without_errors(self) -> None:
        lookup_barrier = threading.Barrier(2, timeout=_RACE_TIMEOUT)
        original_first = QuerySet.first

        def synchronized_first(queryset: QuerySet):
            result = original_first(queryset)
            if queryset.model is Category:
                lookup_barrier.wait()
            return result

        with patch.object(QuerySet, "first", new=synchronized_first):
            outcomes = run_concurrently(
                [lambda: _category_from_name("Concurrent Category") for _ in range(2)],
                timeout=_RACE_TIMEOUT,
            )

        errors = _errors(outcomes)
        if errors:
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], IntegrityError)
        self.assertEqual(errors, [])
        category_ids = {
            outcome.value.pk for outcome in outcomes if outcome.value is not None
        }
        self.assertEqual(len(category_ids), 1)
        self.assertEqual(Category.objects.filter(name="Concurrent Category").count(), 1)

    def test_concurrent_opml_feed_upsert_returns_one_row(self) -> None:
        content = b"""<?xml version="1.0"?>
        <opml version="2.0"><body>
          <outline text="Race Feed" type="rss"
                   xmlUrl="https://example.com/concurrent-feed.xml" />
        </body></opml>
        """
        write_barrier = threading.Barrier(2, timeout=_RACE_TIMEOUT)
        original_upsert = Feed.objects.update_or_create

        def synchronized_upsert(*args, **kwargs):
            write_barrier.wait()
            return original_upsert(*args, **kwargs)

        with patch.object(
            Feed.objects,
            "update_or_create",
            side_effect=synchronized_upsert,
        ):
            outcomes = run_concurrently(
                [lambda: import_opml(content) for _ in range(2)],
                timeout=_RACE_TIMEOUT,
            )

        self.assertEqual(_errors(outcomes), [])
        self.assertEqual(
            Feed.objects.filter(
                feed_url="https://example.com/concurrent-feed.xml"
            ).count(),
            1,
        )
        results = [outcome.value for outcome in outcomes if outcome.value is not None]
        self.assertEqual(sum(result.created for result in results), 1)
        self.assertEqual(sum(result.updated for result in results), 1)

    def test_concurrent_newsletter_feed_get_or_create_returns_one_row(self) -> None:
        outcomes = run_concurrently(
            [newsletter_feed, newsletter_feed], timeout=_RACE_TIMEOUT
        )

        self.assertEqual(_errors(outcomes), [])
        feed_ids = {
            outcome.value.pk for outcome in outcomes if outcome.value is not None
        }
        self.assertEqual(len(feed_ids), 1)
        self.assertEqual(Feed.objects.filter(feed_url=NEWSLETTER_FEED_URL).count(), 1)

    def test_refresh_failure_lock_serializes_failure_count_updates(self) -> None:
        feed = build_feed()
        with patch(
            "feeds.services.fetch_feed_document",
            side_effect=FeedFetchError(
                code="timeout", message="Feed request timed out."
            ),
        ):
            outcomes = run_concurrently(
                [lambda: refresh_feed(Feed.objects.get(pk=feed.pk)) for _ in range(2)],
                timeout=_RACE_TIMEOUT,
            )

        self.assertEqual(_errors(outcomes), [])
        feed.refresh_from_db()
        self.assertEqual(feed.consecutive_failures, 2)
        self.assertEqual(feed.last_error_code, "timeout")
