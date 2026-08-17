from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from feeds.models import Article, BulkReadMarker, ReadScope
from feeds.queries import (
    _covering_markers,
    article_cards,
    articles_between,
    read_article_ids,
)

from .support.base import model_id
from .support.builders import (
    build_article,
    build_bulk_marker,
    build_feed,
    build_read_state,
    build_user,
    frozen_time,
)


def _set_fetched_at(article: Article, moment: datetime) -> Article:
    """`fetched_at` is auto_now_add, so it can only be pinned after creation."""

    Article.objects.filter(pk=article.pk).update(fetched_at=moment)
    article.refresh_from_db()
    return article


def _set_marked_read_at(marker: BulkReadMarker, moment: datetime) -> BulkReadMarker:
    BulkReadMarker.objects.filter(pk=marker.pk).update(marked_read_at=moment)
    marker.refresh_from_db()
    return marker


class ReadStatePolicyTests(TestCase):
    """The rules deciding whether an article counts as read."""

    def setUp(self) -> None:
        self.clock = self.enterContext(frozen_time())
        self.user = build_user()
        self.feed = build_feed()
        self.other_feed = build_feed()

    def _article(self, *, feed=None, fetched_at: datetime | None = None) -> Article:
        article = build_article(feed=feed or self.feed, published_at=self.clock.now)
        return _set_fetched_at(article, fetched_at or self.clock.now)

    def _read_ids(self) -> set[int]:
        return read_article_ids(self.user, Article.objects.all())

    def test_feed_marker_marks_only_its_own_feeds_articles(self) -> None:
        mine = self._article(feed=self.feed)
        theirs = self._article(feed=self.other_feed)
        _set_marked_read_at(
            build_bulk_marker(
                user=self.user, scope=ReadScope.FEED, feed=self.feed
            ),
            self.clock.now + timedelta(minutes=1),
        )

        read_ids = self._read_ids()

        self.assertIn(model_id(mine), read_ids)
        self.assertNotIn(model_id(theirs), read_ids)

    def test_period_marker_marks_only_articles_inside_its_local_dates(self) -> None:
        today = self._article(fetched_at=self.clock.now)
        yesterday = self._article(fetched_at=self.clock.now - timedelta(days=1))
        _set_marked_read_at(
            build_bulk_marker(
                user=self.user,
                scope=ReadScope.DAY,
                period_start=self.clock.today,
                period_end=self.clock.today,
            ),
            self.clock.now + timedelta(minutes=1),
        )

        read_ids = self._read_ids()

        self.assertIn(model_id(today), read_ids)
        self.assertNotIn(model_id(yesterday), read_ids)

    def test_marker_never_reaches_an_article_fetched_after_it_was_set(self) -> None:
        """The fence that keeps newly arrived articles unread after a bulk mark."""

        marked_read_at = self.clock.now
        before = self._article(fetched_at=marked_read_at - timedelta(seconds=1))
        after = self._article(fetched_at=marked_read_at + timedelta(seconds=1))
        _set_marked_read_at(
            build_bulk_marker(
                user=self.user,
                scope=ReadScope.DAY,
                period_start=self.clock.today,
                period_end=self.clock.today,
            ),
            marked_read_at,
        )

        read_ids = self._read_ids()

        self.assertIn(model_id(before), read_ids)
        self.assertNotIn(model_id(after), read_ids)

    def test_an_article_fetched_exactly_at_the_mark_is_covered(self) -> None:
        marked_read_at = self.clock.now
        boundary = self._article(fetched_at=marked_read_at)
        _set_marked_read_at(
            build_bulk_marker(
                user=self.user,
                scope=ReadScope.DAY,
                period_start=self.clock.today,
                period_end=self.clock.today,
            ),
            marked_read_at,
        )

        self.assertIn(model_id(boundary), self._read_ids())

    def test_explicit_unread_overrides_a_covering_marker(self) -> None:
        article = self._article()
        _set_marked_read_at(
            build_bulk_marker(
                user=self.user, scope=ReadScope.FEED, feed=self.feed
            ),
            self.clock.now + timedelta(minutes=1),
        )
        build_read_state(user=self.user, article=article, is_read=False)

        self.assertNotIn(model_id(article), self._read_ids())

    def test_explicit_read_alone_marks_an_article_read(self) -> None:
        article = self._article()
        build_read_state(user=self.user, article=article, is_read=True)

        self.assertIn(model_id(article), self._read_ids())

    def test_another_readers_marker_never_applies(self) -> None:
        article = self._article()
        stranger = build_user()
        _set_marked_read_at(
            build_bulk_marker(
                user=stranger, scope=ReadScope.FEED, feed=self.feed
            ),
            self.clock.now + timedelta(minutes=1),
        )

        self.assertNotIn(model_id(article), self._read_ids())

    def test_an_empty_article_set_resolves_without_touching_the_database(self) -> None:
        with self.assertNumQueries(0):
            self.assertEqual(
                read_article_ids(self.user, Article.objects.none()), set()
            )


class MarkerNarrowingTests(TestCase):
    """Only markers that could cover the articles on screen are loaded."""

    def setUp(self) -> None:
        self.clock = self.enterContext(frozen_time())
        self.user = build_user()
        self.feed = build_feed()
        self.article = _set_fetched_at(
            build_article(feed=self.feed, published_at=self.clock.now),
            self.clock.now,
        )
        self.article_ids = [model_id(self.article)]

    def _loaded(self) -> list[BulkReadMarker]:
        return _covering_markers(
            self.user, Article.objects.filter(id__in=self.article_ids)
        )

    def test_a_marker_set_before_every_article_is_excluded(self) -> None:
        _set_marked_read_at(
            build_bulk_marker(
                user=self.user, scope=ReadScope.FEED, feed=self.feed
            ),
            self.clock.now - timedelta(days=1),
        )

        self.assertEqual(self._loaded(), [])

    def test_a_feed_marker_for_an_absent_feed_is_excluded(self) -> None:
        _set_marked_read_at(
            build_bulk_marker(
                user=self.user, scope=ReadScope.FEED, feed=build_feed()
            ),
            self.clock.now + timedelta(minutes=1),
        )

        self.assertEqual(self._loaded(), [])

    def test_a_period_marker_covering_other_dates_is_excluded(self) -> None:
        _set_marked_read_at(
            build_bulk_marker(
                user=self.user,
                scope=ReadScope.MONTH,
                period_start=self.clock.today - timedelta(days=60),
                period_end=self.clock.today - timedelta(days=31),
            ),
            self.clock.now + timedelta(minutes=1),
        )

        self.assertEqual(self._loaded(), [])

    def test_applicable_feed_and_period_markers_are_loaded(self) -> None:
        feed_marker = _set_marked_read_at(
            build_bulk_marker(
                user=self.user, scope=ReadScope.FEED, feed=self.feed
            ),
            self.clock.now + timedelta(minutes=1),
        )
        period_marker = _set_marked_read_at(
            build_bulk_marker(
                user=self.user,
                scope=ReadScope.DAY,
                period_start=self.clock.today,
                period_end=self.clock.today,
            ),
            self.clock.now + timedelta(minutes=1),
        )

        loaded = {marker.pk for marker in self._loaded()}

        self.assertEqual(loaded, {feed_marker.pk, period_marker.pk})

    def test_history_of_irrelevant_markers_does_not_enlarge_the_loaded_set(
        self,
    ) -> None:
        """The property that keeps marker work proportional to the window shown."""

        relevant = _set_marked_read_at(
            build_bulk_marker(
                user=self.user, scope=ReadScope.FEED, feed=self.feed
            ),
            self.clock.now + timedelta(minutes=1),
        )
        for offset in range(1, 41):
            day = self.clock.today - timedelta(days=offset + 1)
            _set_marked_read_at(
                build_bulk_marker(
                    user=self.user,
                    scope=ReadScope.DAY,
                    period_start=day,
                    period_end=day,
                ),
                self.clock.now + timedelta(minutes=1),
            )

        self.assertEqual(BulkReadMarker.objects.count(), 41)
        self.assertEqual([marker.pk for marker in self._loaded()], [relevant.pk])


class ReadStateQueryCostTests(TestCase):
    """Resolving a digest costs a fixed number of queries."""

    def setUp(self) -> None:
        self.clock = self.enterContext(frozen_time())
        self.user = build_user()
        self.feed = build_feed()
        self._stale_days = 0

    def _build_articles(self, count: int) -> None:
        for _ in range(count):
            _set_fetched_at(
                build_article(feed=self.feed, published_at=self.clock.now),
                self.clock.now,
            )

    def _add_stale_markers(self, count: int) -> None:
        for _ in range(count):
            self._stale_days += 1
            day = self.clock.today - timedelta(days=self._stale_days + 1)
            _set_marked_read_at(
                build_bulk_marker(
                    user=self.user,
                    scope=ReadScope.DAY,
                    period_start=day,
                    period_end=day,
                ),
                self.clock.now + timedelta(minutes=1),
            )

    def _resolve_digest(self) -> list[dict[str, Any]]:
        return article_cards(
            self.user, articles_between(self.clock.today, self.clock.today)
        )

    def _query_count(self) -> int:
        with CaptureQueriesContext(connection) as captured:
            self._resolve_digest()
        return len(captured.captured_queries)

    def test_cost_does_not_grow_with_the_number_of_articles(self) -> None:
        self._build_articles(3)
        baseline = self._query_count()

        self._build_articles(60)

        self.assertEqual(self._query_count(), baseline)

    def test_cost_does_not_grow_with_the_readers_marker_history(self) -> None:
        self._build_articles(5)
        self._add_stale_markers(2)
        baseline = self._query_count()

        self._add_stale_markers(50)

        self.assertEqual(self._query_count(), baseline)

    def test_an_applicable_marker_costs_no_extra_query(self) -> None:
        """Coverage is a predicate inside the visibility query, not a follow-up."""

        self._build_articles(3)
        self._add_stale_markers(5)
        without_markers = self._query_count()

        _set_marked_read_at(
            build_bulk_marker(
                user=self.user, scope=ReadScope.FEED, feed=self.feed
            ),
            self.clock.now + timedelta(minutes=1),
        )

        self.assertEqual(self._query_count(), without_markers)


class SurfaceAgreementTests(TestCase):
    """The HTML and JSON surfaces resolve read state through the same policy."""

    def setUp(self) -> None:
        self.clock = self.enterContext(frozen_time())
        self.user = build_user()
        self.feed = build_feed()
        self.visible = _set_fetched_at(
            build_article(feed=self.feed, published_at=self.clock.now), self.clock.now
        )
        self.bulk_read = _set_fetched_at(
            build_article(feed=self.feed, published_at=self.clock.now), self.clock.now
        )
        _set_marked_read_at(
            build_bulk_marker(
                user=self.user,
                scope=ReadScope.DAY,
                period_start=self.clock.today,
                period_end=self.clock.today,
            ),
            self.clock.now + timedelta(minutes=1),
        )
        build_read_state(user=self.user, article=self.visible, is_read=False)

    def test_both_surfaces_hide_the_same_bulk_read_article(self) -> None:
        cards = article_cards(
            self.user, articles_between(self.clock.today, self.clock.today)
        )

        self.client.force_login(self.user)
        payload = self.client.get("/api/digest/today.json").json()

        card_ids = {card["article"].id for card in cards}
        payload_ids = {article["id"] for article in payload["articles"]}

        self.assertEqual(card_ids, payload_ids)
        self.assertEqual(card_ids, {model_id(self.visible)})
