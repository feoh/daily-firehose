from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from typing import Any
from unittest.mock import Mock, patch

from django.test import override_settings
from django.urls import reverse

from ..models import Article, ArticleReadState, BulkReadMarker, ReadScope, SavedArticle
from .support.base import StaticFilesTestCase, model_id
from .support.builders import (
    FIXED_NOW,
    build_api_token,
    build_article,
    build_feed,
    build_user,
    frozen_time,
)
from .support.http_responses import configure_linkding_response


@override_settings(LINKDING_TOKEN="state-matrix-token")
class ArticleStatePropagationTests(StaticFilesTestCase):
    """Cross-view contract for per-user read and saved state transitions."""

    def setUp(self) -> None:
        self.clock = self.enterContext(frozen_time(FIXED_NOW))
        self.user = build_user(username="state-owner")
        self.other_user = build_user(username="state-observer")
        self.user_token, self.user_key = build_api_token(user=self.user)
        self.other_token, self.other_key = build_api_token(user=self.other_user)
        self.feed = build_feed(title="A state matrix feed")

        # Fetched and published recency deliberately disagree. Digest surfaces use
        # fetched_at, while feed detail uses Article.Meta's published_at ordering.
        self.target = build_article(
            feed=self.feed,
            title="A targeted article",
            url="https://example.com/state-target",
            guid="state-target",
            summary="Targeted state transition article.",
            published_at=self.clock.now - timedelta(minutes=2),
        )
        self.survivor_one = build_article(
            feed=self.feed,
            title="B unrelated survivor",
            url="https://example.com/state-survivor-one",
            guid="state-survivor-one",
            summary="First unrelated survivor.",
            published_at=self.clock.now - timedelta(minutes=3),
        )
        self.survivor_two = build_article(
            feed=self.feed,
            title="C unrelated survivor",
            url="https://example.com/state-survivor-two",
            guid="state-survivor-two",
            summary="Second unrelated survivor.",
            published_at=self.clock.now - timedelta(minutes=1),
        )
        Article.objects.filter(pk=model_id(self.target)).update(
            fetched_at=self.clock.now - timedelta(minutes=1)
        )
        Article.objects.filter(pk=model_id(self.survivor_one)).update(
            fetched_at=self.clock.now - timedelta(minutes=2)
        )
        Article.objects.filter(pk=model_id(self.survivor_two)).update(
            fetched_at=self.clock.now - timedelta(minutes=3)
        )

        self.digest_order = [
            model_id(self.target),
            model_id(self.survivor_one),
            model_id(self.survivor_two),
        ]
        self.feed_order = [
            model_id(self.survivor_two),
            model_id(self.target),
            model_id(self.survivor_one),
        ]
        self.survivor_digest_order = [
            model_id(self.survivor_one),
            model_id(self.survivor_two),
        ]
        self.survivor_feed_order = [
            model_id(self.survivor_two),
            model_id(self.survivor_one),
        ]

    @staticmethod
    def _ids(cards: Iterable[dict[str, Any]]) -> list[int]:
        return [model_id(card["article"]) for card in cards]

    def _html_ids(self, route: str, *args: int) -> list[int]:
        response = self.client.get(reverse(route, args=args))
        self.assertEqual(response.status_code, 200)
        return self._ids(response.context["cards"])

    @staticmethod
    def _bearer_headers(key: str) -> dict[str, str]:
        return {"authorization": f"Bearer {key}"}

    def _api_article_ids(
        self, route: str, *, key: str, query: dict[str, str] | None = None
    ) -> list[int]:
        response = self.client.get(
            reverse(route), query or {}, headers=self._bearer_headers(key)
        )
        self.assertEqual(response.status_code, 200)
        return [article["id"] for article in response.json()["articles"]]

    def _assert_surface_state(
        self,
        *,
        user: Any,
        key: str,
        unread_ids: list[int],
        archived_ids: list[int],
        saved_ids: list[int],
        expected_flags: dict[int, tuple[bool, bool]],
    ) -> None:
        """Assert each surface's own ordering contract, not a shared false proxy."""

        visible = set(unread_ids)
        expected_digest = [pk for pk in self.digest_order if pk in visible]
        expected_feed = [pk for pk in self.feed_order if pk in visible]
        self.assertEqual(unread_ids, expected_digest)

        self.client.force_login(user)
        for route in ("today", "week", "month"):
            with self.subTest(user=user.username, surface=route):
                self.assertEqual(self._html_ids(route), expected_digest)
        self.assertEqual(
            self._html_ids("feed-detail", model_id(self.feed)), expected_feed
        )
        self.assertEqual(self._html_ids("archived"), archived_ids)
        self.assertEqual(self._html_ids("saved-links"), saved_ids)

        digest = self.client.get(reverse("digest-json"))
        self.assertEqual(digest.status_code, 200)
        self.assertEqual(
            [article["id"] for article in digest.json()["articles"]], expected_digest
        )
        self.assertEqual(
            self._api_article_ids("api-morning-briefing", key=key), expected_digest
        )
        self.assertEqual(
            self._api_article_ids("api-articles", key=key), expected_digest
        )

        complete = self.client.get(
            reverse("api-articles"),
            {"include_read": "true", "include_saved": "true"},
            headers=self._bearer_headers(key),
        )
        self.assertEqual(complete.status_code, 200)
        complete_articles = complete.json()["articles"]
        self.assertEqual(
            [article["id"] for article in complete_articles], self.digest_order
        )
        self.assertEqual(
            {
                article["id"]: (article["is_read"], article["is_saved"])
                for article in complete_articles
            },
            expected_flags,
        )

    def _baseline_flags(self) -> dict[int, tuple[bool, bool]]:
        return dict.fromkeys(self.digest_order, (False, False))

    def _assert_other_user_unchanged(self) -> None:
        self._assert_surface_state(
            user=self.other_user,
            key=self.other_key,
            unread_ids=self.digest_order,
            archived_ids=[],
            saved_ids=[],
            expected_flags=self._baseline_flags(),
        )

    def _assert_owner_baseline(self) -> None:
        self._assert_surface_state(
            user=self.user,
            key=self.user_key,
            unread_ids=self.digest_order,
            archived_ids=[],
            saved_ids=[],
            expected_flags=self._baseline_flags(),
        )

    def test_browser_mark_read_and_unread_propagate_across_every_surface(self) -> None:
        self._assert_owner_baseline()
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("mark-article", args=[model_id(self.target)]),
            {"state": "read"},
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["remove"])
        self._assert_surface_state(
            user=self.user,
            key=self.user_key,
            unread_ids=self.survivor_digest_order,
            archived_ids=[model_id(self.target)],
            saved_ids=[],
            expected_flags={
                model_id(self.target): (True, False),
                model_id(self.survivor_one): (False, False),
                model_id(self.survivor_two): (False, False),
            },
        )
        self.assertEqual(
            self._html_ids("feed-detail", model_id(self.feed)),
            self.survivor_feed_order,
        )
        self._assert_other_user_unchanged()

        self.client.force_login(self.user)
        response = self.client.post(
            reverse("mark-article", args=[model_id(self.target)]),
            {"state": "unread", "remove_on_success": "true"},
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["remove"])
        self._assert_owner_baseline()
        self._assert_other_user_unchanged()

    @patch("feeds.services.requests.post")
    def test_browser_save_and_api_unsave_propagate_across_every_surface(
        self, mock_post: Mock
    ) -> None:
        configure_linkding_response(mock_post, article=self.target)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("save-article", args=[model_id(self.target)]),
            {
                "article_id": str(model_id(self.target)),
                "article_url": self.target.url,
            },
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["level"], "success")
        self.assertTrue(response.json()["remove"])
        saved = SavedArticle.objects.get(user=self.user, article=self.target)
        saved_snapshot = (self.target.title, self.target.url)
        self.assertEqual((saved.title, saved.url), saved_snapshot)
        self.assertTrue(saved.linkding_saved)
        self.target.title = "Article title changed after saving"
        self.target.url = "https://example.com/changed-after-saving"
        self.target.save(update_fields=["title", "url"])
        saved_response = self.client.get(reverse("saved-links"))
        self.assertContains(saved_response, saved_snapshot[0])
        self.assertContains(saved_response, f'href="{saved_snapshot[1]}"')
        self.assertContains(saved_response, "Linkding confirmed")
        self.assertNotContains(saved_response, self.target.title)
        self._assert_surface_state(
            user=self.user,
            key=self.user_key,
            unread_ids=self.survivor_digest_order,
            archived_ids=[],
            saved_ids=[model_id(self.target)],
            expected_flags={
                model_id(self.target): (False, True),
                model_id(self.survivor_one): (False, False),
                model_id(self.survivor_two): (False, False),
            },
        )
        self.assertEqual(
            self._html_ids("feed-detail", model_id(self.feed)),
            self.survivor_feed_order,
        )
        self._assert_other_user_unchanged()

        response = self.client.delete(
            reverse("api-article-saved", args=[model_id(self.target)]),
            headers=self._bearer_headers(self.user_key),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["article"]["is_saved"])
        self._assert_owner_baseline()
        self._assert_other_user_unchanged()
        mock_post.assert_called_once()

    @patch("feeds.services.requests.post")
    def test_unconfirmed_linkding_save_warns_then_uses_local_saved_state(
        self, mock_post: Mock
    ) -> None:
        configure_linkding_response(
            mock_post,
            article=self.target,
            url="https://example.com/different-bookmark",
            title="Different bookmark",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("save-article", args=[model_id(self.target)]),
            {
                "article_id": str(model_id(self.target)),
                "article_url": self.target.url,
            },
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["level"], "warning")
        self.assertFalse(response.json()["remove"])
        saved = SavedArticle.objects.get(user=self.user, article=self.target)
        self.assertFalse(saved.linkding_saved)
        self.assertIn("different bookmark URL", saved.linkding_error)

        self._assert_surface_state(
            user=self.user,
            key=self.user_key,
            unread_ids=self.survivor_digest_order,
            archived_ids=[],
            saved_ids=[model_id(self.target)],
            expected_flags={
                model_id(self.target): (False, True),
                model_id(self.survivor_one): (False, False),
                model_id(self.survivor_two): (False, False),
            },
        )
        saved_response = self.client.get(reverse("saved-links"))
        self.assertContains(saved_response, "Linkding failed")
        self._assert_other_user_unchanged()

    @patch("feeds.services.requests.post")
    def test_bearer_read_save_unread_unsave_transitions_match_browser_views(
        self, mock_post: Mock
    ) -> None:
        configure_linkding_response(mock_post, article=self.target)
        headers = self._bearer_headers(self.user_key)

        response = self.client.post(
            reverse("api-article-read", args=[model_id(self.target)]),
            data={"is_read": True},
            content_type="application/json",
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["article"]["is_read"])
        self._assert_surface_state(
            user=self.user,
            key=self.user_key,
            unread_ids=self.survivor_digest_order,
            archived_ids=[model_id(self.target)],
            saved_ids=[],
            expected_flags={
                model_id(self.target): (True, False),
                model_id(self.survivor_one): (False, False),
                model_id(self.survivor_two): (False, False),
            },
        )

        response = self.client.patch(
            reverse("api-article-read", args=[model_id(self.target)]),
            data={"is_read": False},
            content_type="application/json",
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["article"]["is_read"])
        self._assert_owner_baseline()

        response = self.client.post(
            reverse("api-article-saved", args=[model_id(self.target)]),
            data={"is_saved": True},
            content_type="application/json",
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["article"]["is_saved"])
        self._assert_surface_state(
            user=self.user,
            key=self.user_key,
            unread_ids=self.survivor_digest_order,
            archived_ids=[],
            saved_ids=[model_id(self.target)],
            expected_flags={
                model_id(self.target): (False, True),
                model_id(self.survivor_one): (False, False),
                model_id(self.survivor_two): (False, False),
            },
        )

        response = self.client.delete(
            reverse("api-article-saved", args=[model_id(self.target)]),
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        self._assert_owner_baseline()
        self._assert_other_user_unchanged()
        mock_post.assert_called_once()

    def _set_read_recency(self, ordered_articles: list[Article]) -> None:
        for index, article in enumerate(ordered_articles):
            ArticleReadState.objects.filter(user=self.user, article=article).update(
                updated_at=self.clock.now - timedelta(seconds=index)
            )

    def test_explicit_unread_and_saved_state_override_bulk_read_marker(self) -> None:
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("mark-period-read"),
            {
                "scope": ReadScope.DAY,
                "period_start": self.clock.today.isoformat(),
                "period_end": self.clock.today.isoformat(),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            BulkReadMarker.objects.filter(user=self.user, scope=ReadScope.DAY).exists()
        )
        self._set_read_recency([self.target, self.survivor_one, self.survivor_two])
        self._assert_surface_state(
            user=self.user,
            key=self.user_key,
            unread_ids=[],
            archived_ids=self.digest_order,
            saved_ids=[],
            expected_flags=dict.fromkeys(self.digest_order, (True, False)),
        )

        saved = SavedArticle.objects.create(
            user=self.user,
            article=self.target,
            url=self.target.url,
            title=self.target.title,
            feed=self.feed,
            linkding_saved=False,
            linkding_error="Delivery pending.",
        )
        self._assert_surface_state(
            user=self.user,
            key=self.user_key,
            unread_ids=[],
            archived_ids=self.digest_order,
            saved_ids=[model_id(self.target)],
            expected_flags={
                model_id(self.target): (True, True),
                model_id(self.survivor_one): (True, False),
                model_id(self.survivor_two): (True, False),
            },
        )

        ArticleReadState.objects.update_or_create(
            user=self.user, article=self.target, defaults={"is_read": False}
        )
        self._assert_surface_state(
            user=self.user,
            key=self.user_key,
            unread_ids=[],
            archived_ids=[
                model_id(self.survivor_one),
                model_id(self.survivor_two),
            ],
            saved_ids=[model_id(self.target)],
            expected_flags={
                model_id(self.target): (False, True),
                model_id(self.survivor_one): (True, False),
                model_id(self.survivor_two): (True, False),
            },
        )

        saved.delete()
        self._assert_surface_state(
            user=self.user,
            key=self.user_key,
            unread_ids=[model_id(self.target)],
            archived_ids=[
                model_id(self.survivor_one),
                model_id(self.survivor_two),
            ],
            saved_ids=[],
            expected_flags={
                model_id(self.target): (False, False),
                model_id(self.survivor_one): (True, False),
                model_id(self.survivor_two): (True, False),
            },
        )
        self._assert_other_user_unchanged()

    def test_saved_and_archived_views_use_recency_not_digest_order(self) -> None:
        archived_order = [self.survivor_two, self.target, self.survivor_one]
        for article_id in self.digest_order:
            ArticleReadState.objects.create(
                user=self.user,
                article_id=article_id,
                is_read=True,
            )
        self._set_read_recency(archived_order)

        saved_records = {
            model_id(article): SavedArticle.objects.create(
                user=self.user,
                article=article,
                url=article.url,
                title=article.title,
                feed=self.feed,
                linkding_saved=True,
            )
            for article in (self.target, self.survivor_one, self.survivor_two)
        }
        saved_article_order = [self.survivor_one, self.survivor_two, self.target]
        for index, article in enumerate(saved_article_order):
            SavedArticle.objects.filter(
                pk=model_id(saved_records[model_id(article)])
            ).update(saved_at=self.clock.now - timedelta(seconds=index))

        self._assert_surface_state(
            user=self.user,
            key=self.user_key,
            unread_ids=[],
            archived_ids=[model_id(article) for article in archived_order],
            saved_ids=[model_id(article) for article in saved_article_order],
            expected_flags=dict.fromkeys(self.digest_order, (True, True)),
        )
        self._assert_other_user_unchanged()

    def _build_unaffected_feed_article(self) -> tuple[Any, Article]:
        other_feed = build_feed(title="Z unaffected feed")
        other_article = build_article(
            feed=other_feed,
            title="Unaffected other-feed article",
            published_at=self.clock.now - timedelta(minutes=4),
        )
        Article.objects.filter(pk=model_id(other_article)).update(
            fetched_at=self.clock.now - timedelta(minutes=4)
        )
        return other_feed, other_article

    def _assert_feed_wide_propagation(
        self, *, other_feed: Any, other_article: Article
    ) -> None:
        self.assertCountEqual(
            ArticleReadState.objects.filter(
                user=self.user, article__feed=self.feed, is_read=True
            ).values_list("article_id", flat=True),
            self.digest_order,
        )
        # Bulk writes intentionally share one timestamp. Assign distinct recency
        # only after attesting the exact mutated IDs so Archived's -updated_at
        # ordering contract is deterministic rather than database tie-dependent.
        self._set_read_recency([self.target, self.survivor_one, self.survivor_two])
        other_id = model_id(other_article)
        global_digest_order = [*self.digest_order, other_id]

        marker = BulkReadMarker.objects.get(
            user=self.user, scope=ReadScope.FEED, feed=self.feed
        )
        self.assertIsNone(marker.period_start)
        self.assertIsNone(marker.period_end)
        self.assertFalse(
            BulkReadMarker.objects.filter(
                user=self.user, scope=ReadScope.FEED, feed=other_feed
            ).exists()
        )
        self.assertFalse(
            ArticleReadState.objects.filter(
                user=self.user, article=other_article
            ).exists()
        )
        self.assertFalse(BulkReadMarker.objects.filter(user=self.other_user).exists())
        self.assertFalse(
            ArticleReadState.objects.filter(
                user=self.other_user, article__feed=self.feed
            ).exists()
        )

        self.client.force_login(self.user)
        for route in ("today", "week", "month"):
            self.assertEqual(self._html_ids(route), [other_id])
        self.assertEqual(self._html_ids("feed-detail", model_id(self.feed)), [])
        self.assertEqual(
            self._html_ids("feed-detail", model_id(other_feed)), [other_id]
        )
        self.assertEqual(self._html_ids("archived"), self.digest_order)
        self.assertEqual(self._html_ids("saved-links"), [])
        digest = self.client.get(reverse("digest-json"))
        self.assertEqual(
            [article["id"] for article in digest.json()["articles"]], [other_id]
        )
        self.assertEqual(
            self._api_article_ids("api-morning-briefing", key=self.user_key),
            [other_id],
        )
        self.assertEqual(
            self._api_article_ids("api-articles", key=self.user_key), [other_id]
        )
        complete = self.client.get(
            reverse("api-articles"),
            {"include_read": "true", "include_saved": "true"},
            headers=self._bearer_headers(self.user_key),
        ).json()["articles"]
        self.assertEqual([article["id"] for article in complete], global_digest_order)
        self.assertEqual(
            {
                article["id"]: (article["is_read"], article["is_saved"])
                for article in complete
            },
            {
                **dict.fromkeys(self.digest_order, (True, False)),
                other_id: (False, False),
            },
        )

        self.client.force_login(self.other_user)
        for route in ("today", "week", "month"):
            self.assertEqual(self._html_ids(route), global_digest_order)
        self.assertEqual(
            self._html_ids("feed-detail", model_id(self.feed)), self.feed_order
        )
        self.assertEqual(
            self._html_ids("feed-detail", model_id(other_feed)), [other_id]
        )
        self.assertEqual(self._html_ids("archived"), [])
        self.assertEqual(self._html_ids("saved-links"), [])
        other_digest = self.client.get(reverse("digest-json"))
        self.assertEqual(
            [article["id"] for article in other_digest.json()["articles"]],
            global_digest_order,
        )
        self.assertEqual(
            self._api_article_ids("api-morning-briefing", key=self.other_key),
            global_digest_order,
        )
        self.assertEqual(
            self._api_article_ids("api-articles", key=self.other_key),
            global_digest_order,
        )
        other_complete = self.client.get(
            reverse("api-articles"),
            {"include_read": "true", "include_saved": "true"},
            headers=self._bearer_headers(self.other_key),
        ).json()["articles"]
        self.assertEqual(
            [article["id"] for article in other_complete], global_digest_order
        )
        self.assertEqual(
            {
                article["id"]: (article["is_read"], article["is_saved"])
                for article in other_complete
            },
            dict.fromkeys(global_digest_order, (False, False)),
        )

    def test_browser_mark_feed_read_propagates_without_cross_feed_or_user_leak(
        self,
    ) -> None:
        other_feed, other_article = self._build_unaffected_feed_article()
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("mark-feed-read", args=[model_id(self.feed)])
        )

        self.assertEqual(response.status_code, 302)
        self._assert_feed_wide_propagation(
            other_feed=other_feed, other_article=other_article
        )

    def test_bearer_mark_feed_read_propagates_without_cross_feed_or_user_leak(
        self,
    ) -> None:
        other_feed, other_article = self._build_unaffected_feed_article()

        response = self.client.post(
            reverse("api-feed-mark-read", args=[model_id(self.feed)]),
            headers=self._bearer_headers(self.user_key),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["marked_read"]["scope"], ReadScope.FEED)
        self._assert_feed_wide_propagation(
            other_feed=other_feed, other_article=other_article
        )
