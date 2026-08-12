from django.core.management.base import BaseCommand, CommandError

from feeds.services import refresh_active_feeds, safe_feed_title


class Command(BaseCommand):
    help = "Fetch active feeds and store new articles."

    def handle(self, *args, **options):
        results = refresh_active_feeds()
        for result in results:
            title = safe_feed_title(result.feed.title)
            if result.status == "skipped":
                self.stdout.write(
                    self.style.WARNING(f"{title}: skipped until {result.next_retry_at}")
                )
            elif result.status == "superseded":
                self.stdout.write(
                    self.style.WARNING(
                        f"{title}: superseded [{result.error_code}] "
                        f"{result.error_message}"
                    )
                )
            elif result.status == "succeeded":
                self.stdout.write(
                    f"{title}: {result.created} created, {result.updated} updated"
                )
            else:
                self.stdout.write(
                    self.style.ERROR(
                        f"{title}: failed [{result.error_code}] {result.error_message}"
                    )
                )

        checked = len(results)
        attempted = sum(result.status != "skipped" for result in results)
        succeeded = sum(result.status == "succeeded" for result in results)
        failed = sum(result.status == "failed" for result in results)
        skipped = sum(result.status == "skipped" for result in results)
        superseded = sum(result.status == "superseded" for result in results)
        summary = (
            f"Refresh complete: checked {checked}; attempted {attempted}; "
            f"succeeded {succeeded}; failed {failed}; skipped {skipped}; "
            f"superseded {superseded}."
        )
        style = (
            self.style.WARNING
            if failed or skipped or superseded
            else self.style.SUCCESS
        )
        self.stdout.write(style(summary))
        if failed:
            raise CommandError(f"{failed} feed refresh failed")
