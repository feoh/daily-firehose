"""Bounded reading windows and the cost of resolving read state."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from feeds.models import Article, BulkReadMarker, ReadScope
from feeds.queries import (
    article_card_page,
    digest_article_limit,
    visible_articles,
)

from .support.base import StaticFilesTestCase, model_id
from .support.builders import (
    build_api_token,
    build_article,
    build_bulk_marker,
    build_feed,
    build_read_state,
    build_saved_article,
    build_user,
    frozen_time,
)


def _set_fetched_at(article: Article, moment: datetime) -> Article:
    Article.objects.filter(pk=article.pk).update(fetched_at=moment)
    article.refresh_from_db()
    return article


def _set_marked_read_at(marker: BulkReadMarker, moment: datetime) -> BulkReadMarker:
    BulkReadMarker.objects.filter(pk=marker.pk).update(marked_read_at=moment)
    marker.refresh_from_db()
    return marker


class VisibilityFilterTests(TestCase):
    """The SQL visibility filter must obey every read-state rule."""

    def setUp(self) -> None:
        self.clock = self.enterContext(frozen_time())
        self.user = build_user()
        self.feed = build_feed()

    def _article(self, title: str) -> Article:
        return _set_fetched_at(
            build_article(feed=self.feed, title=title, published_at=self.clock.now),
            self.clock.now,
        )

    def _visible_titles(self) -> set[str]:
        return {
            article.title
            for article in visible_articles(self.user, Article.objects.all())
        }

    def test_explicitly_read_and_saved_articles_are_excluded(self) -> None:
        unread = self._article("unread")
        read = self._article("read")
        saved = self._article("saved")
        build_read_state(user=self.user, article=read, is_read=True)
        build_saved_article(user=self.user, article=saved)

        self.assertEqual(self._visible_titles(), {unread.title})

    def test_an_explicit_unread_overrides_a_covering_bulk_marker(self) -> None:
        covered = self._article("covered")
        rescued = self._article("rescued")
        build_read_state(user=self.user, article=rescued, is_read=False)
        _set_marked_read_at(
            build_bulk_marker(user=self.user, scope=ReadScope.FEED, feed=self.feed),
            self.clock.now + timedelta(minutes=1),
        )

        self.assertEqual(self._visible_titles(), {rescued.title})
        self.assertNotIn(covered.title, self._visible_titles())

    def test_a_marker_never_reaches_an_article_fetched_after_it(self) -> None:
        _set_marked_read_at(
            build_bulk_marker(user=self.user, scope=ReadScope.FEED, feed=self.feed),
            self.clock.now,
        )
        later = _set_fetched_at(
            build_article(feed=self.feed, title="later", published_at=self.clock.now),
            self.clock.now + timedelta(minutes=5),
        )

        self.assertEqual(self._visible_titles(), {later.title})

    def test_another_readers_state_does_not_hide_anything(self) -> None:
        article = self._article("mine")
        stranger = build_user(username="stranger")
        build_read_state(user=stranger, article=article, is_read=True)
        build_saved_article(user=stranger, article=article)

        self.assertEqual(self._visible_titles(), {article.title})

    def test_no_markers_means_nothing_is_bulk_read(self) -> None:
        first = self._article("first")
        second = self._article("second")

        self.assertEqual(self._visible_titles(), {first.title, second.title})


class BoundedPageTests(TestCase):
    """A limit must bound visible rows, never stand in for an empty window."""

    def setUp(self) -> None:
        self.clock = self.enterContext(frozen_time())
        self.user = build_user()
        self.feed = build_feed()

    def _articles(self, count: int, *, prefix: str) -> list[Article]:
        return [
            _set_fetched_at(
                build_article(
                    feed=self.feed,
                    title=f"{prefix}-{index}",
                    published_at=self.clock.now,
                ),
                self.clock.now,
            )
            for index in range(count)
        ]

    def _page(self, limit: int):
        return article_card_page(self.user, Article.objects.all(), limit=limit)

    def test_the_limit_counts_visible_articles_not_read_ones(self) -> None:
        """The regression that made a page of read articles look like an empty feed."""

        for article in self._articles(5, prefix="read"):
            build_read_state(user=self.user, article=article, is_read=True)
        unread = self._articles(2, prefix="unread")

        page = self._page(2)

        self.assertEqual(
            {card["article"].title for card in page.cards},
            {article.title for article in unread},
        )
        self.assertFalse(page.has_more)

    def test_has_more_is_exact_at_the_boundary(self) -> None:
        self._articles(2, prefix="visible")

        self.assertFalse(self._page(2).has_more)
        self.assertEqual(len(self._page(2).cards), 2)

        self._articles(1, prefix="extra")

        self.assertTrue(self._page(2).has_more)
        self.assertEqual(len(self._page(2).cards), 2)

    def test_a_limit_below_one_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self._page(0)

    def test_cost_does_not_grow_with_the_window(self) -> None:
        self._articles(3, prefix="small")
        with CaptureQueriesContext(connection) as small:
            self._page(digest_article_limit())

        self._articles(80, prefix="large")
        with CaptureQueriesContext(connection) as large:
            self._page(digest_article_limit())

        self.assertEqual(len(large.captured_queries), len(small.captured_queries))

    def test_the_page_never_hydrates_more_than_one_row_past_the_limit(self) -> None:
        self._articles(40, prefix="many")

        page = article_card_page(self.user, Article.objects.all(), limit=5)

        self.assertEqual(len(page.cards), 5)
        self.assertTrue(page.has_more)


class BoundedSurfaceTests(StaticFilesTestCase):
    """Both surfaces report the bound rather than presenting a truncated view."""

    def setUp(self) -> None:
        self.clock = self.enterContext(frozen_time())
        self.password = "bounded-pass-123"
        self.user = build_user(username="bounded-reader", password=self.password)
        self.feed = build_feed()
        self.client.force_login(self.user)

    def _articles(self, count: int) -> list[Article]:
        return [
            _set_fetched_at(
                build_article(
                    feed=self.feed, title=f"a-{index}", published_at=self.clock.now
                ),
                self.clock.now,
            )
            for index in range(count)
        ]

    def test_an_unbounded_window_does_not_announce_a_bound(self) -> None:
        self._articles(3)

        content = self.client.get(reverse("today")).content.decode()

        self.assertNotIn("Showing the first", content)

    @override_settings(DIGEST_ARTICLE_LIMIT=2)
    def test_a_bounded_digest_says_so(self) -> None:
        self._articles(3)

        response = self.client.get(reverse("today"))
        content = response.content.decode()

        self.assertIn("Showing the first 2", content)
        self.assertEqual(content.count("data-article-card"), 2)

    @override_settings(FEED_ARTICLE_LIMIT=1)
    def test_a_bounded_feed_page_says_so(self) -> None:
        self._articles(3)

        response = self.client.get(reverse("feed-detail", args=[model_id(self.feed)]))

        self.assertContains(response, "Showing the first 1")

    @override_settings(DIGEST_ARTICLE_LIMIT=2)
    def test_the_digest_json_reports_the_bound(self) -> None:
        self._articles(3)

        payload = json.loads(self.client.get(reverse("digest-json")).content)

        self.assertEqual(payload["limit"], 2)
        self.assertTrue(payload["has_more"])
        self.assertEqual(len(payload["articles"]), 2)

    @override_settings(FEED_ARTICLE_LIMIT=2)
    def test_feed_detail_shows_unread_that_sit_behind_read_articles(self) -> None:
        """`feed_detail` sliced before filtering, so a read page hid the rest.

        The bound is smaller than the number of read articles ahead of the unread
        one on purpose: under the old order this page rendered nothing at all.
        """

        for article in self._articles(3):
            build_read_state(user=self.user, article=article, is_read=True)
        buried = _set_fetched_at(
            build_article(
                feed=self.feed, title="buried-unread", published_at=self.clock.now
            ),
            self.clock.now - timedelta(minutes=1),
        )

        response = self.client.get(reverse("feed-detail", args=[model_id(self.feed)]))

        self.assertContains(response, buried.title)
        self.assertNotContains(response, "Showing the first")


class BoundedApiTests(TestCase):
    """The bearer digest bounds its rows and says whether it truncated."""

    def setUp(self) -> None:
        self.clock = self.enterContext(frozen_time())
        self.user = build_user(username="api-bounded")
        self.feed = build_feed()
        _, self.raw_token = build_api_token(user=self.user, capabilities=["read"])

    def _articles(self, count: int) -> list[Article]:
        return [
            _set_fetched_at(
                build_article(
                    feed=self.feed, title=f"api-{index}", published_at=self.clock.now
                ),
                self.clock.now,
            )
            for index in range(count)
        ]

    def _get(self, query: str = "") -> dict:
        response = self.client.get(
            f"/api/v1/articles/{query}",
            headers={"authorization": f"Bearer {self.raw_token}"},
        )
        self.assertEqual(response.status_code, 200)
        return json.loads(response.content)

    @override_settings(DIGEST_ARTICLE_LIMIT=2)
    def test_the_digest_reports_its_bound(self) -> None:
        self._articles(3)

        payload = self._get()

        self.assertEqual(payload["limit"], 2)
        self.assertTrue(payload["has_more"])
        self.assertEqual(len(payload["articles"]), 2)

    def test_read_articles_are_excluded_before_the_bound_is_applied(self) -> None:
        articles = self._articles(3)
        build_read_state(user=self.user, article=articles[0], is_read=True)

        payload = self._get()

        titles = {article["title"] for article in payload["articles"]}
        self.assertEqual(titles, {articles[1].title, articles[2].title})

    def test_include_read_returns_read_articles_marked_as_read(self) -> None:
        articles = self._articles(2)
        build_read_state(user=self.user, article=articles[0], is_read=True)

        payload = self._get("?include_read=true")

        by_title = {item["title"]: item for item in payload["articles"]}
        self.assertEqual(set(by_title), {articles[0].title, articles[1].title})
        self.assertTrue(by_title[articles[0].title]["is_read"])
        self.assertFalse(by_title[articles[1].title]["is_read"])

    def test_include_saved_returns_saved_articles_marked_as_saved(self) -> None:
        articles = self._articles(2)
        build_saved_article(user=self.user, article=articles[0])

        payload = self._get("?include_saved=true")

        by_title = {item["title"]: item for item in payload["articles"]}
        self.assertEqual(set(by_title), {articles[0].title, articles[1].title})
        self.assertTrue(by_title[articles[0].title]["is_saved"])
