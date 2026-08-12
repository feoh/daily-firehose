from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from playwright.sync_api import (  # pyright: ignore[reportMissingImports]
    Browser,
    BrowserContext,
    Page,
    Playwright,
    ViewportSize,
    expect,
    sync_playwright,
)

from ..models import Article, UserPreference
from .support.base import TEST_STORAGES
from .support.builders import (
    FIXED_NOW,
    build_article,
    build_feed,
    build_user,
    frozen_time,
)

VIEWPORTS: dict[str, ViewportSize] = {
    "mobile-375": {"width": 375, "height": 812},
    "tablet-768": {"width": 768, "height": 1024},
    "desktop-1280": {"width": 1280, "height": 900},
}
THEME_COLORS = {
    "light": ("light", "rgb(248, 247, 244)"),
    "dark": ("dark", "rgb(17, 24, 39)"),
    "dracula": ("dark", "rgb(40, 42, 54)"),
}


def _launch_required_chromium(playwright: Playwright) -> Browser:
    install_hint = "Run `uv run playwright install chromium` and retry."
    executable = playwright.chromium.executable_path
    if not executable or not Path(executable).exists():
        playwright.stop()
        raise AssertionError(
            f"Required Playwright Chromium is unavailable. {install_hint}"
        )
    try:
        return playwright.chromium.launch(headless=True)
    except Exception as exc:
        playwright.stop()
        raise AssertionError(
            f"Required Playwright Chromium failed to launch. {install_hint}"
        ) from exc


@override_settings(
    STORAGES=TEST_STORAGES,
    LINKDING_TOKEN="",
    POSTMARK_INBOUND_EMAIL="browser-copy@example.test",
)
class ResponsiveAccessibilityPlaywrightTests(StaticLiveServerTestCase):
    """Live-Django Chromium coverage for responsive and accessible shared UI."""

    playwright: Playwright
    browser: Browser
    context: BrowserContext
    page: Page

    def setUp(self) -> None:
        self.clock = self.enterContext(frozen_time(FIXED_NOW))
        self.password = "responsive-browser-password"
        self.user = build_user(username="responsive-reader", password=self.password)
        self.feed = build_feed(
            title="A deliberately descriptive feed title",
            feed_url=f"https://example.com/{'long-unbroken-feed-path-' * 8}.xml",
        )
        self.first_article = build_article(
            feed=self.feed,
            title="A first keyboard and responsive article",
            summary="Readable summary content for responsive browser assertions.",
            published_at=self.clock.now - timedelta(minutes=2),
        )
        self.second_article = build_article(
            feed=self.feed,
            title="B second keyboard and responsive article",
            summary="A second readable summary for keyboard selection and actions.",
            published_at=self.clock.now - timedelta(minutes=1),
        )
        Article.objects.filter(pk=self.first_article.pk).update(
            fetched_at=self.clock.now - timedelta(minutes=1)
        )
        Article.objects.filter(pk=self.second_article.pk).update(
            fetched_at=self.clock.now - timedelta(minutes=2)
        )
        UserPreference.objects.create(user=self.user)

        # Start Playwright after ORM setup and clean it up before Django teardown.
        self.playwright = sync_playwright().start()
        self.browser = _launch_required_chromium(self.playwright)
        self.addCleanup(self.playwright.stop)
        self.addCleanup(self.browser.close)
        self.context = self.browser.new_context(viewport=VIEWPORTS["desktop-1280"])
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.set_default_timeout(5_000)
        self._sign_in()

    def _url(self, route: str, *args: int) -> str:
        return f"{self.live_server_url}{reverse(route, args=args)}"

    def _sign_in(self) -> None:
        self.page.goto(self._url("login"))
        self.page.get_by_label("Username").fill(self.user.username)
        self.page.get_by_label("Password").fill(self.password)
        self.page.get_by_role("button", name="Sign in").click()
        expect(self.page).to_have_url(self._url("today"))

    def _set_preferences(self, *, theme: str, compact: bool, focus: bool) -> None:
        self.page.goto(self._url("preferences"))
        self.page.get_by_label("Theme").select_option(theme)
        self.page.get_by_label("Compact").set_checked(compact)
        self.page.get_by_label("Focus mode").set_checked(focus)
        self.page.get_by_role("button", name="Save preferences").click()
        expect(self.page.get_by_text("Preferences saved.")).to_be_visible()

    def _semantic_issues(self) -> list[str]:
        return self.page.evaluate(
            """() => {
                const issues = [];
                const visible = element => {
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' &&
                        rect.width > 0 && rect.height > 0;
                };
                const name = element =>
                    element.getAttribute('aria-label') ||
                    (element.getAttribute('aria-labelledby') || '').split(/\\s+/)
                        .map(id => document.getElementById(id)?.textContent || '').join(' ') ||
                    element.textContent || element.getAttribute('alt') ||
                    element.getAttribute('title') || element.value || '';

                if (!document.documentElement.lang) issues.push('html has no lang');
                const mains = [...document.querySelectorAll('main')].filter(visible);
                if (mains.length !== 1) issues.push(`expected one visible main, found ${mains.length}`);
                if (mains[0]?.tabIndex < 0 === false) issues.push('main is not programmatically focusable');
                const h1s = [...document.querySelectorAll('h1')].filter(visible);
                if (h1s.length !== 1) issues.push(`expected one visible h1, found ${h1s.length}`);

                const ids = [...document.querySelectorAll('[id]')].map(node => node.id);
                const duplicates = [...new Set(ids.filter((id, index) => ids.indexOf(id) !== index))];
                if (duplicates.length) issues.push(`duplicate ids: ${duplicates.join(', ')}`);

                for (const nav of [...document.querySelectorAll('nav')].filter(visible)) {
                    if (!name(nav).trim()) issues.push('navigation has no accessible name');
                }
                for (const image of [...document.querySelectorAll('img')].filter(visible)) {
                    if (!image.hasAttribute('alt')) issues.push(`image lacks alt: ${image.src}`);
                }
                for (const control of [...document.querySelectorAll('button, a[href]')].filter(visible)) {
                    if (!name(control).trim()) issues.push(`${control.tagName.toLowerCase()} has no accessible name`);
                }
                for (const input of [...document.querySelectorAll('input:not([type=hidden]), select, textarea')].filter(visible)) {
                    const labels = input.labels ? [...input.labels].map(label => label.textContent).join(' ') : '';
                    if (!(labels || name(input)).trim()) issues.push(`form control #${input.id || '(no id)'} has no label`);
                }
                for (const dialog of [...document.querySelectorAll('[role=dialog]')]) {
                    if (!name(dialog).trim()) issues.push('dialog has no accessible name');
                }
                const headings = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].filter(visible);
                let previous = 0;
                for (const heading of headings) {
                    const level = Number(heading.tagName.slice(1));
                    if (previous && level > previous + 1) {
                        issues.push(`heading level jumps from h${previous} to h${level}`);
                    }
                    previous = level;
                }
                return issues;
            }"""
        )

    def _assert_reflow_targets_and_semantics(self, label: str) -> None:
        metrics: dict[str, Any] = self.page.evaluate(
            """() => {
                const visible = element => {
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' &&
                        rect.width > 0 && rect.height > 0 && rect.bottom > 0;
                };
                const targetRect = element => {
                    let rect = element.getBoundingClientRect();
                    if (element.matches('input[type=checkbox], input[type=radio]') && element.id) {
                        const label = document.querySelector(`label[for='${CSS.escape(element.id)}']`);
                        if (label) {
                            const other = label.getBoundingClientRect();
                            rect = {
                                width: Math.max(rect.right, other.right) - Math.min(rect.left, other.left),
                                height: Math.max(rect.bottom, other.bottom) - Math.min(rect.top, other.top),
                            };
                        }
                    }
                    return {width: rect.width, height: rect.height};
                };
                return {
                    clientWidth: document.documentElement.clientWidth,
                    scrollWidth: document.documentElement.scrollWidth,
                    mainVisible: visible(document.querySelector('main')),
                    targets: [...document.querySelectorAll(
                        'a[href], button, input:not([type=hidden]), select, textarea'
                    )].filter(visible).map(element => ({
                        description: element.outerHTML.slice(0, 120),
                        ...targetRect(element),
                    })),
                };
            }"""
        )
        self.assertLessEqual(
            metrics["scrollWidth"],
            metrics["clientWidth"],
            f"{label}: page has horizontal overflow",
        )
        self.assertTrue(metrics["mainVisible"], f"{label}: main content is not visible")
        undersized = [
            target
            for target in metrics["targets"]
            if target["width"] < 24 or target["height"] < 24
        ]
        self.assertEqual(undersized, [], f"{label}: targets smaller than 24 CSS px")
        self.assertEqual(
            self._semantic_issues(), [], f"{label}: semantic accessibility audit failed"
        )

    def test_shared_and_auth_pages_cover_responsive_matrix(self) -> None:
        authenticated_routes = ("today", "week", "feeds", "preferences", "opml-import")
        for viewport_name, viewport in VIEWPORTS.items():
            self.page.set_viewport_size(viewport)
            for route in authenticated_routes:
                with self.subTest(viewport=viewport_name, route=route):
                    self.page.goto(self._url(route))
                    self._assert_reflow_targets_and_semantics(
                        f"{viewport_name}/{route}"
                    )

            self.context.clear_cookies()
            self.page.goto(self._url("login"))
            with self.subTest(viewport=viewport_name, route="login"):
                self._assert_reflow_targets_and_semantics(f"{viewport_name}/login")
            self._sign_in()

    def test_compact_and_focus_modes_cover_responsive_matrix(self) -> None:
        for compact, focus in (
            (False, False),
            (True, False),
            (False, True),
            (True, True),
        ):
            self._set_preferences(theme="light", compact=compact, focus=focus)
            for viewport_name, viewport in VIEWPORTS.items():
                with self.subTest(viewport=viewport_name, compact=compact, focus=focus):
                    self.page.set_viewport_size(viewport)
                    self.page.goto(self._url("today"))
                    body_classes = set(
                        (self.page.locator("body").get_attribute("class") or "").split()
                    )
                    self.assertEqual("compact" in body_classes, compact)
                    self.assertEqual("focus-mode" in body_classes, focus)
                    self._assert_reflow_targets_and_semantics(
                        f"{viewport_name}/compact={compact}/focus={focus}"
                    )
                    first_card = self.page.locator("[data-article-card]").first
                    bounds = first_card.bounding_box()
                    self.assertIsNotNone(bounds)
                    assert bounds is not None
                    self.assertGreaterEqual(
                        min(bounds["y"] + bounds["height"], viewport["height"])
                        - max(bounds["y"], 0),
                        48,
                        "the initial viewport must expose a discoverable article target",
                    )

    def test_light_dark_dracula_and_system_themes_persist_and_render(self) -> None:
        for theme, (color_scheme, background) in THEME_COLORS.items():
            with self.subTest(theme=theme):
                self._set_preferences(theme=theme, compact=False, focus=False)
                self.page.goto(self._url("feeds"))
                self.page.reload()
                expect(self.page.locator("body")).to_have_class(f"theme-{theme}")
                rendered = self.page.locator("body").evaluate(
                    "element => ({background: getComputedStyle(element).backgroundColor, colorScheme: getComputedStyle(element).colorScheme})"
                )
                self.assertEqual(
                    rendered, {"background": background, "colorScheme": color_scheme}
                )

        self._set_preferences(theme="system", compact=False, focus=False)
        system_schemes: tuple[tuple[Literal["light", "dark"], str], ...] = (
            ("light", "rgb(248, 247, 244)"),
            ("dark", "rgb(17, 24, 39)"),
        )
        for preferred_scheme, expected_background in system_schemes:
            with self.subTest(theme="system", preferred_scheme=preferred_scheme):
                self.page.emulate_media(color_scheme=preferred_scheme)
                self.page.goto(self._url("today"))
                expect(self.page.locator("body")).to_have_class("theme-system")
                self.assertEqual(
                    self.page.locator("body").evaluate(
                        "element => getComputedStyle(element).backgroundColor"
                    ),
                    expected_background,
                )
        self.page.emulate_media(color_scheme="no-preference")

    def test_live_page_keyboard_shortcuts_help_and_editable_suppression(self) -> None:
        self.page.goto(self._url("today"))
        cards = self.page.locator("[data-article-card]")
        expect(cards.first).to_have_attribute("aria-current", "true")
        self.page.keyboard.press("j")
        expect(cards.nth(1)).to_have_attribute("aria-current", "true")
        expect(cards.nth(1)).to_be_focused()

        self.page.keyboard.press("?")
        dialog = self.page.get_by_role("dialog", name="Keyboard shortcuts")
        expect(dialog).to_be_visible()
        expect(dialog.get_by_role("button", name="Close")).to_be_focused()
        self.page.keyboard.press("Escape")
        expect(dialog).to_be_hidden()
        expect(cards.nth(1)).to_be_focused()

        before = cards.count()
        self.page.keyboard.press("m")
        expect(cards).to_have_count(before - 1)
        expect(self.page.get_by_text("Marked article read.")).to_be_visible()

        routes_by_key = {
            "T": "today",
            "W": "week",
            "M": "month",
            "A": "archived",
            "L": "saved-links",
            "F": "feeds",
        }
        for key, route in routes_by_key.items():
            with self.subTest(key=key, route=route):
                self.page.goto(self._url("preferences"))
                self.page.keyboard.press(key)
                expect(self.page).to_have_url(self._url(route))

        self.page.goto(self._url("preferences"))
        theme = self.page.get_by_label("Theme")
        theme.focus()
        original_value = theme.input_value()
        for key in ("j", "k", "s", "m", "o", "?", "T"):
            theme.dispatch_event(
                "keydown", {"key": key, "bubbles": True, "cancelable": True}
            )
        expect(self.page).to_have_url(self._url("preferences"))
        self.assertEqual(theme.input_value(), original_value)
        expect(self.page.locator("#keyboard-help")).to_be_hidden()

        self.page.goto(self._url("feeds"))
        self.page.keyboard.press("o")
        expect(self.page).to_have_url(self._url("feed-detail", self.feed.pk))

    def test_clipboard_copy_uses_browser_permission_and_announces_success(self) -> None:
        self.context.grant_permissions(
            ["clipboard-read", "clipboard-write"], origin=self.live_server_url
        )
        self.page.goto(self._url("feeds"))
        self.page.evaluate(
            "navigator.clipboard.writeText('permission-probe@example.test')"
        )
        self.assertEqual(
            self.page.evaluate("navigator.clipboard.readText()"),
            "permission-probe@example.test",
        )

        copy_button = self.page.get_by_role("button", name="Copy to Clipboard")
        copy_button.evaluate(
            """button => {
                window.__copyStates = [];
                new MutationObserver(() => window.__copyStates.push(button.textContent))
                    .observe(button, {childList: true, characterData: true, subtree: true});
            }"""
        )
        copy_button.click()
        self.page.wait_for_function(
            "window.__copyStates.includes('Copied!') && navigator.clipboard"
        )
        self.assertIn("Copied!", self.page.evaluate("window.__copyStates"))
        self.assertEqual(
            self.page.evaluate("navigator.clipboard.readText()"),
            "browser-copy@example.test",
        )

    def test_400_percent_equivalent_reflow_has_no_overflow(self) -> None:
        # A 320 CSS-pixel viewport is the WCAG reflow equivalent of 400% zoom at 1280.
        self.page.set_viewport_size({"width": 320, "height": 900})
        for route in ("today", "feeds", "preferences", "opml-import"):
            with self.subTest(route=route):
                self.page.goto(self._url(route))
                self._assert_reflow_targets_and_semantics(f"400%-equivalent/{route}")
