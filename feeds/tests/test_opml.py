from __future__ import annotations

from unittest.mock import patch
from xml.etree import ElementTree

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from ..models import Category, Feed
from ..services import (
    OPML_MAX_BYTES,
    OPML_MAX_DEPTH,
    OPML_MAX_OUTLINES,
    OPMLImportError,
    export_opml,
    import_opml,
)
from .support.base import StaticFilesTestCase
from .support.builders import build_user


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

    def test_identical_duplicate_outline_is_skipped_once(self) -> None:
        content = b"""<opml version="2.0"><body>
          <outline title="One" xmlUrl="https://example.com/one.xml" />
          <outline title="One" xmlUrl="https://example.com/one.xml" />
        </body></opml>"""

        result = import_opml(content)

        self.assertEqual((result.created, result.updated, result.skipped), (1, 0, 1))
        self.assertEqual(Feed.objects.count(), 1)

    def test_conflicting_duplicate_outline_rejects_without_writes(self) -> None:
        content = b"""<opml version="2.0"><body>
          <outline title="One" xmlUrl="https://example.com/one.xml" />
          <outline title="Changed" xmlUrl="https://example.com/one.xml" />
        </body></opml>"""

        with self.assertRaises(OPMLImportError):
            import_opml(content)

        self.assertEqual(Feed.objects.count(), 0)

    def test_later_invalid_outline_preserves_existing_rows_and_writes_nothing(
        self,
    ) -> None:
        existing = Feed.objects.create(
            title="Existing",
            feed_url="https://example.com/existing.xml",
            site_url="https://example.com/old",
        )
        content = b"""<opml version="2.0"><body>
          <outline title="Changed" xmlUrl="https://example.com/existing.xml"
                   htmlUrl="https://example.com/new" />
          <outline title="Bad" xmlUrl="javascript:alert(1)" />
        </body></opml>"""

        with self.assertRaises(OPMLImportError):
            import_opml(content)

        existing.refresh_from_db()
        self.assertEqual(
            (existing.title, existing.site_url), ("Existing", "https://example.com/old")
        )
        self.assertEqual(Feed.objects.count(), 1)

    def test_write_failure_rolls_back_prior_feed_and_category_writes(self) -> None:
        content = b"""<opml version="2.0"><body>
          <outline text="New Category">
            <outline title="One" xmlUrl="https://example.com/one.xml" />
            <outline title="Two" xmlUrl="https://example.com/two.xml" />
          </outline>
        </body></opml>"""
        original = Feed.objects.update_or_create
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValidationError("simulated late write failure")
            return original(*args, **kwargs)

        with (
            patch.object(Feed.objects, "update_or_create", side_effect=fail_second),
            self.assertRaises(ValidationError),
        ):
            import_opml(content)

        self.assertEqual(Feed.objects.count(), 0)
        self.assertEqual(Category.objects.count(), 0)

    def test_rejects_malformed_structure_invalid_fields_and_bounds(self) -> None:
        cases = (
            b"<not-opml><body /></not-opml>",
            b"<opml><body /><body /></opml>",
            b"<opml><body><head /></body></opml>",
            b"<opml><body><outline title='empty' /></body></opml>",
            b"<opml><body><outline title='x' xmlUrl='ftp://example.com/feed' /></body></opml>",
            b"<opml><body><outline title='x' xmlUrl='https://example.com/feed' htmlUrl='bad' /></body></opml>",
            (
                "<opml><body><outline text='"
                + "x" * 121
                + "'><outline title='x' xmlUrl='https://example.com/feed'/></outline></body></opml>"
            ).encode(),
            b"x" * (OPML_MAX_BYTES + 1),
        )

        for content in cases:
            with self.subTest(content=content[:80]), self.assertRaises(OPMLImportError):
                import_opml(content)
        self.assertEqual((Feed.objects.count(), Category.objects.count()), (0, 0))

    def test_rejects_dtd_and_entity_declarations(self) -> None:
        documents = (
            b"<!DOCTYPE opml SYSTEM 'https://example.com/evil.dtd'><opml><body /></opml>",
            b"<!DOCTYPE opml [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]><opml><body><outline title='&xxe;' xmlUrl='https://example.com/feed'/></body></opml>",
        )
        for content in documents:
            with self.subTest(content=content), self.assertRaises(OPMLImportError):
                import_opml(content)

    def test_accepts_exact_outline_limit_and_rejects_one_more(self) -> None:
        def document(count: int) -> bytes:
            outlines = "".join(
                f"<outline title='Feed {index}' xmlUrl='https://example.com/{index}.xml'/>"
                for index in range(count)
            )
            return f"<opml><body>{outlines}</body></opml>".encode()

        result = import_opml(document(OPML_MAX_OUTLINES))
        self.assertEqual(result.created, OPML_MAX_OUTLINES)

        with self.assertRaises(OPMLImportError):
            import_opml(document(OPML_MAX_OUTLINES + 1))
        self.assertEqual(Feed.objects.count(), OPML_MAX_OUTLINES)

    def test_accepts_exact_nesting_limit_and_rejects_one_more(self) -> None:
        def document(depth: int, url: str) -> bytes:
            feed = f"<outline title='Feed' xmlUrl='{url}'/>"
            # The service starts body children at depth 1, so depth - 1 category
            # wrappers place the feed outline at the requested depth.
            for index in range(depth - 1):
                feed = f"<outline title='Category {index}'>{feed}</outline>"
            return f"<opml><body>{feed}</body></opml>".encode()

        result = import_opml(
            document(OPML_MAX_DEPTH, "https://example.com/depth-32.xml")
        )
        self.assertEqual(result.created, 1)

        with self.assertRaises(OPMLImportError):
            import_opml(
                document(OPML_MAX_DEPTH + 1, "https://example.com/depth-33.xml")
            )
        self.assertEqual(Feed.objects.count(), 1)


class OPMLExportTests(TestCase):
    def test_export_is_deterministic_escaped_and_round_trips_supported_fields(
        self,
    ) -> None:
        category = Category.objects.create(name='A & "News"', slug="news")
        categorized = Feed.objects.create(
            title='Z <Feed> & "More"',
            feed_url="https://example.com/feed.xml?x=1&y=2",
            site_url="https://example.com/?x=1&y=2",
            category=category,
        )
        uncategorized = Feed.objects.create(
            title="A feed", feed_url="https://example.com/a.xml"
        )
        Feed.objects.create(
            title="Inactive",
            feed_url="https://example.com/inactive.xml",
            is_active=False,
        )

        first = export_opml()
        second = export_opml()

        self.assertEqual(first, second)
        self.assertIn("&amp;", first)
        self.assertIn("&lt;Feed&gt;", first)
        self.assertIn("&quot;News&quot;", first)
        parsed = ElementTree.fromstring(first)
        self.assertEqual(parsed.tag, "opml")
        self.assertNotIn("inactive.xml", first)

        original = {
            feed.feed_url: (feed.title, feed.site_url, feed.category_id)
            for feed in (categorized, uncategorized)
        }
        result = import_opml(first.encode())
        self.assertEqual((result.created, result.updated, result.skipped), (0, 2, 0))
        for feed_url, fields in original.items():
            feed = Feed.objects.get(feed_url=feed_url)
            self.assertEqual((feed.title, feed.site_url, feed.category_id), fields)

    def test_import_and_round_trip_preserve_exact_active_field_whitespace(self) -> None:
        content = b"""<opml version="2.0"><body>
          <outline title="  Category  &#9;">
            <outline title="  Feed title  &#9;" text="ignored"
                     xmlUrl="  https://example.com/spaced.xml  "
                     htmlUrl="  https://example.com/site  " />
          </outline>
        </body></opml>"""

        result = import_opml(content)

        self.assertEqual((result.created, result.updated, result.skipped), (1, 0, 0))
        feed = Feed.objects.select_related("category").get()
        self.assertEqual(feed.title, "  Feed title  \t")
        self.assertEqual(feed.feed_url, "https://example.com/spaced.xml")
        self.assertEqual(feed.site_url, "https://example.com/site")
        assert feed.category is not None
        self.assertEqual(feed.category.name, "  Category  \t")

        result = import_opml(export_opml().encode())

        self.assertEqual((result.created, result.updated, result.skipped), (0, 1, 0))
        feed.refresh_from_db()
        self.assertEqual(feed.title, "  Feed title  \t")
        self.assertEqual(feed.feed_url, "https://example.com/spaced.xml")
        self.assertEqual(feed.site_url, "https://example.com/site")
        assert feed.category is not None
        self.assertEqual(feed.category.name, "  Category  \t")


class OPMLRequestTests(StaticFilesTestCase):
    def setUp(self) -> None:
        self.user = build_user(username="opml-request-user")
        self.client.force_login(self.user)

    def test_invalid_upload_renders_inline_feedback_without_success_message(
        self,
    ) -> None:
        upload = SimpleUploadedFile("broken.opml", b"<opml><body><outline")

        response = self.client.post(reverse("opml-import"), {"opml_file": upload})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Upload a valid OPML file.")
        self.assertNotContains(response, "Imported OPML:")
        self.assertEqual(Feed.objects.count(), 0)

    def test_oversized_upload_is_rejected_by_form(self) -> None:
        upload = SimpleUploadedFile("large.opml", b"x" * (OPML_MAX_BYTES + 1))

        response = self.client.post(reverse("opml-import"), {"opml_file": upload})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Upload a valid OPML file (maximum 1 MiB).")
        self.assertEqual(Feed.objects.count(), 0)

    def test_valid_upload_preserves_progressive_redirect_and_message(self) -> None:
        upload = SimpleUploadedFile(
            "feeds.opml",
            b"<opml version='2.0'><body><outline title='One' xmlUrl='https://example.com/one.xml'/></body></opml>",
        )

        response = self.client.post(
            reverse("opml-import"), {"opml_file": upload}, follow=True
        )

        self.assertRedirects(response, reverse("feeds"))
        self.assertContains(response, "Imported OPML: 1 created, 0 updated, 0 skipped.")

    def test_export_response_has_exact_attachment_headers_and_valid_xml(self) -> None:
        Feed.objects.create(title="One", feed_url="https://example.com/one.xml")

        response = self.client.get(reverse("opml-export"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/x-opml; charset=utf-8")
        self.assertEqual(
            response["Content-Disposition"],
            'attachment; filename="daily-firehose-feeds.opml"',
        )
        ElementTree.fromstring(response.content)
