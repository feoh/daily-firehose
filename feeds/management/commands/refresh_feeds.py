from django.core.management.base import BaseCommand

from feeds.services import refresh_active_feeds, safe_feed_title


class Command(BaseCommand):
    help = "Fetch active feeds and store new articles."

    def handle(self, *args, **options):
        results = refresh_active_feeds()
        for result in results:
            title = safe_feed_title(result.feed.title)
            if result.skipped:
                self.stdout.write(
                    self.style.WARNING(f"{title}: skipped until {result.next_retry_at}")
                )
            elif result.success:
                self.stdout.write(
                    f"{title}: {result.created} created, {result.updated} updated"
                )
            else:
                self.stdout.write(
                    self.style.ERROR(
                        f"{title}: failed [{result.error_code}] {result.error_message}"
                    )
                )

        checked = sum(not result.skipped for result in results)
        succeeded = sum(result.success for result in results)
        failed = sum(not result.success and not result.skipped for result in results)
        skipped = sum(result.skipped for result in results)
        summary = (
            f"Refresh complete: checked {checked}; succeeded {succeeded}; "
            f"failed {failed}; skipped {skipped}."
        )
        style = self.style.WARNING if failed or skipped else self.style.SUCCESS
        self.stdout.write(style(summary))
