from __future__ import annotations

import hmac
from datetime import timedelta
from typing import Any, cast
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    ApiToken,
    Article,
    ArticleReadState,
    BulkReadMarker,
    Category,
    Feed,
    NewsletterIssue,
    ReadScope,
    SavedArticle,
    UserPreference,
)
from .services import (
    LINKDING_TOREAD_TAG,
    RefreshResult,
    import_opml,
    refresh_feed,
    save_to_linkding,
)


def model_id(model: Any) -> int:
    return cast(int, model.id)


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
)
class DigestArticleVisibilityTests(TestCase):
    def setUp(self) -> None:
        user_model = cast(Any, get_user_model())
        self.user = cast(Any, user_model.objects).create_user(username="reader")
        self.feed = Feed.objects.create(
            title="Example Feed", feed_url="https://example.com/feed.xml"
        )
        self.unread_article = Article.objects.create(
            feed=self.feed,
            title="Unread article",
            url="https://example.com/unread",
            guid="unread",
            published_at=timezone.now(),
        )
        self.read_article = Article.objects.create(
            feed=self.feed,
            title="Read article",
            url="https://example.com/read",
            guid="read",
            published_at=timezone.now(),
        )
        self.saved_article = Article.objects.create(
            feed=self.feed,
            title="Saved article",
            url="https://example.com/saved",
            guid="saved",
            published_at=timezone.now(),
        )
        ArticleReadState.objects.create(
            user=self.user, article=self.read_article, is_read=True
        )
        SavedArticle.objects.create(
            user=self.user,
            article=self.saved_article,
            url=self.saved_article.url,
            title=self.saved_article.title,
            feed=self.feed,
        )
        self.client.force_login(self.user)

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

    @patch("feeds.services.save_to_linkding")
    def test_ajax_save_returns_inline_message_payload(
        self, mock_save_to_linkding
    ) -> None:
        response = self.client.post(
            reverse("save-article", args=[model_id(self.unread_article)]),
            headers={"x-requested-with": "XMLHttpRequest"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "message": "Saved article to Linkding and Daily Firehose.",
                "level": "success",
                "remove": True,
            },
        )
        mock_save_to_linkding.assert_called_once()

    @patch("feeds.services.requests.post")
    def test_linkding_save_uses_article_url_and_toread_tag(self, mock_post) -> None:
        mock_post.return_value.raise_for_status.return_value = None

        save_to_linkding(
            base_url="https://linkding.example.com",
            token="x",
            article=self.unread_article,
        )

        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["url"], "https://example.com/unread")
        self.assertEqual(payload["tag_names"], [LINKDING_TOREAD_TAG])

    @patch("feeds.services.requests.post")
    def test_linkding_save_omits_comments_only_summary(self, mock_post) -> None:
        mock_post.return_value.raise_for_status.return_value = None
        self.unread_article.summary = (
            '<p><a href="https://lobste.rs/s/vkoa7r/story">Comments</a></p>'
        )

        save_to_linkding(
            base_url="https://linkding.example.com",
            token="x",
            article=self.unread_article,
        )

        self.assertEqual(mock_post.call_args.kwargs["json"]["description"], "")

    @patch("feeds.services.feedparser.parse")
    def test_refresh_feed_prefers_article_url_over_comments_guid(
        self, mock_parse
    ) -> None:
        feed = Feed.objects.create(title="Lobsters", feed_url="https://lobste.rs/rss")
        mock_parse.return_value = {
            "feed": {"title": "Lobsters"},
            "entries": [
                {
                    "id": "https://lobste.rs/s/vkoa7r",
                    "link": "https://example.com/article",
                    "links": [
                        {
                            "rel": "alternate",
                            "type": "text/html",
                            "href": "https://example.com/article",
                        }
                    ],
                    "title": "An article",
                    "summary": '<p><a href="https://lobste.rs/s/vkoa7r/story">Comments</a></p>',
                }
            ],
        }

        refresh_feed(feed)

        article = Article.objects.get(feed=feed, title="An article")
        self.assertEqual(article.url, "https://example.com/article")
        self.assertEqual(article.guid, "https://lobste.rs/s/vkoa7r")

    @patch("feeds.services.feedparser.parse")
    def test_refresh_feed_prefers_alternate_original_url_over_intermediary_link(
        self, mock_parse
    ) -> None:
        feed = Feed.objects.create(
            title="Example Aggregator", feed_url="https://example.com/rss"
        )
        mock_parse.return_value = {
            "feed": {"title": "Example Aggregator"},
            "entries": [
                {
                    "id": "https://original.example.com/article",
                    "link": "https://daily-firehose.example.com/articles/123/",
                    "links": [
                        {
                            "rel": "alternate",
                            "type": "text/html",
                            "href": "https://original.example.com/article",
                        }
                    ],
                    "title": "An article",
                }
            ],
        }

        refresh_feed(feed)

        article = Article.objects.get(feed=feed, title="An article")
        self.assertEqual(article.url, "https://original.example.com/article")
        self.assertEqual(article.guid, "https://original.example.com/article")


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
)
class FeedListGroupingTests(TestCase):
    def setUp(self) -> None:
        user_model = cast(Any, get_user_model())
        self.user = cast(Any, user_model.objects).create_user(username="reader")
        self.client.force_login(self.user)

    def test_feed_list_shows_postmark_inbound_email_reminder(self) -> None:
        response = self.client.get(reverse("feeds"))

        self.assertContains(
            response,
            "95d8c50c7df8d1ca38d7a6f55ee5a311@inbound.postmarkapp.com",
        )
        self.assertContains(response, "To add a newsletter")

    def test_feeds_are_grouped_by_category(self) -> None:
        tech = Category.objects.create(name="Tech", slug="tech")
        news = Category.objects.create(name="News", slug="news")
        Feed.objects.create(
            title="Python Weekly",
            feed_url="https://example.com/python.xml",
            category=tech,
        )
        Feed.objects.create(
            title="Local News", feed_url="https://example.com/news.xml", category=news
        )
        Feed.objects.create(
            title="Loose Feed", feed_url="https://example.com/loose.xml"
        )

        response = self.client.get(reverse("feeds"))
        content = response.content.decode()

        self.assertContains(response, "News")
        self.assertContains(response, "Tech")
        self.assertContains(response, "Uncategorized")
        self.assertLess(content.index("News"), content.index("Local News"))
        self.assertLess(content.index("Tech"), content.index("Python Weekly"))
        self.assertLess(content.index("Uncategorized"), content.index("Loose Feed"))
        self.assertContains(response, "data-feed-list-item", count=3)
        self.assertContains(response, "data-open-feed", count=3)


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
)
class RefreshFeedsFeedbackTests(TestCase):
    def setUp(self) -> None:
        user_model = cast(Any, get_user_model())
        self.user = cast(Any, user_model.objects).create_user(username="reader")
        self.feed_with_new_articles = Feed.objects.create(
            title="Feed with new articles",
            feed_url="https://example.com/new.xml",
        )
        self.feed_without_new_articles = Feed.objects.create(
            title="Feed without new articles",
            feed_url="https://example.com/old.xml",
        )
        self.client.force_login(self.user)

    @patch("feeds.views.refresh_active_feeds")
    def test_refresh_feedback_includes_feeds_with_new_articles(
        self, mock_refresh_active_feeds
    ) -> None:
        mock_refresh_active_feeds.return_value = [
            RefreshResult(feed=self.feed_with_new_articles, created=3, updated=2),
            RefreshResult(feed=self.feed_without_new_articles, created=0, updated=4),
        ]

        response = self.client.post(reverse("refresh-feeds"), follow=True)

        self.assertContains(
            response,
            "Refresh complete: checked 2 feeds; 1 feeds had new articles; 3 new articles; 6 existing articles updated.",
        )
        mock_refresh_active_feeds.assert_called_once_with()


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
    LINKDING_TOKEN="",
)
class ApiTests(TestCase):
    def setUp(self) -> None:
        user_model = cast(Any, get_user_model())
        self.user = cast(Any, user_model.objects).create_user(username="api-reader")
        self.token, self.key = ApiToken.create_token(user=self.user, name="test agent")
        self.feed = Feed.objects.create(
            title="Example Feed", feed_url="https://example.com/feed.xml"
        )
        self.article = Article.objects.create(
            feed=self.feed,
            title="Morning article",
            url="https://example.com/morning",
            guid="morning",
            published_at=timezone.now(),
        )

    def auth_headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.key}"}

    def test_api_requires_token(self) -> None:
        response = self.client.get(reverse("api-morning-briefing"))

        self.assertEqual(response.status_code, 401)

    def test_morning_briefing_lists_actionable_articles(self) -> None:
        response = self.client.get(
            reverse("api-morning-briefing"), headers=self.auth_headers()
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["date"], timezone.localdate().isoformat())
        self.assertEqual(payload["articles"][0]["title"], "Morning article")
        self.assertFalse(payload["articles"][0]["is_read"])
        self.assertFalse(payload["articles"][0]["is_saved"])

    def test_api_can_mark_article_read(self) -> None:
        response = self.client.post(
            reverse("api-article-read", args=[model_id(self.article)]),
            data={"is_read": True},
            content_type="application/json",
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            ArticleReadState.objects.get(user=self.user, article=self.article).is_read
        )
        self.assertTrue(response.json()["article"]["is_read"])

    def test_api_can_save_article(self) -> None:
        response = self.client.post(
            reverse("api-article-saved", args=[model_id(self.article)]),
            data={"is_saved": True, "notes": "Brief me on this."},
            content_type="application/json",
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        saved = SavedArticle.objects.get(user=self.user, article=self.article)
        self.assertEqual(saved.notes, "Brief me on this.")
        self.assertTrue(response.json()["article"]["is_saved"])

    def test_api_can_update_focus_mode_preference(self) -> None:
        response = self.client.patch(
            reverse("api-preferences"),
            data={"theme": "dracula", "compact": True, "focus_mode": True},
            content_type="application/json",
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["preferences"]
        self.assertEqual(
            payload,
            {"theme": "dracula", "compact": True, "focus_mode": True},
        )
        preferences = UserPreference.objects.get(user=self.user)
        self.assertEqual(preferences.theme, "dracula")
        self.assertTrue(preferences.compact)
        self.assertTrue(preferences.focus_mode)

    @override_settings(
        AGENT_LINK_SECRET="test-secret",
        AGENT_LINK_USERNAME="api-reader",
    )
    def test_signed_save_and_go_link_saves_and_redirects(self) -> None:
        article_id = model_id(self.article)
        signature = hmac.new(
            b"test-secret",
            f"save-and-go:{article_id}".encode(),
            "sha256",
        ).hexdigest()

        response = self.client.get(
            reverse("api-article-save-and-go", args=[article_id]),
            {"sig": signature},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], self.article.url)
        self.assertTrue(
            SavedArticle.objects.filter(user=self.user, article=self.article).exists()
        )

    @override_settings(
        AGENT_LINK_SECRET="test-secret",
        AGENT_LINK_USERNAME="api-reader",
    )
    def test_signed_mark_period_read_link_marks_today_read(self) -> None:
        signature = hmac.new(
            b"test-secret",
            b"mark-period-read:day",
            "sha256",
        ).hexdigest()

        response = self.client.get(
            reverse("api-mark-period-read-and-go"),
            {"scope": "day", "sig": signature},
        )

        today = timezone.localdate()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], reverse("today"))
        self.assertTrue(
            BulkReadMarker.objects.filter(
                user=self.user,
                scope=ReadScope.DAY,
                feed=None,
                period_start=today,
                period_end=today,
            ).exists()
        )


@override_settings(
    POSTMARK_INBOUND_SECRET="inbound-secret",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
)
class PostmarkInboundNewsletterTests(TestCase):
    def setUp(self) -> None:
        user_model = cast(Any, get_user_model())
        self.user = cast(Any, user_model.objects).create_user(username="reader")

    def payload(self, *, message_id: str = "message-1") -> dict[str, Any]:
        return {
            "MessageID": message_id,
            "Subject": "Daily newsletter",
            "From": "sender@example.com",
            "FromFull": {"Name": "Newsletter Sender", "Email": "sender@example.com"},
            "To": "reader@example.com",
            "ToFull": [{"Name": "Reader", "Email": "reader@example.com"}],
            "HtmlBody": '<h1>Hello</h1><script>alert("x")</script><p><a href="https://example.com/story">Story</a></p>',
            "TextBody": "Hello\nStory: https://example.com/story",
        }

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
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex")
        self.assertContains(response, '<meta name="robots" content="noindex">')
        self.assertContains(response, "<h1>Hello</h1>")
        self.assertNotContains(response, 'alert("x")')
        self.assertContains(response, 'target="_blank"')
        self.assertContains(response, "noopener noreferrer")

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


class OPMLImportCategoryTests(TestCase):
    def test_import_uses_parent_outlines_as_categories(self) -> None:
        content = b"""
        <opml version="2.0">
          <body>
            <outline text="Python">
              <outline title="PyPI Blog" text="PyPI Blog" xmlUrl="https://blog.pypi.org/feed.xml" htmlUrl="https://blog.pypi.org/" />
            </outline>
          </body>
        </opml>
        """

        result = import_opml(content)

        self.assertEqual(result.created, 1)
        feed = Feed.objects.get(feed_url="https://blog.pypi.org/feed.xml")
        self.assertIsNotNone(feed.category)
        assert feed.category is not None
        self.assertEqual(feed.category.name, "Python")

    def test_reimport_updates_existing_feed_without_duplicate(self) -> None:
        old_category = Category.objects.create(
            name="Unknown Category", slug="unknown-category"
        )
        Feed.objects.create(
            title="Old title",
            feed_url="https://example.com/feed.xml",
            site_url="https://old.example.com/",
            category=old_category,
        )
        content = b"""
        <opml version="2.0">
          <body>
            <outline text="Updated Category">
              <outline title="New title" text="New title" xmlUrl="https://example.com/feed.xml" htmlUrl="https://new.example.com/" />
            </outline>
          </body>
        </opml>
        """

        result = import_opml(content)

        self.assertEqual(result.created, 0)
        self.assertEqual(result.updated, 1)
        self.assertEqual(
            Feed.objects.filter(feed_url="https://example.com/feed.xml").count(), 1
        )
        feed = Feed.objects.get(feed_url="https://example.com/feed.xml")
        self.assertEqual(feed.title, "New title")
        self.assertEqual(feed.site_url, "https://new.example.com/")
        self.assertIsNotNone(feed.category)
        assert feed.category is not None
        self.assertEqual(feed.category.name, "Updated Category")
