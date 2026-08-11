from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from django.urls import reverse
from django.utils import timezone

from ..models import (
    Article,
    ArticleReadState,
    BulkReadMarker,
    ReadScope,
    UserPreference,
)
from .support.base import (
    DigestTestCase,
    StaticFilesTestCase,
    model_id,
)
from .support.builders import (
    FIXED_NOW,
    build_article,
    build_feed,
    build_read_state,
    build_saved_article,
    build_user,
    frozen_time,
)


class TodayIncidentRequestRegressionTests(StaticFilesTestCase):
    def setUp(self) -> None:
        self.clock = self.enterContext(frozen_time(FIXED_NOW))
        self.user = build_user()
        self.feed = build_feed(title="Today incident feed")
        self.client.force_login(self.user)

    def test_today_uses_utc_date_across_local_midnight_boundary(self) -> None:
        after_utc_midnight = datetime(2026, 1, 5, 0, 30, tzinfo=UTC)
        local_time = after_utc_midnight.astimezone(ZoneInfo("America/Los_Angeles"))
        self.assertEqual(local_time.date().isoformat(), "2026-01-04")

        with frozen_time(after_utc_midnight):
            article = build_article(
                feed=self.feed,
                title="Seen after UTC midnight",
                published_at=after_utc_midnight,
            )
            Article.objects.filter(pk=article.pk).update(fetched_at=after_utc_midnight)
            response = self.client.get(reverse("today"))

        self.assertContains(response, article.title)
        self.assertContains(response, "January 5, 2026")

    def test_mobile_and_desktop_user_agents_receive_identical_cards(self) -> None:
        build_article(feed=self.feed, title="Same card on every device")
        mobile = self.client.get(
            reverse("today"),
            headers={
                "user-agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
                    "AppleWebKit/605.1.15 Mobile/15E148"
                )
            },
        )
        desktop = self.client.get(
            reverse("today"),
            headers={
                "user-agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "Chrome/126.0 Safari/537.36"
                )
            },
        )

        mobile_cards = [model_id(card["article"]) for card in mobile.context["cards"]]
        desktop_cards = [model_id(card["article"]) for card in desktop.context["cards"]]
        self.assertEqual(mobile_cards, desktop_cards)
        self.assertContains(mobile, "Same card on every device")
        self.assertContains(desktop, "Same card on every device")

    def test_visibility_state_is_scoped_to_the_authenticated_user(self) -> None:
        other_user = build_user()
        own_read = build_article(feed=self.feed, title="Current user read")
        own_saved = build_article(feed=self.feed, title="Current user saved")
        other_read = build_article(feed=self.feed, title="Other user read")
        other_saved = build_article(feed=self.feed, title="Other user saved")
        visible = build_article(feed=self.feed, title="Visible to everyone")
        build_read_state(user=self.user, article=own_read)
        build_saved_article(user=self.user, article=own_saved)
        build_read_state(user=other_user, article=other_read)
        build_saved_article(user=other_user, article=other_saved)

        response = self.client.get(reverse("today"))

        self.assertNotContains(response, own_read.title)
        self.assertNotContains(response, own_saved.title)
        self.assertContains(response, other_read.title)
        self.assertContains(response, other_saved.title)
        self.assertContains(response, visible.title)

    def test_today_dynamic_html_forbids_stale_cache_reuse(self) -> None:
        response = self.client.get(reverse("today"))

        cache_control = response.headers.get("Cache-Control", "").lower()
        for directive in ("no-store", "no-cache", "private", "max-age=0"):
            self.assertIn(directive, cache_control)


class DigestArticleVisibilityTests(DigestTestCase):
    def test_today_hides_read_and_saved_articles(self) -> None:
        response = self.client.get(reverse("today"))

        self.assertContains(response, "Unread article")
        self.assertNotContains(response, "Read article")
        self.assertNotContains(response, "Saved article")
        self.assertContains(response, "1 articles in this view.")

    def test_today_uses_first_seen_date_not_publication_date(self) -> None:
        old_published_article = Article.objects.create(
            feed=self.feed,
            title="Old publication seen today",
            url="https://example.com/old-publication",
            guid="old-publication",
            published_at=timezone.now() - timedelta(days=30),
        )
        Article.objects.filter(id=model_id(self.unread_article)).update(
            fetched_at=timezone.now() - timedelta(days=1)
        )

        response = self.client.get(reverse("today"))

        self.assertContains(response, old_published_article.title)
        self.assertNotContains(response, self.unread_article.title)

    def test_focus_mode_adds_body_class_without_changing_theme(self) -> None:
        UserPreference.objects.create(
            user=self.user,
            theme=UserPreference.Theme.CATPPUCCIN_MOCHA,
            focus_mode=True,
        )

        response = self.client.get(reverse("today"))

        self.assertContains(response, "theme-catppuccin-mocha focus-mode")
        self.assertContains(response, "Unread article")

    def test_marked_read_article_is_hidden_from_week_and_month(self) -> None:
        response = self.client.post(
            reverse("mark-article", args=[model_id(self.unread_article)]),
            {"state": "read"},
        )

        self.assertEqual(response.status_code, 302)
        response = self.client.get(reverse("week"))
        self.assertNotContains(response, "Unread article")
        response = self.client.get(reverse("month"))
        self.assertNotContains(response, "Unread article")

    def test_archived_shows_recently_marked_read_articles(self) -> None:
        response = self.client.get(reverse("archived"))

        self.assertContains(response, "Archived (Marked Read)")
        self.assertContains(response, "Recently marked read")
        self.assertContains(response, "Read article")
        self.assertContains(response, "Mark unread")
        self.assertContains(response, 'data-keyboard-nav="A"')
        self.assertNotContains(response, "Unread article")
        self.assertNotContains(response, "Saved article")
        self.assertNotContains(response, "Mark this period read")

    def test_saved_links_shows_only_recently_saved_articles(self) -> None:
        response = self.client.get(reverse("saved-links"))

        self.assertContains(response, "Saved (L)inks")
        self.assertContains(response, "Recently saved to Linkding")
        self.assertContains(response, "Saved article")
        self.assertContains(response, "Linkding confirmed")
        self.assertContains(response, 'data-keyboard-nav="L"')
        self.assertNotContains(response, "Unread article")
        self.assertNotContains(response, "Read article")
        self.assertNotContains(response, "Save to Linkding")
        self.assertNotContains(response, "Mark this period read")

    def test_saved_links_uses_the_snapshot_that_was_sent_to_linkding(self) -> None:
        self.saved_article.title = "Title changed after saving"
        self.saved_article.url = "https://example.com/changed-after-saving"
        self.saved_article.save(update_fields=["title", "url"])

        response = self.client.get(reverse("saved-links"))

        self.assertContains(response, self.saved_record.title)
        self.assertContains(response, f'href="{self.saved_record.url}"')
        self.assertNotContains(response, self.saved_article.title)
        self.assertNotContains(response, self.saved_article.url)

    def test_archived_mark_unread_ajax_removes_card_from_view(self) -> None:
        response = self.client.post(
            reverse("mark-article", args=[model_id(self.read_article)]),
            {"state": "unread", "remove_on_success": "true"},
            headers={"x-requested-with": "XMLHttpRequest"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"message": "Marked article unread.", "level": "success", "remove": True},
        )

    def test_mark_period_read_overrides_unread_state_everywhere(self) -> None:
        today = timezone.localdate()
        ArticleReadState.objects.create(
            user=self.user, article=self.unread_article, is_read=False
        )

        response = self.client.post(
            reverse("mark-period-read"),
            {
                "scope": ReadScope.DAY,
                "period_start": today.isoformat(),
                "period_end": today.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ArticleReadState.objects.get(
                user=self.user, article=self.unread_article
            ).is_read
        )
        response = self.client.get(reverse("week"))
        self.assertNotContains(response, "Unread article")
        response = self.client.get(reverse("month"))
        self.assertNotContains(response, "Unread article")

    def test_digest_json_hides_read_and_saved_articles(self) -> None:
        response = self.client.get(reverse("digest-json"))

        self.assertEqual(response.status_code, 200)
        titles = [article["title"] for article in response.json()["articles"]]
        self.assertEqual(titles, ["Unread article"])

    def test_bulk_read_marker_does_not_hide_articles_fetched_later(self) -> None:
        today = timezone.localdate()
        marker = BulkReadMarker.objects.create(
            user=self.user,
            scope=ReadScope.WEEK,
            period_start=today - timedelta(days=today.weekday()),
            period_end=today + timedelta(days=6 - today.weekday()),
        )
        BulkReadMarker.objects.filter(id=model_id(marker)).update(
            marked_read_at=self.unread_article.fetched_at - timedelta(seconds=1)
        )

        response = self.client.get(reverse("today"))

        self.assertContains(response, "Unread article")

    def test_mark_period_read_updates_existing_marker_timestamp(self) -> None:
        today = timezone.localdate()
        marker = BulkReadMarker.objects.create(
            user=self.user,
            scope=ReadScope.DAY,
            period_start=today,
            period_end=today,
        )
        BulkReadMarker.objects.filter(id=model_id(marker)).update(
            marked_read_at=self.unread_article.fetched_at - timedelta(seconds=1)
        )

        with frozen_time(self.clock.now + timedelta(seconds=1)):
            response = self.client.post(
                reverse("mark-period-read"),
                {
                    "scope": ReadScope.DAY,
                    "period_start": today.isoformat(),
                    "period_end": today.isoformat(),
                },
            )

        self.assertEqual(response.status_code, 302)
        marker.refresh_from_db()
        self.assertGreater(marker.marked_read_at, self.unread_article.fetched_at)
        response = self.client.get(reverse("today"))
        self.assertNotContains(response, "Unread article")

    def test_ajax_mark_read_returns_inline_message_payload(self) -> None:
        response = self.client.post(
            reverse("mark-article", args=[model_id(self.unread_article)]),
            {"state": "read"},
            headers={"x-requested-with": "XMLHttpRequest"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"message": "Marked article read.", "level": "success", "remove": True},
        )
