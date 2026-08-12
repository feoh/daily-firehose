from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from feeds.models import Article


class Command(BaseCommand):
    help = (
        "Audit Article identity duplicates without modifying data. "
        "Run before deploying Article identity/schema reconciliation changes."
    )

    def handle(self, *args: object, **options: object) -> None:
        duplicate_guids = list(
            Article.objects.values("feed_id", "guid")
            .annotate(article_count=Count("id"))
            .filter(article_count__gt=1)
            .order_by("feed_id", "guid")[:20]
        )
        duplicate_urls = list(
            Article.objects.values("feed_id", "url")
            .annotate(article_count=Count("id"))
            .filter(article_count__gt=1)
            .order_by("feed_id", "url")[:20]
        )
        if duplicate_guids or duplicate_urls:
            # Report only IDs/counts. Feed content may be sensitive and is not
            # needed to stop a deployment safely.
            conflicts = [
                f"feed_id={row['feed_id']} count={row['article_count']} kind=guid"
                for row in duplicate_guids
            ]
            conflicts.extend(
                f"feed_id={row['feed_id']} count={row['article_count']} kind=url"
                for row in duplicate_urls
            )
            raise CommandError(
                "Article identity duplicates detected; no data was changed: "
                + "; ".join(conflicts)
            )
        self.stdout.write(self.style.SUCCESS("Article identity audit passed."))
