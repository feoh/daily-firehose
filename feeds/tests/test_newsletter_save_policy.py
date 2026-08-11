from __future__ import annotations

import hmac
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from ..admin import SavedArticleAdminForm
from ..models import NewsletterIssue, SavedArticle
from ..services import (
    ArticleSaveNotAllowed,
    article_save_capability,
    save_article,
)
from .support.base import StaticFilesTestCase, model_id
from .support.builders import (
    build_api_token,
    build_article,
    build_feed,
    build_newsletter_issue,
    build_user,
)


@override_settings(LINKDING_TOKEN="newsletter-policy-token")
class NewsletterSavePolicyTests(StaticFilesTestCase):
    def setUp(self) -> None:
        self.user = build_user(username="newsletter-reader")
        self.other_user = build_user(username="newsletter-other-reader")
        _, self.key = build_api_token(user=self.user)
        self.feed = build_feed(title="Newsletter policy feed")
        self.ordinary = build_article(
            feed=self.feed,
            title="Ordinary RSS article",
            url="https://example.com/ordinary",
            guid="ordinary",
        )
        self.newsletter = build_article(
            feed=self.feed,
            title="Email newsletter issue",
            url="https://example.com/newsletter",
            guid="newsletter",
        )
        build_newsletter_issue(article=self.newsletter)

    def auth_headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.key}"}

    def test_domain_capability_allows_rss_and_rejects_newsletters_before_io(
        self,
    ) -> None:
        self.assertTrue(article_save_capability(self.ordinary).allowed)
        denied = article_save_capability(self.newsletter)
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.code, ArticleSaveNotAllowed.code)
        self.assertEqual(denied.message, ArticleSaveNotAllowed.message)

        for user in (self.user, self.other_user):
            with (
                self.subTest(user=user.username),
                patch("feeds.services.save_to_linkding") as external_save,
            ):
                with self.assertRaises(ArticleSaveNotAllowed):
                    save_article(
                        user=user,
                        article=self.newsletter,
                        base_url="https://linkding.example.com",
                        token="token",
                    )
                external_save.assert_not_called()
        self.assertFalse(SavedArticle.objects.filter(article=self.newsletter).exists())

    @patch("feeds.services.save_to_linkding")
    def test_domain_command_rechecks_stale_article_capability(
        self, external_save
    ) -> None:
        stale_article = build_article(
            feed=self.feed,
            title="Newsletter created after capability check",
        )
        self.assertTrue(article_save_capability(stale_article).allowed)
        self.assertIsNone(stale_article._state.fields_cache.get("newsletter_issue"))
        NewsletterIssue.objects.create(
            article_id=stale_article.pk,
            message_id="stale-cache-newsletter",
            subject=stale_article.title,
        )
        self.assertIsNone(stale_article._state.fields_cache.get("newsletter_issue"))

        with self.assertRaises(ArticleSaveNotAllowed):
            save_article(
                user=self.user,
                article=stale_article,
                base_url="https://linkding.example.com",
                token="token",
            )

        external_save.assert_not_called()
        self.assertFalse(SavedArticle.objects.filter(article=stale_article).exists())

    @patch("feeds.services.save_to_linkding")
    def test_rejection_does_not_mutate_a_legacy_saved_row(self, external_save) -> None:
        legacy = SavedArticle.objects.create(
            user=self.user,
            article=self.newsletter,
            url="https://legacy.example.com/snapshot",
            title="Legacy snapshot",
            feed=self.feed,
            notes="Preserve this note",
            interest_score=4.0,
            linkding_saved=True,
            linkding_error="Preserve this status",
        )
        original = {
            field: getattr(legacy, field)
            for field in (
                "url",
                "title",
                "feed_id",
                "category_id",
                "notes",
                "interest_score",
                "linkding_saved",
                "linkding_error",
                "saved_at",
                "updated_at",
            )
        }

        with self.assertRaises(ArticleSaveNotAllowed):
            save_article(
                user=self.user,
                article=self.newsletter,
                base_url="https://linkding.example.com",
                token="token",
            )

        legacy.refresh_from_db()
        self.assertEqual(
            {field: getattr(legacy, field) for field in original},
            original,
        )
        external_save.assert_not_called()

    def test_admin_cannot_create_or_reassign_a_save_to_a_newsletter(self) -> None:
        form = SavedArticleAdminForm(
            data={
                "user": self.user.pk,
                "article": self.newsletter.pk,
                "url": self.newsletter.url,
                "title": self.newsletter.title,
                "feed": self.feed.pk,
                "notes": "",
                "linkding_error": "",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["article"], [ArticleSaveNotAllowed.message])

        legacy = SavedArticle.objects.create(
            user=self.user,
            article=self.ordinary,
            url=self.ordinary.url,
            title=self.ordinary.title,
            feed=self.feed,
        )
        reassign = SavedArticleAdminForm(
            instance=legacy,
            data={
                "user": self.user.pk,
                "article": self.newsletter.pk,
                "url": self.newsletter.url,
                "title": self.newsletter.title,
                "feed": self.feed.pk,
                "notes": "",
                "linkding_error": "",
            },
        )
        self.assertFalse(reassign.is_valid())
        self.assertEqual(reassign.errors["article"], [ArticleSaveNotAllowed.message])

    @patch("feeds.services.save_to_linkding")
    def test_domain_command_still_saves_ordinary_rss_articles(
        self, external_save
    ) -> None:
        saved = save_article(
            user=self.user,
            article=self.ordinary,
            base_url="https://linkding.example.com",
            token="token",
        )

        self.assertEqual(saved.article, self.ordinary)
        self.assertTrue(saved.linkding_saved)
        external_save.assert_called_once()

    @patch("feeds.services.save_to_linkding")
    def test_session_ajax_rejection_is_actionable_and_keeps_card(
        self, external_save
    ) -> None:
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("save-article", args=[model_id(self.newsletter)]),
            {
                "article_id": str(model_id(self.newsletter)),
                "article_url": self.newsletter.url,
            },
            headers={"x-requested-with": "XMLHttpRequest"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "message": ArticleSaveNotAllowed.message,
                "level": "error",
                "remove": False,
                "error": {
                    "code": ArticleSaveNotAllowed.code,
                    "message": ArticleSaveNotAllowed.message,
                },
            },
        )
        self.assertFalse(SavedArticle.objects.filter(article=self.newsletter).exists())
        external_save.assert_not_called()

    @patch("feeds.services.save_to_linkding")
    def test_session_form_rejection_redirects_with_safe_feedback(
        self, external_save
    ) -> None:
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("save-article", args=[model_id(self.newsletter)]),
            {
                "article_id": str(model_id(self.newsletter)),
                "article_url": self.newsletter.url,
                "next": reverse("today"),
            },
        )

        self.assertRedirects(response, reverse("today"), fetch_redirect_response=False)
        self.assertEqual(
            [str(message) for message in get_messages(response.wsgi_request)],
            [ArticleSaveNotAllowed.message],
        )
        self.assertFalse(SavedArticle.objects.filter(article=self.newsletter).exists())
        external_save.assert_not_called()

    @patch("feeds.services.save_to_linkding")
    def test_bearer_rejection_uses_semantic_json_envelope(self, external_save) -> None:
        response = self.client.post(
            reverse("api-article-saved", args=[model_id(self.newsletter)]),
            {"is_saved": True},
            content_type="application/json",
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": ArticleSaveNotAllowed.code,
                    "message": ArticleSaveNotAllowed.message,
                }
            },
        )
        self.assertFalse(SavedArticle.objects.filter(article=self.newsletter).exists())
        external_save.assert_not_called()

    @override_settings(
        AGENT_LINK_SECRET="newsletter-agent-secret",
        AGENT_LINK_USERNAME="newsletter-reader",
    )
    @patch("feeds.services.save_to_linkding")
    def test_signed_rejection_uses_same_semantic_json_envelope(
        self, external_save
    ) -> None:
        article_id = model_id(self.newsletter)
        signature = hmac.new(
            b"newsletter-agent-secret",
            f"save-and-go:{article_id}".encode(),
            "sha256",
        ).hexdigest()

        response = self.client.get(
            reverse("api-article-save-and-go", args=[article_id]),
            {"sig": signature},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": ArticleSaveNotAllowed.code,
                    "message": ArticleSaveNotAllowed.message,
                }
            },
        )
        self.assertFalse(SavedArticle.objects.filter(article=self.newsletter).exists())
        external_save.assert_not_called()

    def test_article_and_briefing_payloads_advertise_per_article_capabilities(
        self,
    ) -> None:
        article_response = self.client.get(
            reverse("api-articles"), headers=self.auth_headers()
        )
        briefing_response = self.client.get(
            reverse("api-morning-briefing"), headers=self.auth_headers()
        )

        self.assertEqual(article_response.status_code, 200)
        self.assertEqual(briefing_response.status_code, 200)
        for response in (article_response, briefing_response):
            articles = {row["id"]: row for row in response.json()["articles"]}
            ordinary = articles[model_id(self.ordinary)]
            newsletter = articles[model_id(self.newsletter)]
            self.assertTrue(ordinary["capabilities"]["save"]["allowed"])
            self.assertIsNone(ordinary["capabilities"]["save"]["code"])
            self.assertIn("save", ordinary["actions"])
            self.assertFalse(newsletter["capabilities"]["save"]["allowed"])
            self.assertEqual(
                newsletter["capabilities"]["save"]["code"],
                ArticleSaveNotAllowed.code,
            )
            self.assertEqual(
                newsletter["capabilities"]["save"]["message"],
                ArticleSaveNotAllowed.message,
            )
            self.assertNotIn("save", newsletter["actions"])
            self.assertIn("mark_read", newsletter["actions"])
        self.assertIn("save", briefing_response.json()["actions"])

    def test_briefing_capabilities_do_not_add_per_article_queries(self) -> None:
        with CaptureQueriesContext(connection) as baseline_queries:
            baseline = self.client.get(
                reverse("api-morning-briefing"), headers=self.auth_headers()
            )
        self.assertEqual(baseline.status_code, 200)

        for index in range(5):
            article = build_article(
                feed=self.feed,
                title=f"Additional newsletter {index}",
                url=f"https://example.com/newsletter-{index}",
                guid=f"newsletter-{index}",
            )
            build_newsletter_issue(article=article)
        with CaptureQueriesContext(connection) as expanded_queries:
            expanded = self.client.get(
                reverse("api-morning-briefing"), headers=self.auth_headers()
            )
        self.assertEqual(expanded.status_code, 200)
        self.assertEqual(len(expanded_queries), len(baseline_queries))

    def test_newsletter_read_and_open_semantics_remain_available(self) -> None:
        self.client.force_login(self.user)
        today = self.client.get(reverse("today"))
        self.assertContains(today, "Read newsletter")
        self.assertContains(today, f'href="{self.newsletter.url}"')
        self.assertNotContains(
            today,
            reverse("save-article", args=[model_id(self.newsletter)]),
        )

        marked = self.client.post(
            reverse("mark-article", args=[model_id(self.newsletter)]),
            {"state": "read"},
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(marked.status_code, 200)
        self.assertTrue(marked.json()["remove"])
        self.assertFalse(SavedArticle.objects.filter(article=self.newsletter).exists())
