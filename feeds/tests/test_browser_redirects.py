from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import SESSION_KEY
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from playwright.sync_api import (  # pyright: ignore[reportMissingImports]
    expect,
    sync_playwright,
)

from ..models import Article, ReadScope
from .support.base import TEST_STORAGES, StaticFilesTestCase, model_id
from .support.builders import (
    build_article,
    build_feed,
    build_user,
    signed_action_query,
    signed_action_url,
)


@override_settings(LINKDING_TOKEN="")
class BrowserRedirectRequestTests(StaticFilesTestCase):
    def setUp(self) -> None:
        self.password = "redirect-contract-password"
        self.user = build_user(password=self.password)
        self.feed = build_feed(title="Redirect contract feed")
        self.article = build_article(feed=self.feed, title="Redirect contract article")

    def test_auth_next_accepts_local_paths_and_rejects_ambiguous_destinations(
        self,
    ) -> None:
        login_url = reverse("login")
        safe_response = self.client.post(
            login_url,
            {
                "username": self.user.username,
                "password": self.password,
                "next": f"{reverse('week')}?view=compact#articles",
            },
        )
        self.assertRedirects(
            safe_response,
            f"{reverse('week')}?view=compact#articles",
            fetch_redirect_response=False,
        )

        for target in (
            "/search/?q=100%25",
            "/search/?q=encoded%20space",
        ):
            with self.subTest(target=target):
                self.client.logout()
                response = self.client.post(
                    login_url,
                    {
                        "username": self.user.username,
                        "password": self.password,
                        "next": target,
                    },
                )
                self.assertRedirects(response, target, fetch_redirect_response=False)

        unsafe_targets = (
            "https://attacker.example/phish",
            "//attacker.example/phish",
            "//testserver/phish",
            "https://reader@testserver/phish",
            "\\\\attacker.example\\phish",
            "/%2f%2fattacker.example/phish",
            "/%255c%255cattacker.example/phish",
            "/%252f%252fattacker.example/phish",
            "/safe%0d%0aLocation:%20https://attacker.example",
            "/search/?q=malformed%",
            "/search/?q=malformed%2",
            "\x00//attacker.example/phish",
        )
        for target in unsafe_targets:
            with self.subTest(target=target):
                self.client.logout()
                response = self.client.post(
                    login_url,
                    {
                        "username": self.user.username,
                        "password": self.password,
                        "next": target,
                    },
                )
                self.assertRedirects(response, reverse("today"))

    def test_logout_next_uses_the_same_safe_policy(self) -> None:
        self.client.force_login(self.user)
        accepted = self.client.post(reverse("logout"), {"next": reverse("week")})
        self.assertRedirects(accepted, reverse("week"), fetch_redirect_response=False)
        self.assertNotIn(SESSION_KEY, self.client.session)

        self.client.force_login(self.user)
        rejected = self.client.post(
            reverse("logout"), {"next": "//attacker.example/phish"}
        )
        self.assertRedirects(rejected, reverse("login"))
        self.assertNotIn(SESSION_KEY, self.client.session)

    @patch("feeds.commands.refresh_active_feeds", return_value=[])
    def test_every_browser_mutation_rejects_external_next(self, _refresh) -> None:
        self.client.force_login(self.user)
        external = "https://attacker.example/phish"
        cases = (
            (reverse("refresh-feeds"), {"next": external}, reverse("today")),
            (
                reverse("mark-article", args=[model_id(self.article)]),
                {"state": "read", "next": external},
                reverse("today"),
            ),
            (
                reverse("mark-period-read"),
                {
                    "scope": ReadScope.DAY,
                    "period_start": "2026-01-05",
                    "period_end": "2026-01-05",
                    "next": external,
                },
                reverse("today"),
            ),
            (
                reverse("mark-feed-read", args=[model_id(self.feed)]),
                {"next": external},
                reverse("feed-detail", args=[model_id(self.feed)]),
            ),
            (
                reverse("save-article", args=[model_id(self.article)]),
                {
                    "article_id": str(model_id(self.article)),
                    "article_url": self.article.url,
                    "next": external,
                },
                reverse("today"),
            ),
        )
        for url, data, fallback in cases:
            with self.subTest(url=url):
                response = self.client.post(url, data)
                self.assertRedirects(response, fallback)

    def test_browser_mutations_preserve_valid_relative_first_party_next(self) -> None:
        self.client.force_login(self.user)
        destination = f"{reverse('saved-links')}?from=action#articles"
        response = self.client.post(
            reverse("mark-article", args=[model_id(self.article)]),
            {"state": "read", "next": destination},
        )
        self.assertRedirects(response, destination, fetch_redirect_response=False)

    @override_settings(
        ALLOWED_HOSTS=["firehose.example"],
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    )
    def test_proxy_host_and_scheme_define_same_origin(self) -> None:
        self.client.force_login(self.user)
        action = reverse("mark-article", args=[model_id(self.article)])
        accepted = self.client.post(
            action,
            {"state": "read", "next": "https://firehose.example/week/"},
            HTTP_HOST="firehose.example",
            HTTP_X_FORWARDED_PROTO="https",
        )
        self.assertRedirects(
            accepted,
            "https://firehose.example/week/",
            fetch_redirect_response=False,
        )

        for target in (
            "http://firehose.example/week/",
            "//firehose.example/week/",
            "https://attacker.example/week/",
        ):
            with self.subTest(target=target):
                rejected = self.client.post(
                    action,
                    {"state": "read", "next": target},
                    HTTP_HOST="firehose.example",
                    HTTP_X_FORWARDED_PROTO="https",
                )
                self.assertRedirects(rejected, reverse("today"))

    @override_settings(
        AGENT_LINK_SECRET="test-secret",
        AGENT_LINK_USERNAME="signed-redirect-reader",
    )
    def test_signed_save_go_validates_intentional_outbound_article_url(self) -> None:
        signed_user = build_user(username="signed-redirect-reader")
        article_id = model_id(self.article)

        def save_and_go():
            # Each attempt mints its own capability: a signed action is single use.
            return self.client.post(
                signed_action_url(
                    reverse("api-article-save-and-go", args=[article_id]),
                    **signed_action_query(
                        purpose="save-and-go", target=str(article_id)
                    ),
                )
            )

        valid = save_and_go()
        self.assertEqual(valid.status_code, 302)
        self.assertEqual(valid.headers["Location"], self.article.url)

        encoded_article_url = "https://example.com/search/?q=100%25%20coverage"
        Article.objects.filter(pk=article_id).update(url=encoded_article_url)
        encoded_response = save_and_go()
        self.assertRedirects(
            encoded_response, encoded_article_url, fetch_redirect_response=False
        )

        unsafe_urls = (
            "javascript:alert(1)",
            "https://reader@example.com/private",
            "https://example.com/%0d%0aLocation:%20https://attacker.example",
            "https://example.com/%255c%255cattacker.example/phish",
            "https://example.com/search/?q=malformed%",
            "https://example.com\\@attacker.example/phish",
        )
        for target in unsafe_urls:
            with self.subTest(target=target):
                Article.objects.filter(pk=article_id).update(url=target)
                response = save_and_go()
                self.assertRedirects(
                    response, reverse("today"), fetch_redirect_response=False
                )
        self.assertTrue(signed_user.is_active)


@override_settings(STORAGES=TEST_STORAGES)
class BrowserRedirectLiveTests(StaticLiveServerTestCase):
    def setUp(self) -> None:
        self.password = "live-redirect-password"
        self.user = build_user(username="live-redirect-reader", password=self.password)

    def test_live_login_and_logout_ignore_external_next_targets(self) -> None:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(java_script_enabled=False)
            external = "https://attacker.example/phish"
            page.goto(f"{self.live_server_url}{reverse('login')}?next={external}")
            page.get_by_label("Username").fill(self.user.username)
            page.get_by_label("Password").fill(self.password)
            page.get_by_role("button", name="Sign in").click()
            expect(page).to_have_url(f"{self.live_server_url}{reverse('today')}")

            page.get_by_role("button", name="Sign out").click()
            expect(page).to_have_url(f"{self.live_server_url}{reverse('login')}")
            browser.close()

    def test_live_login_rejects_same_host_scheme_relative_http_target(self) -> None:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(java_script_enabled=False)
            same_host_http = (
                f"//{self.live_server_url.removeprefix('http://')}{reverse('week')}"
            )
            page.goto(f"{self.live_server_url}{reverse('login')}?next={same_host_http}")
            page.get_by_label("Username").fill(self.user.username)
            page.get_by_label("Password").fill(self.password)
            page.get_by_role("button", name="Sign in").click()
            expect(page).to_have_url(f"{self.live_server_url}{reverse('today')}")
            browser.close()
