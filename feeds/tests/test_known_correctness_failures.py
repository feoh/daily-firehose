from __future__ import annotations

import hmac
from unittest import expectedFailure
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.urls import reverse

from ..feed_fetch import FetchedFeedDocument
from ..models import (
    Article,
    ArticleReadState,
    BulkReadMarker,
    Feed,
    ReadScope,
    SavedArticle,
    UserPreference,
)
from ..services import import_postmark_newsletter, refresh_feed
from .support.base import StaticFilesTestCase, model_id
from .support.builders import (
    build_api_token,
    build_article,
    build_feed,
    build_saved_article,
    build_user,
    newsletter_payload,
)


@override_settings(LINKDING_TOKEN="")
class KnownCorrectnessFailureTests(StaticFilesTestCase):
    """Executable contracts for confirmed defects awaiting production fixes.

    PostgreSQL concurrency races are covered by the dedicated PostgreSQL task.
    A real never-ending feed response belongs to the fetch-gateway task because
    production currently gives feedparser a URL and exposes no controllable
    transport boundary.
    """

    def setUp(self) -> None:
        self.user = build_user()
        self.token, self.key = build_api_token(user=self.user)
        self.feed = build_feed()
        self.article = build_article(feed=self.feed)

    def auth_headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.key}"}

    def build_newsletter_article(self) -> Article:
        result = import_postmark_newsletter(
            payload=newsletter_payload(),
            base_url="https://daily-firehose.example/",
        )
        return result.issue.article

    def test_native_json_false_values_remain_supported(self) -> None:
        ArticleReadState.objects.create(
            user=self.user,
            article=self.article,
            is_read=True,
        )
        build_saved_article(user=self.user, article=self.article)
        preferences = UserPreference.objects.create(
            user=self.user,
            compact=True,
            focus_mode=True,
        )

        read_response = self.client.post(
            reverse("api-article-read", args=[model_id(self.article)]),
            {"is_read": False},
            content_type="application/json",
            headers=self.auth_headers(),
        )
        save_response = self.client.post(
            reverse("api-article-saved", args=[model_id(self.article)]),
            {"is_saved": False},
            content_type="application/json",
            headers=self.auth_headers(),
        )
        feed_response = self.client.post(
            reverse("api-feeds"),
            {
                "feed_url": "https://example.com/inactive.xml",
                "title": "Inactive feed",
                "is_active": False,
            },
            content_type="application/json",
            headers=self.auth_headers(),
        )
        preferences_response = self.client.patch(
            reverse("api-preferences"),
            {"compact": False, "focus_mode": False},
            content_type="application/json",
            headers=self.auth_headers(),
        )

        self.assertEqual(read_response.status_code, 200)
        self.assertFalse(read_response.json()["article"]["is_read"])
        self.assertFalse(
            ArticleReadState.objects.get(
                user=self.user,
                article=self.article,
            ).is_read
        )
        self.assertEqual(save_response.status_code, 200)
        self.assertFalse(save_response.json()["article"]["is_saved"])
        self.assertFalse(
            SavedArticle.objects.filter(
                user=self.user,
                article=self.article,
            ).exists()
        )
        self.assertEqual(feed_response.status_code, 201)
        self.assertFalse(feed_response.json()["feed"]["is_active"])
        self.assertFalse(
            Feed.objects.get(feed_url="https://example.com/inactive.xml").is_active
        )
        self.assertEqual(preferences_response.status_code, 200)
        self.assertFalse(preferences_response.json()["preferences"]["compact"])
        self.assertFalse(preferences_response.json()["preferences"]["focus_mode"])
        preferences.refresh_from_db()
        self.assertFalse(preferences.compact)
        self.assertFalse(preferences.focus_mode)

    # Regression: non-boolean JSON values must not be coerced by truthiness.
    def test_article_read_rejects_string_boolean(self) -> None:
        response = self.client.post(
            reverse("api-article-read", args=[model_id(self.article)]),
            {"is_read": "false"},
            content_type="application/json",
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "bad_request",
                    "message": "is_read must be a JSON boolean.",
                }
            },
        )
        self.assertFalse(
            ArticleReadState.objects.filter(
                user=self.user,
                article=self.article,
            ).exists()
        )

    # Regression: non-boolean saved state must not mutate persistence.
    def test_article_save_rejects_string_boolean(self) -> None:
        response = self.client.post(
            reverse("api-article-saved", args=[model_id(self.article)]),
            {"is_saved": "false"},
            content_type="application/json",
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "bad_request",
                    "message": "is_saved must be a JSON boolean.",
                }
            },
        )
        self.assertFalse(
            SavedArticle.objects.filter(
                user=self.user,
                article=self.article,
            ).exists()
        )

    # Regression: feed activation accepts only a JSON boolean.
    def test_feed_creation_rejects_string_boolean(self) -> None:
        feed_url = "https://example.com/string-active.xml"
        response = self.client.post(
            reverse("api-feeds"),
            {
                "feed_url": feed_url,
                "title": "String active feed",
                "is_active": "false",
            },
            content_type="application/json",
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "bad_request",
                    "message": "is_active must be a JSON boolean.",
                }
            },
        )
        self.assertFalse(Feed.objects.filter(feed_url=feed_url).exists())

    # Regression: compact accepts only a JSON boolean.
    def test_preferences_rejects_string_compact_without_mutation(self) -> None:
        preferences = UserPreference.objects.create(user=self.user, compact=False)

        response = self.client.patch(
            reverse("api-preferences"),
            {"compact": "false"},
            content_type="application/json",
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status_code, 400)
        preferences.refresh_from_db()
        self.assertFalse(preferences.compact)

    # Regression: focus_mode accepts only a JSON boolean.
    def test_preferences_rejects_string_focus_mode_without_mutation(self) -> None:
        preferences = UserPreference.objects.create(user=self.user, focus_mode=False)

        response = self.client.patch(
            reverse("api-preferences"),
            {"focus_mode": "false"},
            content_type="application/json",
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status_code, 400)
        preferences.refresh_from_db()
        self.assertFalse(preferences.focus_mode)

    # Regression: unsaving must report the independently persisted read state.
    def test_unsave_preserves_true_read_state_in_response(self) -> None:
        ArticleReadState.objects.create(
            user=self.user,
            article=self.article,
            is_read=True,
        )
        build_saved_article(user=self.user, article=self.article)

        response = self.client.post(
            reverse("api-article-saved", args=[model_id(self.article)]),
            {"is_saved": False},
            content_type="application/json",
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["article"]["is_read"])
        self.assertFalse(response.json()["article"]["is_saved"])
        self.assertTrue(
            ArticleReadState.objects.get(
                user=self.user,
                article=self.article,
            ).is_read
        )

    # Bug: newsletter save prohibition is enforced only by hidden UI controls.
    @expectedFailure
    def test_session_endpoint_rejects_newsletter_save(self) -> None:
        article = self.build_newsletter_article()
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("save-article", args=[model_id(article)]),
            {"article_id": model_id(article), "article_url": article.url},
        )

        self.assertFalse(SavedArticle.objects.filter(article=article).exists())
        self.assertNotEqual(response.status_code, 500)

    # Bug: bearer save bypasses newsletter save prohibition.
    @expectedFailure
    def test_bearer_api_rejects_newsletter_save(self) -> None:
        article = self.build_newsletter_article()

        response = self.client.post(
            reverse("api-article-saved", args=[model_id(article)]),
            {"is_saved": True},
            content_type="application/json",
            headers=self.auth_headers(),
        )

        self.assertFalse(SavedArticle.objects.filter(article=article).exists())
        self.assertNotEqual(response.status_code, 500)

    # Bug: permanent signed GET actions bypass newsletter save prohibition.
    @expectedFailure
    @override_settings(
        AGENT_LINK_SECRET="test-secret",
        AGENT_LINK_USERNAME="reader-1",
    )
    def test_signed_action_rejects_newsletter_save(self) -> None:
        self.user.username = "reader-1"
        self.user.save(update_fields=["username"])
        article = self.build_newsletter_article()
        article_id = model_id(article)
        signature = hmac.new(
            b"test-secret",
            f"save-and-go:{article_id}".encode(),
            "sha256",
        ).hexdigest()

        response = self.client.get(
            reverse("api-article-save-and-go", args=[article_id]),
            {"sig": signature},
        )

        self.assertFalse(SavedArticle.objects.filter(article=article).exists())
        self.assertNotEqual(response.status_code, 500)

    # The briefing needs a per-article capability contract, which is specified
    # by the dedicated newsletter-policy task rather than guessed here.

    # Bug: refresh upserts by GUID but separately constrains URL uniqueness.
    @expectedFailure
    @patch("feeds.services.feedparser.parse")
    @patch(
        "feeds.services.fetch_feed_document",
        return_value=FetchedFeedDocument(
            content=b"feed",
            final_url="https://example.com/feed.xml",
            response_headers={"content-location": "https://example.com/feed.xml"},
        ),
    )
    def test_refresh_reconciles_changed_guid_for_same_url(
        self, mock_fetch, mock_parse
    ) -> None:
        existing = build_article(
            feed=self.feed,
            title="Original title",
            url="https://example.com/stable-url",
            guid="old-guid",
        )
        mock_parse.return_value = {
            "feed": {"title": self.feed.title},
            "entries": [
                {
                    "id": "new-guid",
                    "link": existing.url,
                    "title": "Updated title",
                }
            ],
        }

        result = refresh_feed(self.feed)

        self.assertEqual(result.created, 0)
        self.assertEqual(result.updated, 1)
        self.assertEqual(Article.objects.filter(feed=self.feed).count(), 2)
        existing.refresh_from_db()
        self.assertEqual(existing.guid, "new-guid")
        self.assertEqual(existing.title, "Updated title")

    # Bug: malformed OPML escapes as a server error instead of form feedback.
    @expectedFailure
    def test_malformed_opml_returns_form_feedback_without_writes(self) -> None:
        self.client.force_login(self.user)
        self.client.raise_request_exception = False
        upload = SimpleUploadedFile(
            "broken.opml",
            b"<opml><body><outline",
            content_type="text/xml",
        )

        response = self.client.post(reverse("opml-import"), {"opml_file": upload})

        self.assertEqual(Feed.objects.exclude(id=model_id(self.feed)).count(), 0)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Upload a valid OPML file.")

    # Regression: resource misses use the documented JSON envelope.
    def test_missing_api_article_returns_json_404_envelope(self) -> None:
        response = self.client.post(
            reverse("api-article-read", args=[999999]),
            {"is_read": True},
            content_type="application/json",
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "not_found",
                    "message": "Article not found.",
                }
            },
        )

    # Bug: browser mutation handlers redirect directly to an untrusted next value.
    @expectedFailure
    def test_mark_article_rejects_external_next_redirect(self) -> None:
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("mark-article", args=[model_id(self.article)]),
            {"state": "read", "next": "https://attacker.example/phish"},
        )

        if 300 <= response.status_code < 400:
            self.assertFalse(
                response.headers["Location"].startswith("https://attacker.example")
            )
        else:
            self.assertIn(response.status_code, {400, 403})

    # Bug: article creation is committed before NewsletterIssue creation fails.
    @expectedFailure
    def test_postmark_issue_failure_rolls_back_article(self) -> None:
        with (
            patch(
                "feeds.services.NewsletterIssue.objects.create",
                side_effect=IntegrityError("forced issue failure"),
            ),
            self.assertRaises(IntegrityError),
        ):
            import_postmark_newsletter(
                payload=newsletter_payload(),
                base_url="https://daily-firehose.example/",
            )

        self.assertEqual(Article.objects.exclude(id=model_id(self.article)).count(), 0)

    # Bug: no database constraint enforces the feed-marker shape.
    @expectedFailure
    def test_database_rejects_feed_marker_without_feed(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            BulkReadMarker.objects.create(
                user=self.user,
                scope=ReadScope.FEED,
                feed=None,
                period_start=None,
                period_end=None,
            )

    # Bug: no database constraint requires period-marker dates.
    @expectedFailure
    def test_database_rejects_period_marker_without_dates(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            BulkReadMarker.objects.create(
                user=self.user,
                scope=ReadScope.DAY,
                feed=None,
                period_start=None,
                period_end=None,
            )

    # Bug: no database constraint keeps feeds off period markers.
    @expectedFailure
    def test_database_rejects_period_marker_with_feed(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            BulkReadMarker.objects.create(
                user=self.user,
                scope=ReadScope.DAY,
                feed=self.feed,
                period_start=self.article.fetched_at.date(),
                period_end=self.article.fetched_at.date(),
            )

    # Bug: no database constraint enforces ordered period dates.
    @expectedFailure
    def test_database_rejects_reversed_period_marker(self) -> None:
        day = self.article.fetched_at.date()
        with self.assertRaises(IntegrityError), transaction.atomic():
            BulkReadMarker.objects.create(
                user=self.user,
                scope=ReadScope.WEEK,
                feed=None,
                period_start=day.replace(day=day.day + 1),
                period_end=day,
            )

    # Canonical newsletter-origin behavior is specified by the production-
    # configuration task after the setting and proxy contract are selected.
