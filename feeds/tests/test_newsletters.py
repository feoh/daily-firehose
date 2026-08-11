from __future__ import annotations

from typing import Any

from django.test import override_settings
from django.urls import reverse

from ..models import Article, NewsletterIssue
from .support.base import StaticFilesTestCase
from .support.builders import (
    build_user,
    newsletter_payload,
)


@override_settings(POSTMARK_INBOUND_SECRET="inbound-secret")
class PostmarkInboundNewsletterTests(StaticFilesTestCase):
    def setUp(self) -> None:
        self.user = build_user()

    def payload(self, *, message_id: str = "message-1") -> dict[str, Any]:
        return newsletter_payload(message_id=message_id)

    def post_payload(self, payload: dict[str, Any], secret: str = "inbound-secret"):
        return self.client.post(
            reverse("postmark-inbound", args=[secret]),
            payload,
            content_type="application/json",
        )

    def test_webhook_rejects_bad_secret(self) -> None:
        response = self.post_payload(self.payload(), secret="wrong")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(NewsletterIssue.objects.count(), 0)

    def test_webhook_creates_newsletter_issue_and_article(self) -> None:
        response = self.post_payload(self.payload())

        self.assertEqual(response.status_code, 201)
        issue = NewsletterIssue.objects.select_related("article", "article__feed").get()
        self.assertEqual(issue.subject, "Daily newsletter")
        self.assertEqual(issue.from_email, "sender@example.com")
        self.assertEqual(issue.from_name, "Newsletter Sender")
        self.assertEqual(issue.to_email, "reader@example.com")
        self.assertEqual(issue.article.title, "Daily newsletter")
        self.assertEqual(issue.article.guid, "message-1")
        self.assertEqual(issue.article.feed.title, "Email Newsletters")
        self.assertIn(f"/newsletters/{issue.public_id}/", issue.article.url)

    def test_webhook_dedupes_by_message_id(self) -> None:
        first = self.post_payload(self.payload())
        second = self.post_payload(self.payload())

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(NewsletterIssue.objects.count(), 1)
        self.assertEqual(Article.objects.count(), 1)
        self.assertFalse(second.json()["created"])

    def test_newsletter_detail_is_public_noindex_and_sanitized(self) -> None:
        self.post_payload(self.payload())
        issue = NewsletterIssue.objects.get()

        response = self.client.get(reverse("newsletter-detail", args=[issue.public_id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Robots-Tag"), "noindex")
        self.assertContains(response, '<meta name="robots" content="noindex">')
        self.assertContains(response, "<h1>Hello</h1>")
        self.assertNotContains(response, 'alert("x")')
        self.assertContains(response, 'target="_blank"')
        self.assertContains(response, "noopener noreferrer")

    def test_authenticated_newsletter_detail_keeps_shortcuts_available(self) -> None:
        self.post_payload(self.payload())
        issue = NewsletterIssue.objects.get()
        self.client.force_login(self.user)

        response = self.client.get(reverse("newsletter-detail", args=[issue.public_id]))

        self.assertContains(response, "data-article-card")
        self.assertContains(response, 'data-action-type="mark-read"')
        self.assertContains(response, "Mark read")
        self.assertNotContains(response, "Save to Linkding")

    def test_newsletter_card_hides_linkding_save(self) -> None:
        self.post_payload(self.payload())
        self.client.force_login(self.user)

        response = self.client.get(reverse("today"))

        self.assertContains(response, "Daily newsletter")
        self.assertContains(response, "Read newsletter")
        self.assertNotContains(response, "Save to Linkding")

    def test_newsletter_card_hides_summary_preview(self) -> None:
        self.post_payload(self.payload())
        self.client.force_login(self.user)

        response = self.client.get(reverse("today"))

        self.assertContains(response, "Daily newsletter")
        self.assertNotContains(response, "Story: https://example.com/story")
