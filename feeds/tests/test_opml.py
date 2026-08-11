from __future__ import annotations

from django.test import TestCase

from ..models import Category, Feed
from ..services import import_opml


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
