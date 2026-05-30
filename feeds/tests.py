from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Article, ArticleReadState, Feed, SavedArticle


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class DigestArticleVisibilityTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="reader", password="password")
        self.feed = Feed.objects.create(title="Example Feed", feed_url="https://example.com/feed.xml")
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
        ArticleReadState.objects.create(user=self.user, article=self.read_article, is_read=True)
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
