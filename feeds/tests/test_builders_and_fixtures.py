from __future__ import annotations

from datetime import date, timedelta
from importlib import import_module
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import feedparser
import requests
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ..models import (
    ApiToken,
    ArticleReadState,
    BulkReadMarker,
    Feed,
    NewsletterIssue,
    ReadScope,
    SavedArticle,
)
from ..services import import_opml, sanitize_newsletter_html
from .support.builders import (
    FIXED_NOW,
    FIXED_TODAY,
    build_api_token,
    build_article,
    build_bulk_marker,
    build_category,
    build_feed,
    build_feed_marker,
    build_newsletter_issue,
    build_period_marker,
    build_read_state,
    build_saved_article,
    build_user,
    fixture_bytes,
    fixture_text,
    frozen_time,
    newsletter_payload,
)
from .support.http_responses import (
    configure_json_response,
    configure_request_timeout,
)


class BuilderTests(TestCase):
    def test_builders_create_a_connected_domain_graph(self) -> None:
        user = build_user()
        category = build_category()
        feed = build_feed(category=category)
        article = build_article(feed=feed)
        read_state = build_read_state(user=user, article=article)
        marker = build_period_marker(user=user)
        saved = build_saved_article(user=user, article=article)
        token, raw_key = build_api_token(user=user)
        newsletter_article = build_article(feed=feed)
        issue = build_newsletter_issue(article=newsletter_article)

        self.assertIsInstance(read_state, ArticleReadState)
        self.assertEqual(read_state.user, user)
        self.assertEqual(read_state.article, article)
        self.assertIsInstance(marker, BulkReadMarker)
        self.assertEqual(marker.period_start, FIXED_TODAY)
        self.assertIsInstance(saved, SavedArticle)
        self.assertEqual(saved.feed, feed)
        self.assertEqual(saved.category, category)
        self.assertFalse(saved.linkding_saved)
        self.assertIsInstance(token, ApiToken)
        self.assertNotEqual(token.key_hash, raw_key)
        self.assertTrue(raw_key.startswith(token.prefix))
        self.assertIsInstance(issue, NewsletterIssue)
        self.assertEqual(issue.article, newsletter_article)
        self.assertEqual(article.published_at, FIXED_NOW)

    def test_default_builder_identities_are_safely_composable(self) -> None:
        users = [build_user(), build_user()]
        categories = [build_category(), build_category()]
        feeds = [build_feed(), build_feed()]
        articles = [build_article(feed=feeds[0]), build_article(feed=feeds[0])]
        tokens = [build_api_token(user=users[0]), build_api_token(user=users[0])]
        issues = [
            build_newsletter_issue(article=build_article(feed=feeds[0])),
            build_newsletter_issue(article=build_article(feed=feeds[0])),
        ]

        self.assertEqual(len({user.username for user in users}), 2)
        self.assertEqual(len({category.slug for category in categories}), 2)
        self.assertEqual(len({feed.feed_url for feed in feeds}), 2)
        self.assertEqual(len({article.url for article in articles}), 2)
        self.assertEqual(len({token.name for token, _ in tokens}), 2)
        self.assertEqual(len({issue.message_id for issue in issues}), 2)

    def test_frozen_time_controls_django_and_automatic_model_timestamps(self) -> None:
        with frozen_time() as clock:
            self.assertEqual(timezone.now(), clock.now)
            self.assertEqual(timezone.localdate(), clock.today)
            feed = build_feed()
            article = build_article(feed=feed)

        later = FIXED_NOW + timedelta(hours=1)
        with frozen_time(later):
            feed.title = "Updated feed"
            feed.save(update_fields=["title", "updated_at"])
            article.title = "Updated article"
            article.save(update_fields=["title", "updated_at"])

        feed.refresh_from_db()
        article.refresh_from_db()
        self.assertNotEqual(timezone.now(), later)
        self.assertNotEqual(timezone.localdate(), clock.today)
        self.assertEqual(clock.now, FIXED_NOW)
        self.assertEqual(clock.today, FIXED_TODAY)
        self.assertEqual(feed.created_at, clock.now)
        self.assertEqual(feed.updated_at, later)
        self.assertEqual(article.fetched_at, clock.now)
        self.assertEqual(article.updated_at, later)

    def test_bulk_marker_builders_reject_invalid_shapes(self) -> None:
        user = build_user()
        feed = build_feed()

        period = build_period_marker(user=user)
        feed_marker = build_feed_marker(user=user, feed=feed)

        self.assertEqual(period.scope, ReadScope.DAY)
        self.assertIsNone(period.feed)
        self.assertEqual(feed_marker.scope, ReadScope.FEED)
        self.assertEqual(feed_marker.feed, feed)
        self.assertIsNone(feed_marker.period_start)
        with self.assertRaisesRegex(ValueError, "require a feed"):
            build_bulk_marker(user=user, scope=ReadScope.FEED)
        with self.assertRaisesRegex(ValueError, "require dates"):
            build_bulk_marker(user=user, scope=ReadScope.DAY, feed=feed)
        with self.assertRaisesRegex(ValueError, "must not be after"):
            build_bulk_marker(
                user=user,
                scope=ReadScope.WEEK,
                period_start=FIXED_TODAY.replace(day=6),
                period_end=FIXED_TODAY,
            )
        with self.assertRaisesRegex(ValueError, "unsupported"):
            build_bulk_marker(user=user, scope="invalid")

        migration = import_module(
            "feeds.migrations.0008_enforce_bulk_marker_invariants"
        )
        day = date(2026, 1, 5)
        markers = [
            SimpleNamespace(
                pk=7,
                user_id=1,
                scope="day",
                feed_id=None,
                period_start=None,
                period_end=None,
            ),
            SimpleNamespace(
                pk=8,
                user_id=1,
                scope="feed",
                feed_id=3,
                period_start=None,
                period_end=None,
            ),
            SimpleNamespace(
                pk=9,
                user_id=1,
                scope="feed",
                feed_id=3,
                period_start=None,
                period_end=None,
            ),
            SimpleNamespace(
                pk=10,
                user_id=2,
                scope="week",
                feed_id=None,
                period_start=day,
                period_end=day,
            ),
        ]
        marker_state = [vars(marker).copy() for marker in markers]
        queryset = Mock()
        queryset.iterator.return_value = iter(markers)
        model = Mock()
        model.objects.order_by.return_value = queryset
        apps = Mock()
        apps.get_model.return_value = model
        with self.assertRaisesRegex(
            RuntimeError,
            r"invalid row IDs: 7; duplicate row IDs: 8, 9.*"
            r"no marker rows were modified",
        ):
            migration.audit_bulk_read_markers(apps, Mock())
        self.assertEqual([vars(marker) for marker in markers], marker_state)
        queryset.delete.assert_not_called()
        queryset.update.assert_not_called()
        model.objects.delete.assert_not_called()
        model.objects.update.assert_not_called()

    def test_local_feed_and_opml_fixtures_are_parseable(self) -> None:
        rss = cast(Any, feedparser.parse(fixture_bytes("rss.xml")))
        atom = cast(Any, feedparser.parse(fixture_bytes("atom.xml")))

        self.assertFalse(rss.bozo)
        self.assertEqual(rss.feed.title, "Fixture RSS")
        self.assertEqual(rss.entries[0].id, "rss-entry-1")
        self.assertEqual(rss.entries[0].link, "https://example.com/rss-article")
        self.assertFalse(atom.bozo)
        self.assertEqual(atom.feed.title, "Fixture Atom")
        self.assertEqual(atom.entries[0].id, "atom-entry-1")
        self.assertEqual(atom.entries[0].link, "https://example.com/atom-article")
        result = import_opml(fixture_bytes("subscriptions.opml"))
        feed = Feed.objects.select_related("category").get()
        self.assertEqual(result.created, 1)
        self.assertEqual(feed.title, "PyPI Blog")
        self.assertIsNotNone(feed.category)
        assert feed.category is not None
        self.assertEqual(feed.category.name, "Python")

    def test_postmark_and_sanitizer_fixtures_are_deterministic(self) -> None:
        payload = newsletter_payload(message_id="fixture-message")
        sanitized = sanitize_newsletter_html(fixture_text("sanitizer-attack.html"))

        self.assertEqual(payload["MessageID"], "fixture-message")
        self.assertEqual(payload["Subject"], "Daily newsletter")
        self.assertNotIn("<script", sanitized)
        self.assertNotIn("onerror", sanitized)
        self.assertNotIn("javascript:", sanitized)
        self.assertIn("https://example.com/story", sanitized)


class HttpResponseHelperTests(SimpleTestCase):
    def test_json_status_and_timeout_helpers_configure_request_mocks(self) -> None:
        request = Mock()
        response = configure_json_response(
            request,
            payload={"error": "unavailable"},
            status_code=503,
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": "unavailable"})
        with self.assertRaises(requests.HTTPError):
            response.raise_for_status()

        timeout_request = Mock()
        error = configure_request_timeout(timeout_request)
        with self.assertRaises(requests.Timeout) as caught:
            timeout_request()
        self.assertIs(caught.exception, error)
