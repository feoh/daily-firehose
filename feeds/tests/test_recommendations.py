from __future__ import annotations

from datetime import timedelta

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from feeds.models import Article
from feeds.recommendations import (
    ArticleDocument,
    SavedSignal,
    rank_recommendations,
    recommendation_cards,
)

from .support.base import StaticFilesTestCase, model_id
from .support.builders import (
    FIXED_NOW,
    build_article,
    build_category,
    build_feed,
    build_read_state,
    build_saved_article,
    build_user,
    frozen_time,
)


def document(
    article_id: int,
    *,
    title: str,
    summary: str,
    feed_id: int = 1,
    feed_title: str = "Example feed",
    category_id: int | None = 1,
    category_title: str = "Technology",
    url: str | None = None,
) -> ArticleDocument:
    return ArticleDocument(
        article_id=article_id,
        feed_id=feed_id,
        category_id=category_id,
        url=url or f"https://example.com/{article_id}",
        title=title,
        summary=summary,
        feed_title=feed_title,
        category_title=category_title,
    )


class RecommendationRankingTests(StaticFilesTestCase):
    def test_topical_matches_beat_unrelated_articles_across_feeds(self) -> None:
        documents = [
            document(
                1,
                title="Python type checking patterns",
                summary="mypy protocols typing static analysis for Python",
                feed_id=1,
            ),
            document(
                2,
                title="Reliable Django background jobs",
                summary="Python Django workers retries database transactions",
                feed_id=2,
            ),
            document(
                3,
                title="Advanced Python protocols",
                summary="typing protocols mypy and static analysis",
                feed_id=3,
            ),
            document(
                4,
                title="Premier league transfer table",
                summary="football scores clubs players and fixtures",
                feed_id=1,
            ),
            document(
                5,
                title="Sourdough hydration guide",
                summary="bread flour starter fermentation recipe",
                feed_id=4,
            ),
        ]

        ranked = rank_recommendations(
            documents,
            [
                SavedSignal(article_id=1, title=documents[0].title),
                SavedSignal(article_id=2, title=documents[1].title),
            ],
            limit=3,
        )

        self.assertEqual(ranked[0].article_id, 3)
        self.assertIn("Because you saved", ranked[0].reason)
        self.assertNotEqual(ranked[0].article_id, 4)

    def test_notes_and_interest_scores_are_accepted_as_profile_signals(self) -> None:
        documents = [
            document(1, title="Weekly notes", summary="general roundup"),
            document(2, title="Another roundup", summary="general roundup"),
            document(3, title="PostgreSQL internals", summary="database query planner"),
            document(4, title="Garden update", summary="tomatoes and flowers"),
        ]

        ranked = rank_recommendations(
            documents,
            [
                SavedSignal(
                    article_id=1,
                    title="Weekly notes",
                    notes="I value PostgreSQL database query planner internals",
                    interest_score=5,
                ),
                # Defensive handling for malformed legacy model values.
                SavedSignal(
                    article_id=2,
                    title="Another roundup",
                    interest_score=float("nan"),
                ),
            ],
            limit=2,
        )

        self.assertEqual(ranked[0].article_id, 3)

    def test_saved_articles_and_duplicate_urls_are_not_recommended(self) -> None:
        documents = [
            document(1, title="Saved Python", summary="python django"),
            document(
                2,
                title="Python copy one",
                summary="python django",
                url="https://example.com/same#first",
            ),
            document(
                3,
                title="Python copy two",
                summary="python django",
                url="https://example.com/same#second",
            ),
            document(4, title="Unrelated", summary="cooking"),
            document(
                5,
                title="Saved Python",
                summary="syndicated copy",
                url="https://mirror.example.com/saved-python",
            ),
        ]

        ranked = rank_recommendations(
            documents,
            [SavedSignal(article_id=1, title=documents[0].title)],
            limit=4,
        )

        ids = [result.article_id for result in ranked]
        self.assertNotIn(1, ids)
        self.assertNotIn(5, ids)
        self.assertEqual(len({result.article_id for result in ranked}), len(ranked))
        self.assertEqual(
            len([article_id for article_id in ids if article_id in {2, 3}]), 1
        )

    def test_empty_profile_is_cold_start_and_html_only_text_is_safe(self) -> None:
        self.assertEqual(
            rank_recommendations(
                [document(1, title="Candidate", summary="content")], [], limit=10
            ),
            [],
        )

        ranked = rank_recommendations(
            [
                document(1, title="<b></b>", summary="<p></p>"),
                document(2, title="<i></i>", summary="<div></div>"),
            ],
            [SavedSignal(article_id=1, title="<b></b>")],
            limit=10,
        )

        self.assertEqual([result.article_id for result in ranked], [2])
        self.assertEqual(ranked[0].reason, "Similar to articles you’ve saved.")

    def test_ranking_is_deterministic_and_has_no_date_input(self) -> None:
        documents = [
            document(1, title="Saved systems", summary="distributed systems"),
            document(2, title="Alpha systems", summary="distributed systems"),
            document(3, title="Beta systems", summary="distributed systems"),
        ]
        signal = [SavedSignal(article_id=1, title=documents[0].title)]

        first = rank_recommendations(documents, signal, limit=2)
        second = rank_recommendations(documents, signal, limit=2)

        self.assertEqual(first, second)
        self.assertEqual([result.article_id for result in first], [2, 3])
        self.assertNotIn("published_at", ArticleDocument.__dataclass_fields__)
        self.assertNotIn("fetched_at", ArticleDocument.__dataclass_fields__)

    def test_limit_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 1"):
            rank_recommendations([], [], limit=0)


@override_settings(RECOMMENDATION_ARTICLE_LIMIT=10, RECOMMENDATION_CACHE_SECONDS=60)
class RecommendationOrmTests(StaticFilesTestCase):
    def setUp(self) -> None:
        cache.clear()
        self.clock = self.enterContext(frozen_time(FIXED_NOW))
        self.user = build_user(username="recommendation-owner")
        self.category = build_category(name="Python", slug="python")
        self.feed = build_feed(title="Python engineering", category=self.category)
        self.saved = build_article(
            feed=self.feed,
            title="Saved Django reliability",
            summary="Python Django database retries and robust workers",
        )
        build_saved_article(user=self.user, article=self.saved)

    def tearDown(self) -> None:
        cache.clear()

    def test_all_dates_and_read_states_remain_eligible_with_accurate_flags(
        self,
    ) -> None:
        old_read = build_article(
            feed=self.feed,
            title="Old Django worker reliability",
            summary="Python Django database retries and robust workers",
            published_at=self.clock.now - timedelta(days=3_000),
        )
        Article.objects.filter(pk=old_read.pk).update(
            fetched_at=self.clock.now - timedelta(days=100)
        )
        build_read_state(user=self.user, article=old_read, is_read=True)
        build_article(
            feed=self.feed,
            title="New football scores",
            summary="clubs players fixtures and transfer table",
            published_at=self.clock.now,
        )

        page = recommendation_cards(self.user)

        self.assertEqual(model_id(page.cards[0]["article"]), model_id(old_read))
        self.assertTrue(page.cards[0]["is_read"])
        self.assertNotIn(
            model_id(self.saved),
            [model_id(card["article"]) for card in page.cards],
        )
        self.assertEqual(page.profile_size, 1)

    def test_another_users_saves_do_not_change_the_profile(self) -> None:
        python_candidate = build_article(
            feed=self.feed,
            title="Python typing patterns",
            summary="Python Django typing protocols",
        )
        cooking_feed = build_feed(title="Cooking")
        cooking_candidate = build_article(
            feed=cooking_feed,
            title="Sourdough starter guide",
            summary="bread flour fermentation starter",
        )
        stranger = build_user(username="recommendation-stranger")
        stranger_saved = build_article(
            feed=cooking_feed,
            title="Saved bread recipe",
            summary="sourdough bread flour fermentation starter",
        )
        build_saved_article(user=stranger, article=stranger_saved)

        own_page = recommendation_cards(self.user)
        stranger_page = recommendation_cards(stranger)

        self.assertEqual(
            model_id(own_page.cards[0]["article"]), model_id(python_candidate)
        )
        self.assertEqual(
            model_id(stranger_page.cards[0]["article"]), model_id(cooking_candidate)
        )

    def test_a_new_save_invalidates_the_profile_fingerprint(self) -> None:
        first = build_article(
            feed=self.feed,
            title="Python candidate",
            summary="Python Django database",
        )
        second = build_article(
            feed=self.feed,
            title="Another Python candidate",
            summary="Python Django database",
        )
        first_page = recommendation_cards(self.user)
        self.assertIn(
            model_id(first), [model_id(card["article"]) for card in first_page.cards]
        )

        build_saved_article(user=self.user, article=first)
        second_page = recommendation_cards(self.user)

        self.assertNotIn(
            model_id(first), [model_id(card["article"]) for card in second_page.cards]
        )
        self.assertIn(
            model_id(second), [model_id(card["article"]) for card in second_page.cards]
        )
        self.assertEqual(second_page.profile_size, 2)


@override_settings(RECOMMENDATION_ARTICLE_LIMIT=10, RECOMMENDATION_CACHE_SECONDS=60)
class RecommendedViewTests(StaticFilesTestCase):
    def setUp(self) -> None:
        cache.clear()
        self.user = build_user(username="recommended-view")
        self.client.force_login(self.user)

    def tearDown(self) -> None:
        cache.clear()

    def test_cold_start_explains_how_to_get_recommendations(self) -> None:
        response = self.client.get(reverse("recommended"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recommended")
        self.assertContains(response, "Save some articles")
        self.assertContains(response, "No personalized matches yet")
        self.assertContains(response, 'data-keyboard-nav="R"')
        self.assertNotContains(response, "Mark this period read")
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_view_renders_reason_and_escapes_saved_titles(self) -> None:
        feed = build_feed(title="Secure feed")
        saved = build_article(
            feed=feed,
            title="Trusted Python article",
            summary="Python Django security testing",
        )
        build_saved_article(
            user=self.user,
            article=saved,
            title='<script>alert("saved")</script> Trusted Python article',
        )
        candidate = build_article(
            feed=feed,
            title="Candidate Python security",
            summary="Python Django security testing",
        )

        response = self.client.get(reverse("recommended"))

        self.assertContains(response, candidate.title)
        self.assertContains(response, "Why this:")
        self.assertNotContains(response, "<script>", html=True)
        self.assertContains(response, "article age is not part of the score")
