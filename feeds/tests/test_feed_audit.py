from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ..feed_fetch import FeedFetchError
from .support.builders import build_feed


class FeedAuditCommandTests(TestCase):
    @patch("feeds.management.commands.audit_feeds.validate_feed_metadata")
    def test_audit_checks_active_feeds_without_modifying_them(self, validate) -> None:
        active = build_feed(feed_url="https://example.com/feed.xml")
        build_feed(feed_url="https://example.com/inactive.xml", is_active=False)
        output = StringIO()

        call_command("audit_feeds", stdout=output)

        validate.assert_called_once_with(active.feed_url)
        self.assertIn("1 active feeds", output.getvalue())

    @patch("feeds.management.commands.audit_feeds.validate_feed_metadata")
    def test_audit_fails_when_a_stored_url_is_not_a_feed(self, validate) -> None:
        feed = build_feed(title="Website only", feed_url="https://example.com/")
        validate.side_effect = FeedFetchError(
            code="invalid_feed", message="The URL did not return a valid feed."
        )
        errors = StringIO()

        with self.assertRaises(CommandError) as raised:
            call_command("audit_feeds", stderr=errors)

        self.assertIn("1 of 1 active feeds", str(raised.exception))
        self.assertIn(f"feed_id={feed.pk}", errors.getvalue())
        self.assertEqual(feed.feed_url, "https://example.com/")
