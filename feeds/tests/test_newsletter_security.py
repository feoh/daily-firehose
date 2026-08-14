from __future__ import annotations

import base64
import json
from importlib import import_module
from typing import Any, cast

from django.apps import apps
from django.test import TestCase, override_settings
from django.urls import reverse

from feeds.models import Article, NewsletterIssue
from feeds.services import (
    MAX_MESSAGE_ID_LENGTH,
    MAX_SUBJECT_LENGTH,
    newsletter_feed,
    sanitize_newsletter_html,
)

from .support.base import StaticFilesTestCase
from .support.builders import newsletter_payload

# The migration module name starts with a digit, so it cannot be imported by name.
derive_sanitized_html = import_module(
    "feeds.migrations.0011_newsletter_sanitized_html"
).derive_sanitized_html

BASIC_USERNAME = "postmark"
BASIC_PASSWORD = "inbound-basic-password"


def _basic_header(username: str, password: str) -> str:
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return f"Basic {encoded}"


# Each entry must not survive sanitizing into anything executable.
XSS_CORPUS = (
    "<script>alert(1)</script>",
    "<SCRIPT SRC=https://evil.example/x.js></SCRIPT>",
    "<scr<script>ipt>alert(1)</scr</script>ipt>",
    "<img src=x onerror=alert(1)>",
    "<img src=`javascript:alert(1)`>",
    '<a href="javascript:alert(1)">click</a>',
    '<a href="JaVaScRiPt:alert(1)">click</a>',
    '<a href="&#106;avascript:alert(1)">click</a>',
    '<a href="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">x</a>',
    "<svg/onload=alert(1)>",
    '<svg><script>alert(1)</script></svg>',
    "<iframe src=https://evil.example></iframe>",
    '<iframe srcdoc="&lt;script&gt;alert(1)&lt;/script&gt;"></iframe>',
    "<object data=https://evil.example/x.swf></object>",
    "<embed src=https://evil.example/x.swf>",
    '<form action="https://evil.example"><input name="x"></form>',
    "<body onload=alert(1)>",
    "<div onmouseover=alert(1)>hover</div>",
    '<style>@import url(https://evil.example/x.css);</style>',
    '<div style="background:url(javascript:alert(1))">x</div>',
    "<math><mtext><script>alert(1)</script></mtext></math>",
    "<template><script>alert(1)</script></template>",
    "<noscript><p title='</noscript><img src=x onerror=alert(1)>'>",
    "<base href='https://evil.example/'>",
    "<meta http-equiv='refresh' content='0;url=https://evil.example'>",
    "<link rel=stylesheet href='https://evil.example/x.css'>",
    '<input type="image" src=x onerror=alert(1)>',
    "<details open ontoggle=alert(1)>x</details>",
    "<marquee onstart=alert(1)>x</marquee>",
    "<video><source onerror=alert(1)></video>",
)

FORBIDDEN_SUBSTRINGS = (
    "<script",
    "javascript:",
    "onerror=",
    "onload=",
    "onmouseover=",
    "ontoggle=",
    "onstart=",
    "<iframe",
    "<object",
    "<embed",
    "<form",
    "<base",
    "<meta",
    "<link",
    "srcdoc",
    "<style",
    # Entity-encoded "j" in javascript:, which a naive string filter misses.
    "&#106;",
    # A data: document navigates to attacker HTML; data: images stay allowed.
    "data:text/html",
)


class NewsletterSanitizationCorpusTests(TestCase):
    def test_every_corpus_entry_is_dangerous_before_sanitizing(self) -> None:
        """Guard against a corpus that passes because it asserts nothing."""

        for payload in XSS_CORPUS:
            with self.subTest(payload=payload):
                lowered = payload.lower()
                self.assertTrue(
                    any(forbidden in lowered for forbidden in FORBIDDEN_SUBSTRINGS),
                    "corpus entry carries no marker the sanitizer must remove",
                )

    def test_no_corpus_entry_survives_as_executable_markup(self) -> None:
        for payload in XSS_CORPUS:
            with self.subTest(payload=payload):
                cleaned = sanitize_newsletter_html(payload).lower()
                for forbidden in FORBIDDEN_SUBSTRINGS:
                    self.assertNotIn(forbidden, cleaned)

    def test_legitimate_newsletter_markup_is_preserved(self) -> None:
        cleaned = sanitize_newsletter_html(
            "<h1>Issue 12</h1><p>Hello <strong>reader</strong></p>"
            '<table><tr><th scope="col">A</th><td colspan="2">B</td></tr></table>'
            '<a href="https://example.com/post">Read</a>'
        )

        self.assertIn("<h1>Issue 12</h1>", cleaned)
        self.assertIn("<strong>reader</strong>", cleaned)
        self.assertIn('scope="col"', cleaned)
        self.assertIn('colspan="2"', cleaned)
        self.assertIn('href="https://example.com/post"', cleaned)

    def test_outbound_links_cannot_reach_the_opener(self) -> None:
        cleaned = sanitize_newsletter_html('<a href="https://example.com">x</a>')

        self.assertIn('target="_blank"', cleaned)
        self.assertIn("noopener noreferrer", cleaned)

    def test_remote_images_load_but_do_not_carry_the_archive_url(self) -> None:
        cleaned = sanitize_newsletter_html(
            '<img src="https://tracker.example/pixel.gif?id=7" alt="">'
        )

        self.assertIn('src="https://tracker.example/pixel.gif?id=7"', cleaned)
        self.assertIn('referrerpolicy="no-referrer"', cleaned)
        self.assertIn('loading="lazy"', cleaned)

    def test_style_attributes_are_stripped_so_a_strict_policy_holds(self) -> None:
        cleaned = sanitize_newsletter_html('<p style="color:red">x</p>')

        self.assertNotIn("style", cleaned)


@override_settings(
    POSTMARK_INBOUND_SECRET="inbound-secret",
    POSTMARK_WEBHOOK_USERNAME=BASIC_USERNAME,
    POSTMARK_WEBHOOK_PASSWORD=BASIC_PASSWORD,
)
class PostmarkAuthenticationTests(TestCase):
    def _post_basic(self, header: str | None = None, **payload: Any):
        headers = {}
        if header is not None:
            headers["authorization"] = header
        return self.client.post(
            reverse("postmark-inbound-basic"),
            payload or newsletter_payload(message_id="basic-1"),
            content_type="application/json",
            headers=headers,
        )

    def test_valid_basic_credentials_accept_delivery_without_a_url_secret(self) -> None:
        response = self._post_basic(_basic_header(BASIC_USERNAME, BASIC_PASSWORD))

        self.assertEqual(response.status_code, 201)
        self.assertEqual(NewsletterIssue.objects.count(), 1)

    def test_missing_credentials_are_rejected_with_a_challenge(self) -> None:
        response = self._post_basic()

        self.assertEqual(response.status_code, 401)
        self.assertIn("Basic", response.headers["WWW-Authenticate"])
        self.assertEqual(NewsletterIssue.objects.count(), 0)

    def test_wrong_password_and_wrong_username_are_both_rejected(self) -> None:
        for username, password in (
            (BASIC_USERNAME, "wrong"),
            ("wrong", BASIC_PASSWORD),
            ("wrong", "wrong"),
        ):
            with self.subTest(username=username):
                response = self._post_basic(_basic_header(username, password))
                self.assertEqual(response.status_code, 401)
        self.assertEqual(NewsletterIssue.objects.count(), 0)

    def test_malformed_authorization_headers_are_rejected(self) -> None:
        for header in (
            "Basic",
            "Basic !!!not-base64!!!",
            "Basic " + base64.b64encode(b"no-colon").decode("ascii"),
            "Bearer " + base64.b64encode(b"postmark:pw").decode("ascii"),
            _basic_header(BASIC_USERNAME, BASIC_PASSWORD).replace("Basic", "Digest"),
        ):
            with self.subTest(header=header):
                self.assertEqual(self._post_basic(header).status_code, 401)

    @override_settings(POSTMARK_WEBHOOK_USERNAME="", POSTMARK_WEBHOOK_PASSWORD="")
    def test_unconfigured_credentials_never_authorize_a_delivery(self) -> None:
        for header in (None, _basic_header("", ""), "Basic " + base64.b64encode(b":").decode("ascii")):
            with self.subTest(header=header):
                self.assertEqual(self._post_basic(header).status_code, 401)

    def test_legacy_path_secret_still_delivers_during_the_rotation_window(self) -> None:
        response = self.client.post(
            reverse("postmark-inbound", args=["inbound-secret"]),
            newsletter_payload(message_id="legacy-1"),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)

    def test_each_delivery_records_which_mechanism_authenticated_it(self) -> None:
        with self.assertLogs("daily_firehose.webhook", level="INFO") as logs:
            self._post_basic(_basic_header(BASIC_USERNAME, BASIC_PASSWORD))
            self.client.post(
                reverse("postmark-inbound", args=["inbound-secret"]),
                newsletter_payload(message_id="legacy-2"),
                content_type="application/json",
            )

        mechanisms = [cast(Any, record).mechanism for record in logs.records]
        self.assertEqual(mechanisms, ["basic", "path_secret"])
        for record in logs.records:
            self.assertNotIn(BASIC_PASSWORD, record.getMessage())
            self.assertNotIn("inbound-secret", record.getMessage())

    def test_status_codes_match_postmark_retry_semantics(self) -> None:
        """Postmark stops retrying on 403 and retries every other non-200.

        Auth failure therefore answers 401, so a mistyped credential still has
        Postmark's ~10-hour retry window to be corrected before the message is
        marked Inbound Error, instead of being dropped on the first attempt.
        """

        self.assertEqual(self._post_basic().status_code, 401)
        self.assertEqual(
            self._post_basic(_basic_header(BASIC_USERNAME, "wrong")).status_code, 401
        )
        accepted = self._post_basic(_basic_header(BASIC_USERNAME, BASIC_PASSWORD))
        self.assertIn(accepted.status_code, {200, 201})

    def test_get_is_rejected_on_both_routes(self) -> None:
        self.assertEqual(self.client.get(reverse("postmark-inbound-basic")).status_code, 405)
        self.assertEqual(
            self.client.get(reverse("postmark-inbound", args=["inbound-secret"])).status_code,
            405,
        )


@override_settings(POSTMARK_INBOUND_SECRET="inbound-secret")
class PostmarkPayloadLimitTests(TestCase):
    def _post(self, payload: dict[str, Any]):
        return self.client.post(
            reverse("postmark-inbound", args=["inbound-secret"]),
            payload,
            content_type="application/json",
        )

    @override_settings(POSTMARK_MAX_BODY_BYTES=2000)
    def test_oversized_delivery_is_refused_without_writes(self) -> None:
        payload = newsletter_payload(message_id="huge-1")
        payload["HtmlBody"] = "<p>x</p>" * 1000

        response = self._post(payload)

        self.assertEqual(response.status_code, 413)
        self.assertEqual(
            json.loads(response.content)["error"]["code"], "payload_too_large"
        )
        self.assertEqual(NewsletterIssue.objects.count(), 0)
        self.assertEqual(Article.objects.count(), 0)

    @override_settings(POSTMARK_MAX_BODY_BYTES=2_500_000)
    def test_delivery_at_the_documented_limit_is_accepted(self) -> None:
        payload = newsletter_payload(message_id="large-1")
        payload["HtmlBody"] = "<p>x</p>" * 1000

        self.assertEqual(self._post(payload).status_code, 201)

    def test_overlong_subject_is_truncated_rather_than_losing_the_issue(self) -> None:
        payload = newsletter_payload(message_id="long-subject")
        payload["Subject"] = "S" * (MAX_SUBJECT_LENGTH + 250)

        response = self._post(payload)

        self.assertEqual(response.status_code, 201)
        issue = NewsletterIssue.objects.select_related("article").get()
        self.assertEqual(len(issue.subject), MAX_SUBJECT_LENGTH)
        self.assertEqual(len(issue.article.title), MAX_SUBJECT_LENGTH)

    def test_overlong_identity_is_refused_because_truncation_would_collide(
        self,
    ) -> None:
        payload = newsletter_payload(message_id="M" * (MAX_MESSAGE_ID_LENGTH + 1))

        response = self._post(payload)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(NewsletterIssue.objects.count(), 0)
        self.assertEqual(Article.objects.count(), 0)

    def test_overlong_sender_fields_are_truncated_to_their_columns(self) -> None:
        payload = newsletter_payload(message_id="long-sender")
        payload["FromFull"] = {
            "Email": "e" * 300 + "@example.com",
            "Name": "N" * 400,
        }

        self.assertEqual(self._post(payload).status_code, 201)
        issue = NewsletterIssue.objects.get()
        self.assertLessEqual(len(issue.from_email), 254)
        self.assertLessEqual(len(issue.from_name), 255)


class SanitizedHtmlBackfillTests(TestCase):
    """The migration's data step, which an empty test database never exercises."""

    def _issue(self, *, message_id: str, html: str) -> NewsletterIssue:
        article = Article.objects.create(
            feed=newsletter_feed(),
            title="Issue",
            url=f"https://example.com/{message_id}",
            guid=message_id,
        )
        return NewsletterIssue.objects.create(
            article=article,
            message_id=message_id,
            subject="Issue",
            html_body=html,
        )

    def test_backfill_derives_every_issue_across_chunk_boundaries(self) -> None:
        # More rows than the migration's chunk size, so the batching path runs.
        for index in range(150):
            self._issue(
                message_id=f"backfill-{index}",
                html=f"<p>Issue {index}</p><script>alert({index})</script>",
            )
        NewsletterIssue.objects.update(sanitized_html="")

        derive_sanitized_html(apps, None)

        self.assertEqual(NewsletterIssue.objects.count(), 150)
        for issue in NewsletterIssue.objects.all():
            self.assertNotIn("script", issue.sanitized_html)
            self.assertIn("<p>Issue ", issue.sanitized_html)
        self.assertFalse(
            NewsletterIssue.objects.filter(sanitized_html="").exists(),
            "every stored issue must end the migration with derived markup",
        )

    def test_backfill_skips_bodyless_issues_and_is_repeatable(self) -> None:
        self._issue(message_id="empty-1", html="")
        self._issue(message_id="full-1", html="<p>hi</p>")

        derive_sanitized_html(apps, None)
        derive_sanitized_html(apps, None)

        self.assertEqual(
            NewsletterIssue.objects.get(message_id="empty-1").sanitized_html, ""
        )
        self.assertEqual(
            NewsletterIssue.objects.get(message_id="full-1").sanitized_html,
            "<p>hi</p>",
        )


@override_settings(POSTMARK_INBOUND_SECRET="inbound-secret")
class NewsletterPagePrivacyTests(StaticFilesTestCase):
    def _ingest(self, html: str) -> NewsletterIssue:
        payload = newsletter_payload(message_id="privacy-1")
        payload["HtmlBody"] = html
        self.client.post(
            reverse("postmark-inbound", args=["inbound-secret"]),
            payload,
            content_type="application/json",
        )
        return NewsletterIssue.objects.get()

    def test_markup_is_sanitized_once_at_ingest_and_stored(self) -> None:
        issue = self._ingest("<p>hi</p><script>alert(1)</script>")

        self.assertIn("<p>hi</p>", issue.sanitized_html)
        self.assertNotIn("script", issue.sanitized_html)
        self.assertIn("<script>", issue.html_body)

    def test_public_page_renders_the_stored_derivation_without_re_sanitizing(
        self,
    ) -> None:
        issue = self._ingest("<p>original</p>")
        NewsletterIssue.objects.filter(pk=issue.pk).update(
            sanitized_html="<p>stored derivation</p>"
        )

        response = self.client.get(
            reverse("newsletter-detail", args=[issue.public_id])
        )

        self.assertContains(response, "<p>stored derivation</p>")
        self.assertNotContains(response, "<p>original</p>")

    def test_public_page_sends_a_policy_that_forbids_scripts_and_framing(self) -> None:
        issue = self._ingest("<p>hi</p>")

        response = self.client.get(
            reverse("newsletter-detail", args=[issue.public_id])
        )

        policy = response.headers["Content-Security-Policy"]
        self.assertIn("script-src 'self'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertIn("object-src 'none'", policy)
        self.assertIn("base-uri 'none'", policy)
        self.assertIn("img-src 'self' https: data:", policy)
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex")

    def test_remote_images_are_served_with_no_referrer(self) -> None:
        issue = self._ingest('<img src="https://tracker.example/p.gif" alt="">')

        response = self.client.get(
            reverse("newsletter-detail", args=[issue.public_id])
        )

        self.assertContains(response, "https://tracker.example/p.gif")
        self.assertContains(response, 'referrerpolicy="no-referrer"')
