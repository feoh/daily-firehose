from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from feeds.feed_fetch import FeedFetchError
from feeds.models import Feed
from feeds.services import safe_feed_title, validate_feed_metadata


class Command(BaseCommand):
    help = (
        "Fetch every active subscription and verify that its stored URL returns "
        "a feed supported by the refresh service. No data is modified."
    )

    def handle(self, *args: object, **options: object) -> None:
        checked = 0
        failures: list[str] = []
        for feed in Feed.objects.filter(is_active=True).order_by("id"):
            checked += 1
            try:
                validate_feed_metadata(feed.feed_url)
            except FeedFetchError as exc:
                title = safe_feed_title(feed.title)
                failures.append(
                    f"feed_id={feed.pk} {title}: [{exc.code}] {exc}"
                )

        if failures:
            for failure in failures:
                self.stderr.write(self.style.ERROR(failure))
            raise CommandError(
                f"Feed audit failed: {len(failures)} of {checked} active feeds "
                "do not return a supported feed. No data was changed."
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Feed audit passed: {checked} active feeds return supported feeds."
            )
        )
