from django.core.management.base import BaseCommand

from feeds.services import refresh_active_feeds


class Command(BaseCommand):
    help = "Fetch active feeds and store new articles."

    def handle(self, *args, **options):
        results = refresh_active_feeds()
        for result in results:
            self.stdout.write(
                f"{result.feed.title}: {result.created} created, {result.updated} updated"
            )
        self.stdout.write(self.style.SUCCESS(f"Refreshed {len(results)} feeds."))
