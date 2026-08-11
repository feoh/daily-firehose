from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from playwright.sync_api import (  # pyright: ignore[reportMissingImports]
    Browser,
    BrowserContext,
    Page,
    Playwright,
    expect,
    sync_playwright,
)

from .support.base import TEST_STORAGES, model_id
from .support.builders import build_article, build_feed, build_user, frozen_time

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "static/js/article-actions.js"

FETCH_STUB = """
window.__fetchCalls = [];
window.__fetchResponses = [];
window.__fetchResolvers = [];
window.fetch = (url, options) => {
    window.__fetchCalls.push({
        url,
        method: options.method,
        body: Object.fromEntries(options.body.entries()),
        headers: options.headers,
        credentials: options.credentials,
    });
    const spec = window.__fetchResponses.shift() || {
        ok: true,
        status: 200,
        body: {message: "Done.", level: "success", remove: false},
    };
    if (spec.pending) {
        return new Promise((resolve, reject) => {
            window.__fetchResolvers.push({resolve, reject});
        });
    }
    if (spec.reject) {
        return Promise.reject(new Error(spec.reject));
    }
    return Promise.resolve({
        ok: spec.ok ?? true,
        status: spec.status ?? 200,
        json: () => Promise.resolve(spec.body),
    });
};
"""


def _card(article_id: int, *, url: str | None = None) -> str:
    article_url = url or f"https://article.test/{article_id}"
    return f"""
    <article class="article-card" tabindex="-1" data-article-card
             data-article-id="{article_id}" data-article-url="{article_url}">
      <h2><a href="{article_url}" data-open-article>Article {article_id}</a></h2>
      <form method="post" action="https://app.test/articles/{article_id}/save/"
            data-article-action data-action-type="save"
            data-article-id="{article_id}" data-article-url="{article_url}">
        <input name="csrfmiddlewaretoken" value="csrf-token">
        <input name="article_id" value="{article_id}">
        <input name="article_url" value="{article_url}">
        <button type="submit">Save to Linkding</button>
      </form>
      <form method="post" action="https://app.test/articles/{article_id}/mark/"
            data-article-action data-action-type="mark-read">
        <input name="csrfmiddlewaretoken" value="csrf-token">
        <input name="state" value="read">
        <button type="submit">Mark read</button>
      </form>
    </article>
    """


def _document(body: str) -> str:
    return f"""
    <!doctype html>
    <html><body>
      <main id="main-content" tabindex="-1">{body}</main>
      <div id="keyboard-help" hidden>
        <div role="dialog" aria-labelledby="keyboard-help-heading">
          <h2 id="keyboard-help-heading">Keyboard shortcuts</h2>
          <button type="button" data-close-keyboard-help>Close</button>
        </div>
      </div>
    </body></html>
    """


def _launch_required_chromium(playwright: Playwright) -> Browser:
    install_hint = "Run `uv run playwright install chromium` and retry."
    executable = Path(playwright.chromium.executable_path)
    if not executable.exists():
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


class ChromiumTestMixin:
    playwright: Playwright
    browser: Browser

    @classmethod
    def start_chromium(cls) -> None:
        cls.playwright = sync_playwright().start()
        cls.browser = _launch_required_chromium(cls.playwright)

    @classmethod
    def stop_chromium(cls) -> None:
        cls.browser.close()
        cls.playwright.stop()


class ArticleActionsExecutedDOMTests(ChromiumTestMixin, SimpleTestCase):
    """Execute the progressive article-action script in Chromium against a small DOM."""

    context: BrowserContext
    page: Page

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.start_chromium()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.stop_chromium()
        super().tearDownClass()

    def setUp(self) -> None:
        self.context = self.browser.new_context()
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.set_default_timeout(3_000)

    def load(self, body: str | None = None) -> None:
        if getattr(self, "_script_loaded", False):
            self.page.close()
            self.page = self.context.new_page()
            self.page.set_default_timeout(3_000)
        self._script_loaded = True
        self.page.set_content(_document(body or (_card(101) + _card(202))))
        self.page.evaluate(f"() => {{{FETCH_STUB}}}")
        self.page.add_script_tag(path=str(SCRIPT_PATH))
        self.page.evaluate("document.dispatchEvent(new Event('DOMContentLoaded'))")

    def queue_response(self, body: dict[str, Any], **response: Any) -> None:
        self.page.evaluate(
            "spec => window.__fetchResponses.push(spec)",
            {"body": body, **response},
        )

    def fetch_calls(self) -> list[dict[str, Any]]:
        return self.page.evaluate("window.__fetchCalls")

    def test_read_and_save_forms_use_the_ajax_enhancement_contract(self) -> None:
        self.load()
        self.queue_response(
            {
                "message": "Saved inline.",
                "level": "success",
                "remove": False,
                "article": {"id": 101, "url": "https://article.test/101"},
            }
        )
        self.queue_response(
            {"message": "Marked inline.", "level": "success", "remove": False}
        )

        first_card = self.page.locator("[data-article-card]").first
        first_card.get_by_role("button", name="Save to Linkding").click()
        expect(first_card.get_by_text("Saved inline.")).to_be_visible()
        first_card.get_by_role("button", name="Mark read").click()
        expect(first_card.get_by_text("Marked inline.")).to_be_visible()

        calls = self.fetch_calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            [call["url"] for call in calls],
            [
                "https://app.test/articles/101/save/",
                "https://app.test/articles/101/mark/",
            ],
        )
        for call in calls:
            self.assertEqual(call["method"], "POST")
            self.assertEqual(call["headers"], {"X-Requested-With": "XMLHttpRequest"})
            self.assertEqual(call["credentials"], "same-origin")
        self.assertEqual(
            calls[0]["body"],
            {
                "csrfmiddlewaretoken": "csrf-token",
                "article_id": "101",
                "article_url": "https://article.test/101",
            },
        )
        self.assertEqual(
            calls[1]["body"],
            {"csrfmiddlewaretoken": "csrf-token", "state": "read"},
        )

    def assert_both_cards_remain(self) -> None:
        expect(self.page.locator("[data-article-card]")).to_have_count(2)
        for article_id in (101, 202):
            expect(
                self.page.locator(
                    f"[data-article-card][data-article-id='{article_id}']"
                )
            ).to_have_count(1)

    def test_form_id_mismatch_keeps_both_cards_without_fetching(self) -> None:
        self.load()
        target = self.page.locator("[data-article-card]").first
        target.locator("form[data-action-type='save']").evaluate(
            "form => form.dataset.articleId = '202'"
        )

        target.get_by_role("button", name="Save to Linkding").click()

        expect(target.locator("[role='alert']")).to_have_count(1)
        self.assert_both_cards_remain()
        self.assertEqual(self.fetch_calls(), [])

    def test_form_url_mismatch_keeps_both_cards_without_fetching(self) -> None:
        self.load()
        target = self.page.locator("[data-article-card]").first
        target.locator("form[data-action-type='save']").evaluate(
            "form => form.dataset.articleUrl = 'https://article.test/202'"
        )

        target.get_by_role("button", name="Save to Linkding").click()

        expect(target.locator("[role='alert']")).to_have_count(1)
        self.assert_both_cards_remain()
        self.assertEqual(self.fetch_calls(), [])

    def test_response_id_mismatch_keeps_both_cards(self) -> None:
        self.load()
        self.queue_response(
            {
                "message": "Wrong response ID.",
                "remove": True,
                "article": {"id": 202, "url": "https://article.test/101"},
            }
        )
        target = self.page.locator("[data-article-card]").first

        target.get_by_role("button", name="Save to Linkding").click()

        expect(target.locator("[role='alert']")).to_have_count(1)
        self.assert_both_cards_remain()
        self.assertEqual(len(self.fetch_calls()), 1)

    def test_response_url_mismatch_keeps_both_cards(self) -> None:
        self.load()
        self.queue_response(
            {
                "message": "Wrong response URL.",
                "remove": True,
                "article": {"id": 101, "url": "https://article.test/202"},
            }
        )
        target = self.page.locator("[data-article-card]").first

        target.get_by_role("button", name="Save to Linkding").click()

        expect(target.locator("[role='alert']")).to_have_count(1)
        self.assert_both_cards_remain()
        self.assertEqual(len(self.fetch_calls()), 1)

    def test_pending_actions_disable_and_suppress_duplicate_and_repeated_submits(
        self,
    ) -> None:
        self.load()
        self.page.evaluate("window.__fetchResponses.push({pending: true})")
        target = self.page.locator("[data-article-card]").first
        form = target.locator("form[data-action-type='save']")
        button = target.get_by_role("button", name="Save to Linkding")

        for key in ("s", "m"):
            self.page.evaluate(
                """key => document.dispatchEvent(new KeyboardEvent('keydown', {
                    key, repeat: true, bubbles: true, cancelable: true
                }))""",
                key,
            )
        self.assertEqual(self.fetch_calls(), [])

        button.click()
        expect(button).to_be_disabled()
        form.dispatch_event("submit")
        self.assertEqual(len(self.fetch_calls()), 1)

        self.page.evaluate(
            """body => window.__fetchResolvers.shift().resolve({
                ok: true,
                status: 200,
                json: () => Promise.resolve(body),
            })""",
            {
                "message": "Finished once.",
                "level": "success",
                "remove": False,
                "article": {"id": 101, "url": "https://article.test/101"},
            },
        )
        expect(target.get_by_text("Finished once.")).to_be_visible()
        expect(button).to_be_enabled()
        self.assertEqual(form.get_attribute("data-action-pending"), "false")

    def test_success_removes_only_the_submitted_card_and_selects_the_survivor(
        self,
    ) -> None:
        self.load()
        self.queue_response(
            {"message": "Marked read.", "level": "success", "remove": True}
        )

        self.page.locator("[data-article-card]").first.get_by_role(
            "button", name="Mark read"
        ).click()

        expect(
            self.page.locator("[data-article-card][data-article-id='101']")
        ).to_have_count(0)
        survivor = self.page.locator("[data-article-card][data-article-id='202']")
        expect(survivor).to_have_count(1)
        expect(survivor).to_have_class("article-card is-selected")
        expect(survivor).to_have_attribute("aria-current", "true")
        message = self.page.locator("p.inline-message[role='status']")
        expect(message).to_have_text("Marked read.")
        expect(message).to_have_class("message inline-message success")

    def test_inline_success_and_error_states_keep_forms_retryable(self) -> None:
        self.load()
        self.queue_response(
            {
                "message": "Saved locally with a warning.",
                "level": "warning",
                "remove": False,
                "article": {"id": 101, "url": "https://article.test/101"},
            }
        )
        self.page.evaluate("window.__fetchResponses.push({reject: 'offline'})")

        first = self.page.locator("[data-article-card]").first
        first.get_by_role("button", name="Save to Linkding").click()
        status = first.locator("[role='status']")
        expect(status).to_have_text("Saved locally with a warning.")
        expect(status).to_have_class("message inline-message warning")
        expect(first.get_by_role("button", name="Save to Linkding")).to_be_enabled()

        second = self.page.locator("[data-article-card]").nth(1)
        second.get_by_role("button", name="Mark read").click()
        alert = second.locator("[role='alert']")
        expect(alert).to_have_text("Sorry, that action failed. Please try again.")
        expect(alert).to_have_class("message inline-message error")
        expect(second.get_by_role("button", name="Mark read")).to_be_enabled()

    def test_j_and_k_select_and_focus_articles_and_feeds(self) -> None:
        self.load()
        cards = self.page.locator("[data-article-card]")
        expect(cards.first).to_have_attribute("aria-current", "true")

        self.page.keyboard.press("j")
        expect(cards.nth(1)).to_have_attribute("aria-current", "true")
        self.assertEqual(
            self.page.evaluate("document.activeElement.dataset.articleId"), "202"
        )
        self.page.keyboard.press("j")
        expect(cards.nth(1)).to_have_attribute("aria-current", "true")
        self.page.keyboard.press("k")
        expect(cards.first).to_have_attribute("aria-current", "true")

        self.load(
            """
            <ul>
              <li tabindex="-1" data-feed-list-item>
                <a href="https://feed.test/1" data-open-feed>Feed one</a>
              </li>
              <li tabindex="-1" data-feed-list-item>
                <a href="https://feed.test/2" data-open-feed>Feed two</a>
              </li>
            </ul>
            """
        )
        feeds = self.page.locator("[data-feed-list-item]")
        self.page.keyboard.press("j")
        expect(feeds.nth(1)).to_have_attribute("aria-current", "true")
        self.assertEqual(
            self.page.evaluate("document.activeElement.textContent"), "Feed two"
        )
        self.page.keyboard.press("k")
        expect(feeds.first).to_have_attribute("aria-current", "true")

    def test_s_and_m_submit_the_selected_cards_matching_forms(self) -> None:
        self.load()
        self.queue_response(
            {
                "message": "Saved selected.",
                "remove": False,
                "article": {"id": 202, "url": "https://article.test/202"},
            }
        )
        self.queue_response({"message": "Marked selected.", "remove": False})
        self.page.keyboard.press("j")

        self.page.keyboard.press("s")
        expect(self.page.get_by_text("Saved selected.")).to_be_visible()
        self.page.keyboard.press("m")
        expect(self.page.get_by_text("Marked selected.")).to_be_visible()

        self.assertEqual(
            [call["url"] for call in self.fetch_calls()],
            [
                "https://app.test/articles/202/save/",
                "https://app.test/articles/202/mark/",
            ],
        )

    def test_o_opens_the_selected_article(self) -> None:
        self.page.route(
            "https://article.test/**",
            lambda route: route.fulfill(status=200, body="Opened article"),
        )
        self.load()
        self.page.keyboard.press("j")

        self.page.keyboard.press("o")

        expect(self.page).to_have_url("https://article.test/202")
        expect(self.page.get_by_text("Opened article")).to_be_visible()

    def test_shortcuts_are_suppressed_for_every_editable_element(self) -> None:
        self.load(
            _card(101)
            + _card(202)
            + """
              <input id="input-target" value="input value">
              <textarea id="textarea-target">textarea value</textarea>
              <select id="select-target">
                <option id="option-target" value="one">one</option>
              </select>
              <button id="button-target" type="button">
                <span id="button-child">button target</span>
              </button>
              <div id="editable-bare" contenteditable>bare editable</div>
              <div id="editable-true" contenteditable="true">
                <span id="editable-true-child">true editable</span>
              </div>
              <div id="editable-plaintext" contenteditable="plaintext-only">
                <span id="editable-plaintext-child">plaintext editable</span>
              </div>
              <div id="editable-false" contenteditable="false">
                <span id="editable-false-child">not editable</span>
              </div>
            """
        )
        self.page.evaluate(
            """() => {
                for (const id of ["input-target", "textarea-target"]) {
                    const child = document.createElement("span");
                    child.id = `${id}-child`;
                    document.getElementById(id).append(child);
                }
            }"""
        )
        variants = (
            ("#input-target", "#input-target-child", True),
            ("#textarea-target", "#textarea-target-child", True),
            ("#select-target", "#option-target", False),
            ("#button-target", "#button-child", False),
            ("#editable-bare", "#editable-bare", False),
            ("#editable-true", "#editable-true-child", False),
            ("#editable-plaintext", "#editable-plaintext-child", False),
        )
        state_script = """selector => {
            const target = document.querySelector(selector);
            const selection = document.getSelection();
            return {
                focused: document.activeElement === target,
                value: "value" in target ? target.value : null,
                selectionStart: "selectionStart" in target
                    ? target.selectionStart : null,
                selectionEnd: "selectionEnd" in target ? target.selectionEnd : null,
                selectedText: selection?.toString() || "",
                anchorOffset: selection?.anchorOffset ?? null,
                focusOffset: selection?.focusOffset ?? null,
            };
        }"""
        for focus_selector, event_selector, has_text_selection in variants:
            focus_target = self.page.locator(focus_selector)
            focus_target.focus()
            if has_text_selection:
                focus_target.evaluate("target => target.setSelectionRange(1, 4)")
            elif focus_selector.startswith("#editable-"):
                focus_target.evaluate(
                    """target => {
                        const range = document.createRange();
                        range.selectNodeContents(target);
                        range.setStart(target.firstChild, 1);
                        range.setEnd(target.firstChild, 4);
                        const selection = document.getSelection();
                        selection.removeAllRanges();
                        selection.addRange(range);
                    }"""
                )
            before = self.page.evaluate(state_script, focus_selector)

            for key in ("j", "k", "s", "m", "o", "?"):
                self.page.locator(event_selector).dispatch_event(
                    "keydown", {"key": key, "bubbles": True, "cancelable": True}
                )
                self.assertEqual(
                    self.page.evaluate(state_script, focus_selector),
                    before,
                    f"{key} changed focus/value/selection for {focus_selector}",
                )
                expect(
                    self.page.locator("[data-article-card][data-article-id='101']")
                ).to_have_attribute("aria-current", "true")
                expect(
                    self.page.locator("[data-article-card][data-article-id='202']")
                ).to_have_attribute("aria-current", "false")
                expect(self.page.locator("#keyboard-help")).to_be_hidden()
                self.assertEqual(self.fetch_calls(), [])
                self.assertEqual(self.page.url, "about:blank")

        false_target = self.page.locator("#editable-false")
        false_target.focus()
        self.page.locator("#editable-false-child").dispatch_event(
            "keydown", {"key": "j", "bubbles": True, "cancelable": True}
        )
        expect(
            self.page.locator("[data-article-card][data-article-id='202']")
        ).to_have_attribute("aria-current", "true")
        expect(
            self.page.locator("[data-article-card][data-article-id='202']")
        ).to_be_focused()
        self.assertEqual(self.fetch_calls(), [])
        expect(self.page.locator("#keyboard-help")).to_be_hidden()
        self.assertEqual(self.page.url, "about:blank")

    def test_help_opens_and_closes_by_button_and_escape_with_focus_restored(
        self,
    ) -> None:
        self.load()
        help_dialog = self.page.locator("#keyboard-help")
        close_button = help_dialog.get_by_role("button", name="Close")

        self.page.keyboard.press("?")
        expect(help_dialog).to_be_visible()
        expect(close_button).to_be_focused()
        close_button.click()
        expect(help_dialog).to_be_hidden()
        expect(
            self.page.locator("[data-article-card][data-article-id='101']")
        ).to_be_focused()

        self.page.keyboard.press("j")
        self.page.keyboard.press("?")
        expect(close_button).to_be_focused()
        self.page.keyboard.press("Escape")
        expect(help_dialog).to_be_hidden()
        expect(
            self.page.locator("[data-article-card][data-article-id='202']")
        ).to_be_focused()


@override_settings(
    STORAGES=TEST_STORAGES,
    LINKDING_TOKEN="",
    LINKDING_URL="https://linkding.invalid",
)
class ArticleActionsProgressiveFallbackTests(
    ChromiumTestMixin, StaticLiveServerTestCase
):
    """Prove the server-rendered read/save forms work in a browser with JS disabled."""

    context: BrowserContext
    page: Page

    def setUp(self) -> None:
        self.clock = self.enterContext(frozen_time())
        self.password = "native-form-password"
        self.user = build_user(username="native-form-reader", password=self.password)
        self.feed = build_feed(title="Native form feed")
        self.read_target = build_article(
            feed=self.feed,
            title="Native read target",
            url="https://example.com/native-read",
        )
        self.save_target = build_article(
            feed=self.feed,
            title="Native save target",
            url="https://example.com/native-save",
        )

        # Keep Playwright's sync event loop strictly inside the browser portion of
        # this test so Django ORM setup and transactional teardown stay synchronous.
        self.playwright = sync_playwright().start()
        self.browser = _launch_required_chromium(self.playwright)
        self.addCleanup(self.playwright.stop)
        self.addCleanup(self.browser.close)
        self.context = self.browser.new_context(java_script_enabled=False)
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.goto(f"{self.live_server_url}{reverse('login')}")
        self.page.get_by_label("Username").fill(self.user.username)
        self.page.get_by_label("Password").fill(self.password)
        self.page.get_by_role("button", name="Sign in").click()
        expect(self.page).to_have_url(f"{self.live_server_url}{reverse('today')}")

    @patch("feeds.services.requests.post")
    def test_read_and_save_native_forms_submit_without_javascript(
        self, mock_post: Mock
    ) -> None:
        read_card = self.page.locator(
            f"[data-article-card][data-article-id='{model_id(self.read_target)}']"
        )
        read_form = read_card.locator("form[data-action-type='mark-read']")
        self.assertEqual(read_form.get_attribute("method"), "post")
        self.assertEqual(
            read_form.get_attribute("action"),
            reverse("mark-article", args=[model_id(self.read_target)]),
        )
        self.assertTrue(
            read_form.locator("input[name='csrfmiddlewaretoken']").input_value()
        )
        self.assertEqual(read_form.locator("input[name='next']").input_value(), "/")

        read_button = read_card.get_by_role("button", name="Mark read")
        read_button.focus()
        self.page.keyboard.press("Enter")
        expect(self.page).to_have_url(f"{self.live_server_url}{reverse('today')}")
        expect(self.page.get_by_text("Marked article read.")).to_be_visible()
        expect(
            self.page.locator(
                f"[data-article-card][data-article-id='{model_id(self.read_target)}']"
            )
        ).to_have_count(0)

        save_card = self.page.locator(
            f"[data-article-card][data-article-id='{model_id(self.save_target)}']"
        )
        save_form = save_card.locator("form[data-action-type='save']")
        self.assertEqual(save_form.get_attribute("method"), "post")
        self.assertEqual(
            save_form.get_attribute("action"),
            reverse("save-article", args=[model_id(self.save_target)]),
        )
        self.assertEqual(
            save_form.locator("input[name='article_id']").input_value(),
            str(model_id(self.save_target)),
        )
        self.assertEqual(
            save_form.locator("input[name='article_url']").input_value(),
            self.save_target.url,
        )

        save_button = save_card.get_by_role("button", name="Save to Linkding")
        save_button.focus()
        self.page.keyboard.press("Enter")
        expect(self.page).to_have_url(f"{self.live_server_url}{reverse('today')}")
        expect(
            self.page.get_by_text(
                "Saved locally, but Linkding failed: LINKDING_TOKEN is not configured"
            )
        ).to_be_visible()
        expect(
            self.page.locator(
                f"[data-article-card][data-article-id='{model_id(self.save_target)}']"
            )
        ).to_have_count(0)

        self.page.goto(f"{self.live_server_url}{reverse('saved-links')}")
        expect(
            self.page.get_by_text(self.save_target.title, exact=True)
        ).to_be_visible()
        expect(
            self.page.locator(
                f"[data-article-card][data-article-id='{model_id(self.save_target)}']"
            )
        ).to_have_count(1)
        mock_post.assert_not_called()
