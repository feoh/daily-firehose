from __future__ import annotations

from unittest.mock import patch

from django.urls import reverse

from ..feed_fetch import FeedFetchError
from ..models import Feed
from .support.base import StaticFilesTestCase
from .support.builders import (
    build_category,
    build_feed,
    build_user,
)


class FeedListGroupingTests(StaticFilesTestCase):
    def setUp(self) -> None:
        self.user = build_user()
        self.client.force_login(self.user)

    @patch(
        "feeds.views.discover_feed_metadata",
        side_effect=FeedFetchError(code="timeout", message="Feed request timed out."),
    )
    def test_feed_creation_shows_fetch_error_without_writes(
        self, mock_discover
    ) -> None:
        feed_url = "https://feeds.example/blocked.xml"

        response = self.client.post(reverse("feeds"), {"feed_url": feed_url})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Feed request timed out.")
        self.assertFalse(Feed.objects.filter(feed_url=feed_url).exists())
        mock_discover.assert_called_once_with(feed_url)

    def test_feed_list_shows_postmark_inbound_email_reminder(self) -> None:
        response = self.client.get(reverse("feeds"))

        self.assertContains(
            response,
            "95d8c50c7df8d1ca38d7a6f55ee5a311@inbound.postmarkapp.com",
        )
        self.assertContains(response, "To add a newsletter")
        self.assertContains(response, "Copy to Clipboard")
        self.assertContains(response, "data-copy-to-clipboard")

    def test_feeds_are_grouped_by_category(self) -> None:
        tech = build_category(name="Tech", slug="tech")
        news = build_category(name="News", slug="news")
        build_feed(
            title="Python Weekly",
            feed_url="https://example.com/python.xml",
            category=tech,
        )
        build_feed(
            title="Local News", feed_url="https://example.com/news.xml", category=news
        )
        build_feed(title="Loose Feed", feed_url="https://example.com/loose.xml")

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
