from __future__ import annotations

import hmac
from typing import Any, cast
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import ApiToken, Article, ArticleReadState, Category, Feed, SavedArticle
from .services import LINKDING_TOREAD_TAG, RefreshResult, import_opml, save_to_linkding


def model_id(model: Any) -> int:
    return int(model.id)


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

    def test_digest_json_hides_read_and_saved_articles(self) -> None:
        response = self.client.get(reverse("digest-json"))

        self.assertEqual(response.status_code, 200)
        titles = [article["title"] for article in response.json()["articles"]]
        self.assertEqual(titles, ["Unread article"])

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
    def test_linkding_save_applies_toread_tag(self, mock_post) -> None:
        mock_post.return_value.raise_for_status.return_value = None

        save_to_linkding(
            base_url="https://linkding.example.com",
            token="x",
            article=self.unread_article,
        )

        mock_post.assert_called_once()
        self.assertEqual(
            mock_post.call_args.kwargs["json"]["tag_names"], [LINKDING_TOREAD_TAG]
        )


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
