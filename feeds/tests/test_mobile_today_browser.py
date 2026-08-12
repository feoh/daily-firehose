from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from playwright.sync_api import (  # pyright: ignore[reportMissingImports]
    Browser,
    Page,
    Playwright,
    ViewportSize,
    expect,
    sync_playwright,
)

from ..models import Article, UserPreference
from .support.base import TEST_STORAGES, model_id
from .support.builders import (
    FIXED_NOW,
    build_article,
    build_feed,
    build_user,
    frozen_time,
)
from .support.http_responses import configure_linkding_response

MOBILE_VIEWPORT: ViewportSize = {"width": 390, "height": 844}
NARROW_MOBILE_VIEWPORT: ViewportSize = {"width": 320, "height": 844}
MIN_DISCOVERABLE_CARD_HEIGHT = 48
ARTIFACT_DIR = Path(__file__).resolve().parents[2] / "test-artifacts" / "playwright"


@override_settings(STORAGES=TEST_STORAGES)
class MobileTodayPlaywrightTests(StaticLiveServerTestCase):
    """Narrow real-browser regressions for the mobile Today incident."""

    playwright: Playwright
    browser: Browser
    page: Page

    def setUp(self) -> None:
        self.clock = self.enterContext(frozen_time(FIXED_NOW))
        self.password = "browser-test-password"
        self.user = build_user(username="mobile-reader", password=self.password)
        self.feed = build_feed(title="Mobile regression feed")
        self.first_article = build_article(
            feed=self.feed,
            title="A mobile target article",
            summary="Useful target-card body text for a mobile reader.",
            published_at=self.clock.now - timedelta(minutes=2),
        )
        self.second_article = build_article(
            feed=self.feed,
            title="B first mobile survivor",
            summary="Useful first-survivor body text that must remain visible.",
            published_at=self.clock.now - timedelta(minutes=3),
        )
        self.third_article = build_article(
            feed=self.feed,
            title="C second mobile survivor",
            summary="Useful second-survivor body text that must remain visible.",
            published_at=self.clock.now - timedelta(minutes=1),
        )
        Article.objects.filter(pk=model_id(self.first_article)).update(
            fetched_at=self.clock.now - timedelta(minutes=1)
        )
        Article.objects.filter(pk=model_id(self.second_article)).update(
            fetched_at=self.clock.now - timedelta(minutes=2)
        )
        Article.objects.filter(pk=model_id(self.third_article)).update(
            fetched_at=self.clock.now - timedelta(minutes=3)
        )
        UserPreference.objects.create(user=self.user)
        self.articles_by_id = {
            model_id(self.first_article): self.first_article,
            model_id(self.second_article): self.second_article,
            model_id(self.third_article): self.third_article,
        }

        # Start the sync Playwright event loop only after main-thread ORM setup.
        # Cleanup runs before Django's transactional post-teardown database work.
        self.playwright = sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        executable = Path(self.playwright.chromium.executable_path)
        install_hint = "Run `uv run playwright install chromium` and retry."
        if not executable.exists():
            raise AssertionError(
                f"Required Playwright Chromium is unavailable. {install_hint}"
            )
        try:
            self.browser = self.playwright.chromium.launch(headless=True)
        except Exception as exc:
            raise AssertionError(
                f"Required Playwright Chromium failed to launch. {install_hint}"
            ) from exc
        self.addCleanup(self.browser.close)
        self.context = self.browser.new_context(
            viewport=MOBILE_VIEWPORT,
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
                "AppleWebKit/605.1.15 Mobile/15E148"
            ),
        )
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.goto(f"{self.live_server_url}{reverse('login')}")
        self.page.get_by_label("Username").fill(self.user.username)
        self.page.get_by_label("Password").fill(self.password)
        self.page.get_by_role("button", name="Sign in").click()
        expect(self.page).to_have_url(f"{self.live_server_url}{reverse('today')}")

    def _with_failure_artifacts(
        self, name: str, assertions: Callable[[], None]
    ) -> None:
        try:
            assertions()
        except Exception:
            ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(ARTIFACT_DIR / f"{name}.png"), full_page=True)
            (ARTIFACT_DIR / f"{name}.html").write_text(
                self.page.content(), encoding="utf-8"
            )
            raise

    def _set_display_mode(self, *, compact: bool, focus: bool) -> None:
        self.page.goto(f"{self.live_server_url}{reverse('preferences')}")
        self.page.locator("#id_compact").set_checked(compact)
        self.page.locator("#id_focus_mode").set_checked(focus)
        self.page.get_by_role("button", name="Save preferences").click()
        self.page.goto(f"{self.live_server_url}{reverse('today')}")

    def _assert_mobile_card_contract(self, *, compact: bool, focus: bool) -> None:
        self._set_display_mode(compact=compact, focus=focus)

        body_classes = set(
            (self.page.locator("body").get_attribute("class") or "").split()
        )
        self.assertEqual("compact" in body_classes, compact)
        self.assertEqual("focus-mode" in body_classes, focus)

        nav_links = self.page.locator(".site-nav a")
        for index in range(nav_links.count()):
            bounds = nav_links.nth(index).bounding_box()
            self.assertIsNotNone(bounds)
            assert bounds is not None
            self.assertGreaterEqual(bounds["height"], 24)

        cards = self.page.locator("[data-article-card]")
        expect(cards).to_have_count(3)
        viewport = self.page.viewport_size
        self.assertIsNotNone(viewport)
        assert viewport is not None
        for index in range(cards.count()):
            card = cards.nth(index)
            expect(card).to_be_visible()
            self.assertGreater(len(card.locator("h2").inner_text().strip()), 5)
            self.assertGreater(
                len(card.locator("p:not(.article-meta)").inner_text().strip()), 5
            )
            self.assertGreater(
                len(card.locator(".article-actions").inner_text().strip()), 5
            )
            bounds = card.bounding_box()
            self.assertIsNotNone(bounds)
            assert bounds is not None
            self.assertGreater(bounds["height"], 0)
            self.assertGreater(bounds["width"], 0)
            self.assertGreaterEqual(bounds["x"], 0)
            self.assertLessEqual(bounds["x"] + bounds["width"], viewport["width"])

        overflow: dict[str, Any] = self.page.evaluate(
            """() => ({
                documentScrollWidth: document.documentElement.scrollWidth,
                documentClientWidth: document.documentElement.clientWidth,
                cards: [...document.querySelectorAll('[data-article-card]')].map(
                    card => ({
                        scrollWidth: card.scrollWidth,
                        clientWidth: card.clientWidth,
                    })
                ),
            })"""
        )
        self.assertLessEqual(
            overflow["documentScrollWidth"], overflow["documentClientWidth"]
        )
        for card_widths in overflow["cards"]:
            self.assertLessEqual(card_widths["scrollWidth"], card_widths["clientWidth"])

    def _assert_first_card_initially_discoverable(self) -> None:
        first = self.page.locator("[data-article-card]").first
        bounds = first.bounding_box()
        viewport = self.page.viewport_size
        self.assertIsNotNone(bounds)
        self.assertIsNotNone(viewport)
        assert bounds is not None and viewport is not None
        visible_top = max(bounds["y"], 0)
        visible_bottom = min(bounds["y"] + bounds["height"], viewport["height"])
        visible_height = max(0, visible_bottom - visible_top)
        self.assertGreaterEqual(
            visible_height,
            MIN_DISCOVERABLE_CARD_HEIGHT,
            "the initial viewport must show a meaningful area of the first card",
        )

    def _assert_mode_and_discoverability(self, *, compact: bool, focus: bool) -> None:
        self._assert_mobile_card_contract(compact=compact, focus=focus)
        self._assert_first_card_initially_discoverable()

    def test_normal_mobile_cards_are_readable_and_discoverable(self) -> None:
        self._with_failure_artifacts(
            "today-mobile-normal",
            lambda: self._assert_mode_and_discoverability(compact=False, focus=False),
        )

    def test_compact_mobile_cards_are_readable_and_discoverable(self) -> None:
        self._with_failure_artifacts(
            "today-mobile-compact",
            lambda: self._assert_mode_and_discoverability(compact=True, focus=False),
        )

    def test_focus_mobile_cards_are_readable_without_overflow(self) -> None:
        self._with_failure_artifacts(
            "today-mobile-focus-card-contract",
            lambda: self._assert_mobile_card_contract(compact=False, focus=True),
        )

    def test_focus_first_card_is_initially_discoverable(self) -> None:
        self._set_display_mode(compact=False, focus=True)
        self._with_failure_artifacts(
            "today-mobile-focus-discoverability",
            self._assert_first_card_initially_discoverable,
        )

    def test_320px_mobile_cards_are_readable_without_overflow(self) -> None:
        self.page.set_viewport_size(NARROW_MOBILE_VIEWPORT)
        self._with_failure_artifacts(
            "today-mobile-320px-card-contract",
            lambda: self._assert_mobile_card_contract(compact=False, focus=False),
        )

    def test_320px_first_card_is_initially_discoverable(self) -> None:
        self.page.set_viewport_size(NARROW_MOBILE_VIEWPORT)
        self._set_display_mode(compact=False, focus=False)
        self._with_failure_artifacts(
            "today-mobile-320px-discoverability",
            self._assert_first_card_initially_discoverable,
        )

    @staticmethod
    def _visible_card_contract(page: Page) -> list[dict[str, Any]]:
        return page.locator("[data-article-card]").evaluate_all(
            """cards => cards.map(card => ({
                id: Number(card.dataset.articleId),
                title: card.querySelector('h2')?.innerText.trim() || '',
            }))"""
        )

    def _hard_reload_card_ids(self, route: str, *args: int) -> list[int]:
        url = f"{self.live_server_url}{reverse(route, args=args)}"
        self.page.goto(url, wait_until="networkidle")
        self.page.reload(wait_until="networkidle")
        return [card["id"] for card in self._visible_card_contract(self.page)]

    def _assert_server_rendered_state(
        self,
        *,
        digest_unread_ids: list[int],
        feed_unread_ids: list[int],
        archived_ids: list[int],
        saved_ids: list[int],
    ) -> None:
        for route in ("today", "week", "month"):
            self.assertEqual(self._hard_reload_card_ids(route), digest_unread_ids)
        self.assertEqual(
            self._hard_reload_card_ids("feed-detail", model_id(self.feed)),
            feed_unread_ids,
        )
        self.assertEqual(self._hard_reload_card_ids("archived"), archived_ids)
        self.assertEqual(self._hard_reload_card_ids("saved-links"), saved_ids)

        digest_response = self.page.request.get(
            f"{self.live_server_url}{reverse('digest-json')}"
        )
        self.assertTrue(digest_response.ok)
        self.assertEqual(
            [article["id"] for article in digest_response.json()["articles"]],
            digest_unread_ids,
        )

    def test_hard_reload_matches_json_and_desktop_card_contracts(self) -> None:
        today_url = f"{self.live_server_url}{reverse('today')}"
        reload_response = self.page.reload(wait_until="networkidle")
        self.assertIsNotNone(reload_response)
        assert reload_response is not None
        self.assertIn(
            "no-store", reload_response.headers.get("cache-control", "").lower()
        )
        mobile_cards = self._visible_card_contract(self.page)

        json_response = self.page.request.get(
            f"{self.live_server_url}{reverse('digest-json')}"
        )
        self.assertTrue(json_response.ok)
        json_cards = [
            {"id": article["id"], "title": article["title"]}
            for article in json_response.json()["articles"]
        ]
        self.assertEqual(mobile_cards, json_cards)

        desktop_context = self.browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "Chrome/126.0 Safari/537.36"
            ),
            storage_state=self.context.storage_state(),
        )
        self.addCleanup(desktop_context.close)
        desktop_page = desktop_context.new_page()
        desktop_page.goto(today_url)
        desktop_page.reload(wait_until="networkidle")

        self.assertEqual(self._visible_card_contract(desktop_page), mobile_cards)

    def _assert_targeted_action_removal(
        self, action_name: str
    ) -> tuple[int, list[int]]:
        cards = self.page.locator("[data-article-card]")
        before: list[str] = cards.evaluate_all(
            "cards => cards.map(card => card.dataset.articleId)"
        )
        self.assertEqual(len(before), 3)
        target_id = before[0]
        survivor_ids = before[1:]
        target = self.page.locator(
            f"[data-article-card][data-article-id='{target_id}']"
        )

        target.get_by_role("button", name=action_name).click()

        expect(
            self.page.locator(f"[data-article-card][data-article-id='{target_id}']")
        ).to_have_count(0)
        for survivor_id in survivor_ids:
            expect(
                self.page.locator(
                    f"[data-article-card][data-article-id='{survivor_id}']"
                )
            ).to_have_count(1)
        after: list[str] = cards.evaluate_all(
            "remaining => remaining.map(card => card.dataset.articleId)"
        )
        self.assertEqual(after, survivor_ids)
        expect(self.page.locator("p.inline-message[role='status']")).to_be_visible()
        return int(target_id), [int(survivor_id) for survivor_id in survivor_ids]

    def test_successful_mark_read_removes_only_the_target_card(self) -> None:
        def assertions() -> None:
            target_id, survivor_ids = self._assert_targeted_action_removal("Mark read")
            self._assert_server_rendered_state(
                digest_unread_ids=survivor_ids,
                feed_unread_ids=list(reversed(survivor_ids)),
                archived_ids=[target_id],
                saved_ids=[],
            )

        self._with_failure_artifacts("today-mobile-mark-read", assertions)

    @override_settings(LINKDING_TOKEN="browser-test-token")
    @patch("feeds.services.requests.post")
    def test_successful_save_removes_only_the_target_card(
        self, mock_post: Mock
    ) -> None:
        cards = self.page.locator("[data-article-card]")
        target_id = int(cards.first.get_attribute("data-article-id") or "0")
        target_article = self.articles_by_id[target_id]
        configure_linkding_response(mock_post, article=target_article)

        def assertions() -> None:
            removed_id, survivor_ids = self._assert_targeted_action_removal(
                "Save to Linkding"
            )
            self._assert_server_rendered_state(
                digest_unread_ids=survivor_ids,
                feed_unread_ids=list(reversed(survivor_ids)),
                archived_ids=[],
                saved_ids=[removed_id],
            )
            mock_post.assert_called_once()

        self._with_failure_artifacts("today-mobile-save", assertions)
