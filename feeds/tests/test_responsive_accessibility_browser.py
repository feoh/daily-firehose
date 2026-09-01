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
THEME_VARIANTS: dict[str, tuple[str, Literal["light", "dark"], str]] = {
    "light": ("light", "light", "rgb(248, 247, 244)"),
    "dark": ("dark", "dark", "rgb(17, 24, 39)"),
    "dracula": ("dracula", "dark", "rgb(40, 42, 54)"),
    "system-light": ("system", "light", "rgb(248, 247, 244)"),
    "system-dark": ("system", "dark", "rgb(17, 24, 39)"),
}
DISPLAY_MODES = (
    (False, False),
    (True, False),
    (False, True),
    (True, True),
)
AUTHENTICATED_RESPONSIVE_ROUTES = (
    ("today", "Today"),
    ("week", "Week"),
    ("recommended", "Recommended"),
    ("feeds", "Feeds"),
    ("preferences", "Preferences"),
    ("opml-import", "OPML"),
)


def _launch_required_chromium(playwright: Playwright) -> Browser:
    install_hint = "Run `uv run playwright install chromium` and retry."
    executable = playwright.chromium.executable_path
    if not executable or not Path(executable).exists():
        raise AssertionError(
            "Required Playwright Chromium executable is unavailable at "
            f"{executable!r}. {install_hint}"
        )
    try:
        return playwright.chromium.launch(headless=True)
    except Exception as exc:
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
            feed_url=f"https://example.com/{'long-unbroken-feed-path-' * 7}.xml",
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
        self.addCleanup(self.playwright.stop)
        self.browser = _launch_required_chromium(self.playwright)
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
                const referencedName = element =>
                    (element.getAttribute('aria-labelledby') || '').split(/\\s+/)
                        .filter(Boolean)
                        .map(id => document.getElementById(id)?.textContent || '')
                        .join(' ');
                const explicitName = (element, includeAssociatedLabels = false) => {
                    const labels = includeAssociatedLabels && element.labels
                        ? [...element.labels].map(label => label.textContent || '').join(' ')
                        : '';
                    return element.getAttribute('aria-label') || referencedName(element) ||
                        labels || element.getAttribute('title') || '';
                };
                const contentName = element => explicitName(element) ||
                    element.textContent || element.getAttribute('alt') || '';

                if (!document.documentElement.lang) issues.push('html has no lang');
                const mains = [...document.querySelectorAll('main')].filter(visible);
                if (mains.length !== 1) issues.push(`expected one visible main, found ${mains.length}`);
                if (mains.length === 1 && mains[0].getAttribute('tabindex') !== '-1') {
                    issues.push('main must explicitly declare tabindex="-1"');
                }
                const h1s = [...document.querySelectorAll('h1')].filter(visible);
                if (h1s.length !== 1) issues.push(`expected one visible h1, found ${h1s.length}`);

                const ids = [...document.querySelectorAll('[id]')].map(node => node.id);
                const duplicates = [...new Set(ids.filter((id, index) => ids.indexOf(id) !== index))];
                if (duplicates.length) issues.push(`duplicate ids: ${duplicates.join(', ')}`);

                for (const nav of [...document.querySelectorAll('nav')].filter(visible)) {
                    if (!explicitName(nav).trim()) issues.push('navigation has no accessible name');
                }
                for (const image of [...document.querySelectorAll('img')].filter(visible)) {
                    if (!image.hasAttribute('alt')) issues.push(`image lacks alt: ${image.src}`);
                }
                for (const control of [...document.querySelectorAll('button, a[href]')].filter(visible)) {
                    if (!contentName(control).trim()) issues.push(`${control.tagName.toLowerCase()} has no accessible name`);
                }
                for (const input of [...document.querySelectorAll('input:not([type=hidden]), select, textarea')].filter(visible)) {
                    if (!explicitName(input, true).trim()) {
                        issues.push(`form control #${input.id || '(no id)'} has no label`);
                    }
                }
                for (const dialog of [...document.querySelectorAll('[role=dialog]')]) {
                    if (!explicitName(dialog).trim()) issues.push('dialog has no accessible name');
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
                        inlineLink: element.matches('a[href]') &&
                            getComputedStyle(element).display === 'inline',
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
        # WCAG 2.5.8 exempts inline links, whose box is set by the surrounding
        # line-height. Measuring them compares host font metrics, not layout.
        undersized = [
            target
            for target in metrics["targets"]
            if not target["inlineLink"]
            and (target["width"] < 24 or target["height"] < 24)
        ]
        self.assertEqual(undersized, [], f"{label}: targets smaller than 24 CSS px")
        self.assertEqual(
            self._semantic_issues(), [], f"{label}: semantic accessibility audit failed"
        )

    def test_shared_and_auth_pages_cover_responsive_matrix(self) -> None:
        for viewport_name, viewport in VIEWPORTS.items():
            self.page.set_viewport_size(viewport)
            for route, page_name in AUTHENTICATED_RESPONSIVE_ROUTES:
                with self.subTest(viewport=viewport_name, route=page_name):
                    self.page.goto(self._url(route))
                    self._assert_reflow_targets_and_semantics(
                        f"{viewport_name}/{page_name}"
                    )

            self.context.clear_cookies()
            self.page.goto(self._url("login"))
            with self.subTest(viewport=viewport_name, route="login"):
                self._assert_reflow_targets_and_semantics(f"{viewport_name}/login")
            self._sign_in()

    def test_compact_and_focus_modes_cover_responsive_matrix(self) -> None:
        for compact, focus in DISPLAY_MODES:
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

    def test_tested_themes_cross_compact_and_focus_modes(self) -> None:
        self.page.set_viewport_size(VIEWPORTS["desktop-1280"])
        for variant, (theme, preferred_scheme, background) in THEME_VARIANTS.items():
            for compact, focus in DISPLAY_MODES:
                with self.subTest(theme=variant, compact=compact, focus=focus):
                    self.page.emulate_media(color_scheme=preferred_scheme)
                    self._set_preferences(theme=theme, compact=compact, focus=focus)
                    self.page.goto(self._url("today"))
                    self.page.reload()
                    body = self.page.locator("body")
                    body_classes = set((body.get_attribute("class") or "").split())
                    self.assertIn(f"theme-{theme}", body_classes)
                    self.assertEqual("compact" in body_classes, compact)
                    self.assertEqual("focus-mode" in body_classes, focus)
                    self.assertEqual(
                        body.evaluate(
                            "element => ({background: getComputedStyle(element).backgroundColor, colorScheme: getComputedStyle(element).colorScheme})"
                        ),
                        {
                            "background": background,
                            "colorScheme": preferred_scheme,
                        },
                    )
                    self._assert_reflow_targets_and_semantics(
                        f"desktop-1280/{variant}/compact={compact}/focus={focus}"
                    )
        self.page.emulate_media(color_scheme="no-preference")

    def test_semantic_audit_rejects_false_names_and_implicit_main_tabindex(
        self,
    ) -> None:
        cases = (
            (
                "select option text is not a label",
                '<select id="synthetic-select"><option>Descendant option text</option></select>',
                'tabindex="-1"',
                ["form control #synthetic-select has no label"],
            ),
            (
                "navigation descendant text is not a name",
                '<nav><a href="#">Named descendant</a></nav>',
                'tabindex="-1"',
                ["navigation has no accessible name"],
            ),
            (
                "dialog descendant text is not a name",
                '<div role="dialog"><h2>Descendant heading</h2></div>',
                'tabindex="-1"',
                ["dialog has no accessible name"],
            ),
            (
                "main needs an explicit tabindex attribute",
                "<p>Main content</p>",
                "",
                ['main must explicitly declare tabindex="-1"'],
            ),
        )
        for label, fragment, main_attributes, expected_issues in cases:
            with self.subTest(case=label):
                self.page.set_content(
                    f"""<!doctype html>
                    <html lang="en"><body>
                    <main {main_attributes}><h1>Synthetic audit</h1>{fragment}</main>
                    </body></html>"""
                )
                self.assertEqual(self._semantic_issues(), expected_issues)

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
            "R": "recommended",
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
        for key in ("j", "k", "s", "m", "o", "?", "T", "R"):
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
        feedback = self.page.locator("[data-copy-feedback]")
        expect(feedback).to_have_attribute("role", "status")
        expect(feedback).to_have_attribute("aria-live", "polite")
        expect(feedback).to_have_text("Copied email address.")
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
