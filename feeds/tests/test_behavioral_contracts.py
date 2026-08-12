from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest import expectedFailure
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from ..feed_fetch import FetchedFeedDocument
from ..models import (
    Article,
    ArticleReadState,
    Category,
    Feed,
    NewsletterIssue,
    ReadScope,
    SavedArticle,
    UserPreference,
)
from ..services import (
    NEWSLETTER_FEED_TITLE,
    NEWSLETTER_FEED_URL,
    RefreshResult,
    export_opml,
    import_opml,
    import_postmark_newsletter,
    refresh_active_feeds,
    refresh_feed,
    save_article,
)
from .support.base import StaticFilesTestCase, model_id
from .support.builders import (
    build_api_token,
    build_article,
    build_category,
    build_feed,
    build_user,
    frozen_time,
    newsletter_payload,
)


class ReadingWindowContractTests(StaticFilesTestCase):
    def setUp(self) -> None:
        self.user = build_user()
        self.feed = build_feed(title="Boundary feed")
        self.client.force_login(self.user)

    def _article_seen_at(self, title: str, moment: datetime) -> Article:
        article = build_article(feed=self.feed, title=title, published_at=moment)
        Article.objects.filter(pk=article.pk).update(fetched_at=moment)
        article.refresh_from_db()
        return article

    def _card_titles(self, route: str) -> list[str]:
        response = self.client.get(reverse(route))
        self.assertEqual(response.status_code, 200)
        return [card["article"].title for card in response.context["cards"]]

    def test_week_is_inclusive_monday_through_sunday_across_year_boundary(
        self,
    ) -> None:
        current = datetime(2026, 1, 4, 12, tzinfo=UTC)
        cases = (
            ("Before week", datetime(2025, 12, 28, 12, tzinfo=UTC), False),
            ("Monday boundary", datetime(2025, 12, 29, 0, tzinfo=UTC), True),
            ("Sunday boundary", datetime(2026, 1, 4, 23, 59, tzinfo=UTC), True),
            ("After week", datetime(2026, 1, 5, 0, tzinfo=UTC), False),
        )
        for title, moment, _ in cases:
            self._article_seen_at(title, moment)

        with frozen_time(current):
            titles = self._card_titles("week")

        for title, _, included in cases:
            with self.subTest(title=title):
                self.assertEqual(title in titles, included)

    def test_month_includes_leap_day_and_excludes_adjacent_months(self) -> None:
        current = datetime(2024, 2, 15, 12, tzinfo=UTC)
        cases = (
            ("Before leap month", datetime(2024, 1, 31, 23, 59, tzinfo=UTC), False),
            ("Leap month start", datetime(2024, 2, 1, 0, tzinfo=UTC), True),
            ("Leap day end", datetime(2024, 2, 29, 23, 59, tzinfo=UTC), True),
            ("After leap month", datetime(2024, 3, 1, 0, tzinfo=UTC), False),
        )
        for title, moment, _ in cases:
            self._article_seen_at(title, moment)

        with frozen_time(current):
            titles = self._card_titles("month")

        for title, _, included in cases:
            with self.subTest(title=title):
                self.assertEqual(title in titles, included)

    def test_december_month_is_inclusive_without_leaking_into_next_year(self) -> None:
        current = datetime(2025, 12, 15, 12, tzinfo=UTC)
        cases = (
            ("Before December", datetime(2025, 11, 30, 23, 59, tzinfo=UTC), False),
            ("December start", datetime(2025, 12, 1, 0, tzinfo=UTC), True),
            ("December end", datetime(2025, 12, 31, 23, 59, tzinfo=UTC), True),
            ("Next year", datetime(2026, 1, 1, 0, tzinfo=UTC), False),
        )
        for title, moment, _ in cases:
            self._article_seen_at(title, moment)

        with frozen_time(current):
            titles = self._card_titles("month")

        for title, _, included in cases:
            with self.subTest(title=title):
                self.assertEqual(title in titles, included)

    def test_bulk_cutoff_includes_exact_equality_and_excludes_later_fetches(
        self,
    ) -> None:
        cutoff = datetime(2026, 1, 5, 12, tzinfo=UTC)
        equal = self._article_seen_at("Exactly at cutoff", cutoff)
        later = self._article_seen_at(
            "After cutoff", cutoff + timedelta(microseconds=1)
        )
        marker = self.user.bulkreadmarker_set.create(
            scope=ReadScope.FEED,
            feed=self.feed,
        )
        type(marker).objects.filter(pk=marker.pk).update(marked_read_at=cutoff)

        with frozen_time(cutoff + timedelta(seconds=1)):
            titles = self._card_titles("today")

        self.assertNotIn(equal.title, titles)
        self.assertIn(later.title, titles)


class PersistenceIdempotencyContractTests(TestCase):
    @patch("feeds.services.feedparser.parse")
    @patch(
        "feeds.services.fetch_feed_document",
        return_value=FetchedFeedDocument(
            content=b"feed",
            final_url="https://example.com/feed.xml",
            response_headers={"content-location": "https://example.com/feed.xml"},
        ),
    )
    def test_repeated_refresh_updates_one_article_without_resetting_first_seen(
        self, mock_fetch, mock_parse
    ) -> None:
        feed = build_feed(feed_url="https://example.com/feed.xml")
        mock_parse.return_value = {
            "feed": {"title": "Example"},
            "entries": [
                {
                    "id": "stable-guid",
                    "link": "https://example.com/stable",
                    "title": "First title",
                }
            ],
        }
        first_time = datetime(2026, 1, 5, 12, tzinfo=UTC)
        second_time = first_time + timedelta(hours=1)

        with frozen_time(first_time):
            first = refresh_feed(feed)
        article = Article.objects.get(feed=feed, guid="stable-guid")
        first_seen = article.fetched_at
        first_updated = article.updated_at
        mock_parse.return_value["entries"][0]["title"] = "Updated title"
        with frozen_time(second_time):
            second = refresh_feed(feed)

        self.assertEqual((first.created, first.updated), (1, 0))
        self.assertEqual((second.created, second.updated), (0, 1))
        self.assertEqual(Article.objects.filter(feed=feed).count(), 1)
        article.refresh_from_db()
        self.assertEqual(article.title, "Updated title")
        self.assertEqual(article.fetched_at, first_seen)
        self.assertEqual(article.updated_at, second_time)
        self.assertGreater(article.updated_at, first_updated)

    @patch("feeds.services.save_to_linkding")
    def test_repeated_save_keeps_one_row_and_saved_at_but_refreshes_snapshots(
        self, mock_save_to_linkding
    ) -> None:
        user = build_user()
        old_category = build_category(name="Old", slug="old")
        new_category = build_category(name="New", slug="new")
        feed = build_feed(category=old_category)
        article = build_article(feed=feed, title="Original title")
        mock_save_to_linkding.side_effect = [
            ValueError("temporary remote failure"),
            {"url": article.url},
        ]
        first_time = datetime(2026, 1, 5, 12, tzinfo=UTC)
        second_time = first_time + timedelta(hours=1)

        with frozen_time(first_time):
            first = save_article(
                user=user,
                article=article,
                base_url="https://linkding.example.com",
                token="configured",
            )
        first_saved_at = first.saved_at
        article.title = "Updated title"
        article.save(update_fields=["title", "updated_at"])
        feed.category = new_category
        feed.save(update_fields=["category", "updated_at"])
        with frozen_time(second_time):
            second = save_article(
                user=user,
                article=article,
                base_url="https://linkding.example.com",
                token="configured",
            )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(SavedArticle.objects.count(), 1)
        second.refresh_from_db()
        self.assertEqual(second.saved_at, first_saved_at)
        self.assertEqual(second.updated_at, second_time)
        self.assertEqual(second.title, "Updated title")
        self.assertEqual(second.category, new_category)
        self.assertTrue(second.linkding_saved)
        self.assertEqual(second.linkding_error, "")
        self.assertEqual(mock_save_to_linkding.call_count, 2)


class FeedAndNewsletterLifecycleContractTests(StaticFilesTestCase):
    def setUp(self) -> None:
        self.user = build_user(username="contract-reader")
        self.token, self.key = build_api_token(user=self.user)

    def auth_headers(self, scheme: str = "Bearer") -> dict[str, str]:
        return {"authorization": f"{scheme} {self.key}"}

    @patch("feeds.services.refresh_feed")
    def test_refresh_excludes_inactive_feeds(self, mock_refresh_feed) -> None:
        inactive = build_feed(title="A inactive", is_active=False)
        active = build_feed(title="B active")
        mock_refresh_feed.side_effect = lambda feed: RefreshResult(feed=feed)

        results = refresh_active_feeds()

        self.assertEqual([result.feed for result in results], [active])
        mock_refresh_feed.assert_called_once_with(active)
        inactive.refresh_from_db()
        self.assertIsNone(inactive.last_attempt_at)

    def test_api_soft_delete_preserves_content_and_orm_deletes_cascade(self) -> None:
        category = build_category(name="Lifecycle", slug="lifecycle")
        feed = build_feed(category=category)
        article = build_article(feed=feed)
        saved = SavedArticle.objects.create(
            user=self.user,
            article=article,
            url=article.url,
            title=article.title,
            feed=feed,
            category=category,
        )

        response = self.client.delete(
            reverse("api-feed-detail", args=[model_id(feed)]),
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["feed"]["is_active"])
        feed.refresh_from_db()
        self.assertFalse(feed.is_active)
        self.assertTrue(Article.objects.filter(pk=article.pk, feed=feed).exists())
        self.assertTrue(SavedArticle.objects.filter(pk=saved.pk).exists())

        category.delete()
        feed.refresh_from_db()
        saved.refresh_from_db()
        self.assertIsNone(feed.category)
        self.assertIsNone(saved.category)

        article_id = model_id(article)
        saved_id = model_id(saved)
        feed.delete()
        self.assertFalse(Article.objects.filter(pk=article_id).exists())
        self.assertFalse(SavedArticle.objects.filter(pk=saved_id).exists())

        other_feed = build_feed()
        other_article = build_article(feed=other_feed)
        other_saved = SavedArticle.objects.create(
            user=self.user,
            article=other_article,
            url=other_article.url,
            title=other_article.title,
            feed=other_feed,
        )
        other_article.delete()
        self.assertFalse(SavedArticle.objects.filter(pk=other_saved.pk).exists())

    def test_opml_reimport_reactivates_existing_feed(self) -> None:
        feed = build_feed(
            title="Inactive",
            feed_url="https://example.com/reactivate.xml",
            is_active=False,
        )
        content = b"""
        <opml version="2.0"><body>
          <outline title="Reactivated" xmlUrl="https://example.com/reactivate.xml" />
        </body></opml>
        """

        result = import_opml(content)

        self.assertEqual((result.created, result.updated), (0, 1))
        feed.refresh_from_db()
        self.assertTrue(feed.is_active)
        self.assertEqual(feed.title, "Reactivated")

    def test_new_synthetic_newsletter_feed_is_inactive(self) -> None:
        result = import_postmark_newsletter(
            payload=newsletter_payload(message_id="new-synthetic-feed"),
            base_url="https://daily-firehose.example/",
        )

        self.assertFalse(result.issue.article.feed.is_active)
        self.assertEqual(result.issue.article.feed.feed_url, NEWSLETTER_FEED_URL)

    def test_existing_synthetic_newsletter_feed_keeps_active_state(self) -> None:
        feed = Feed.objects.create(
            title=NEWSLETTER_FEED_TITLE,
            feed_url=NEWSLETTER_FEED_URL,
            is_active=True,
        )

        result = import_postmark_newsletter(
            payload=newsletter_payload(message_id="existing-synthetic-feed"),
            base_url="https://daily-firehose.example/",
        )

        feed.refresh_from_db()
        self.assertTrue(feed.is_active)
        self.assertEqual(result.issue.article.feed, feed)

    def test_public_newsletter_get_creates_no_read_save_preference_or_open_state(
        self,
    ) -> None:
        result = import_postmark_newsletter(
            payload=newsletter_payload(message_id="public-read-only"),
            base_url="https://daily-firehose.example/",
        )
        before = (
            ArticleReadState.objects.count(),
            SavedArticle.objects.count(),
            UserPreference.objects.count(),
        )

        response = self.client.get(
            reverse("newsletter-detail", args=[result.issue.public_id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            (
                ArticleReadState.objects.count(),
                SavedArticle.objects.count(),
                UserPreference.objects.count(),
            ),
            before,
        )
        self.assertFalse(hasattr(result.issue, "opened_at"))


@override_settings(POSTMARK_INBOUND_SECRET="inbound-secret")
class AdapterBoundaryContractTests(StaticFilesTestCase):
    def setUp(self) -> None:
        self.user = build_user(username="adapter-reader")
        self.token, self.key = build_api_token(user=self.user)
        self.feed = build_feed()
        self.article = build_article(feed=self.feed)

    def test_bearer_method_precedes_auth_and_auth_precedes_validation(self) -> None:
        route = reverse("api-preferences")
        method_response = self.client.generic(
            "PUT", route, data="{", content_type="application/json"
        )
        auth_response = self.client.generic(
            "PATCH", route, data="{", content_type="application/json"
        )
        moment = datetime(2026, 1, 5, 12, tzinfo=UTC)
        with frozen_time(moment):
            validation_response = self.client.generic(
                "PATCH",
                route,
                data="{",
                content_type="application/json",
                headers={"authorization": f"Bearer {self.key}"},
            )

        self.assertEqual(method_response.status_code, 405)
        self.assertEqual(method_response.json()["error"]["code"], "method_not_allowed")
        self.assertEqual(auth_response.status_code, 401)
        self.assertEqual(auth_response.json()["error"]["code"], "unauthorized")
        self.assertEqual(auth_response.headers["WWW-Authenticate"], "Bearer")
        self.assertEqual(validation_response.status_code, 400)
        self.assertEqual(validation_response.json()["error"]["code"], "bad_request")
        self.token.refresh_from_db()
        self.assertEqual(self.token.last_used_at, moment)

    def test_token_alias_scheme_casing_and_inactive_principals(self) -> None:
        for scheme in ("Bearer", "bearer", "BEARER", "Token", "token", "TOKEN"):
            with self.subTest(scheme=scheme):
                response = self.client.get(
                    reverse("api-feeds"),
                    headers={"authorization": f"{scheme} {self.key}"},
                )
                self.assertEqual(response.status_code, 200)

        self.token.is_active = False
        self.token.save(update_fields=["is_active"])
        response = self.client.get(
            reverse("api-feeds"),
            headers={"authorization": f"Bearer {self.key}"},
        )
        self.assertEqual(response.status_code, 401)
        self.token.is_active = True
        self.token.save(update_fields=["is_active"])
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        response = self.client.get(
            reverse("api-feeds"),
            headers={"authorization": f"Bearer {self.key}"},
        )
        self.assertEqual(response.status_code, 401)

    def test_briefing_action_templates_and_article_urls_are_exact(self) -> None:
        response = self.client.get(
            reverse("api-morning-briefing"),
            headers={"authorization": f"Bearer {self.key}"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["actions"],
            {
                "mark_read": "/api/v1/articles/{id}/read/",
                "save": "/api/v1/articles/{id}/saved/",
            },
        )
        represented = next(
            item for item in payload["articles"] if item["id"] == model_id(self.article)
        )
        self.assertEqual(
            represented["actions"],
            {
                "mark_read": f"/api/v1/articles/{model_id(self.article)}/read/",
                "save": f"/api/v1/articles/{model_id(self.article)}/saved/",
            },
        )

    def test_legacy_digest_is_method_agnostic_after_session_and_csrf_boundary(
        self,
    ) -> None:
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.get(reverse("digest-json")).status_code, 200)
        rejected = client.post(reverse("digest-json"), {"ignored": "value"})
        self.assertEqual(rejected.status_code, 403)
        client.get(reverse("today"))
        csrf_token = client.cookies[settings.CSRF_COOKIE_NAME].value
        accepted_post = client.post(
            f"{reverse('digest-json')}?ignored=value",
            {"ignored": "value"},
            headers={"x-csrftoken": csrf_token},
        )
        accepted_put = client.put(
            reverse("digest-json"),
            data="ignored",
            content_type="text/plain",
            headers={"x-csrftoken": csrf_token},
        )
        self.assertEqual(accepted_post.status_code, 200)
        self.assertEqual(accepted_put.status_code, 200)
        self.assertEqual(
            accepted_post.json()["articles"][0]["id"], model_id(self.article)
        )

    def test_postmark_persists_invalid_emails_without_model_clean(self) -> None:
        response = self.client.post(
            reverse("postmark-inbound", args=["inbound-secret"]),
            {
                "MessageID": "permissive-current-contract",
                "From": "not-an-email",
                "To": "also-not-an-email",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        issue = NewsletterIssue.objects.get(message_id="permissive-current-contract")
        self.assertEqual(issue.subject, "Untitled newsletter")
        self.assertEqual(
            (issue.from_email, issue.to_email),
            ("not-an-email", "also-not-an-email"),
        )
        self.assertEqual((issue.html_body, issue.text_body), ("", ""))
        with self.assertRaises(ValidationError):
            issue.full_clean()


class KnownCrossFeatureContractFailures(StaticFilesTestCase):
    """Narrow characterizations of confirmed cross-feature contract violations."""

    def setUp(self) -> None:
        self.user = build_user(username="known-contract-reader")
        self.token, self.key = build_api_token(user=self.user)
        self.category = build_category(name="Engineering", slug="engineering")
        self.feed = build_feed(category=self.category)
        self.article = build_article(feed=self.feed)
        self.client.force_login(self.user)

    def test_opml_export_import_round_trip_preserves_category(self) -> None:
        original = (
            self.feed.title,
            self.feed.feed_url,
            self.feed.site_url,
            model_id(self.feed.category) if self.feed.category else None,
        )

        import_opml(export_opml().encode())

        self.feed.refresh_from_db()
        self.assertEqual(
            (
                self.feed.title,
                self.feed.feed_url,
                self.feed.site_url,
                model_id(self.feed.category) if self.feed.category else None,
            ),
            original,
        )

    def test_opml_reuses_same_name_category_with_different_slug(self) -> None:
        category = Category.objects.create(name="Python", slug="editorial-python")
        content = b"""
        <opml version="2.0"><body><outline text="Python">
          <outline title="Python feed" xmlUrl="https://example.com/python.xml" />
        </outline></body></opml>
        """

        result = import_opml(content)

        self.assertEqual(result.created, 1)
        imported = Feed.objects.get(feed_url="https://example.com/python.xml")
        self.assertEqual(imported.category, category)
        self.assertEqual(Category.objects.filter(name="Python").count(), 1)

    @expectedFailure
    def test_all_authenticated_get_responses_are_private_no_store(self) -> None:
        issue = import_postmark_newsletter(
            payload=newsletter_payload(message_id="cache-contract"),
            base_url="https://daily-firehose.example/",
        ).issue
        session_routes = (
            ("today", ()),
            ("week", ()),
            ("month", ()),
            ("archived", ()),
            ("saved-links", ()),
            ("feeds", ()),
            ("feed-detail", (model_id(self.feed),)),
            ("opml-import", ()),
            ("opml-export", ()),
            ("preferences", ()),
            ("newsletter-detail", (issue.public_id,)),
            ("digest-json", ()),
        )
        bearer_routes = (
            ("api-morning-briefing", ()),
            ("api-articles", ()),
            ("api-feeds", ()),
            ("api-feed-detail", (model_id(self.feed),)),
            ("api-categories", ()),
            ("api-preferences", ()),
        )
        violations: list[str] = []
        for route, args in session_routes:
            response = self.client.get(reverse(route, args=args))
            self.assertEqual(response.status_code, 200, route)
            directives = response.headers.get("Cache-Control", "").lower()
            if not {"private", "no-store"}.issubset(
                set(directives.replace(",", " ").split())
            ):
                violations.append(route)
        for route, args in bearer_routes:
            response = self.client.get(
                reverse(route, args=args),
                headers={"authorization": f"Bearer {self.key}"},
            )
            self.assertEqual(response.status_code, 200, route)
            directives = response.headers.get("Cache-Control", "").lower()
            if not {"private", "no-store"}.issubset(
                set(directives.replace(",", " ").split())
            ):
                violations.append(route)

        self.assertEqual(violations, [])

    @expectedFailure
    def test_negative_path_ids_use_auth_and_json_validation_envelopes(self) -> None:
        cases: tuple[tuple[str, str, str | None], ...] = (
            ("POST", "/api/v1/articles/-1/read/", '{"is_read": true}'),
            ("POST", "/api/v1/articles/-1/saved/", '{"is_saved": true}'),
            ("GET", "/api/v1/feeds/-1/", None),
            ("POST", "/api/v1/feeds/-1/mark-read/", None),
        )
        violations: list[str] = []
        for method, path, body in cases:
            unauthenticated = self.client.generic(method, path)
            if body is None:
                authenticated = self.client.generic(
                    method,
                    path,
                    headers={"authorization": f"Bearer {self.key}"},
                )
            else:
                authenticated = self.client.generic(
                    method,
                    path,
                    data=body,
                    content_type="application/json",
                    headers={"authorization": f"Bearer {self.key}"},
                )
            if (
                unauthenticated.status_code != 401
                or not unauthenticated.headers.get("Content-Type", "").startswith(
                    "application/json"
                )
                or authenticated.status_code != 422
                or not authenticated.headers.get("Content-Type", "").startswith(
                    "application/json"
                )
            ):
                violations.append(path)

        self.assertEqual(violations, [])
