"""Drain owed Linkding bookmark deliveries."""

from __future__ import annotations

from argparse import ArgumentParser
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from feeds.models import LinkdingDelivery
from feeds.services import deliver_pending_saved_articles


class Command(BaseCommand):
    help = "Attempt every Linkding bookmark delivery that is owed and due."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Attempt at most this many deliveries.",
        )
        parser.add_argument(
            "--requeue-failed",
            action="store_true",
            help=(
                "First return permanently failed deliveries to the queue. "
                "This will post their bookmarks to Linkding."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["requeue_failed"]:
            requeued = LinkdingDelivery.objects.filter(
                state=LinkdingDelivery.State.PERMANENT_FAILED
            ).update(
                state=LinkdingDelivery.State.QUEUED,
                attempts=0,
                next_attempt_at=timezone.now(),
                error_class="",
                error_message="",
                updated_at=timezone.now(),
            )
            self.stdout.write(f"requeued {requeued} permanently failed deliveries")

        tally = deliver_pending_saved_articles(
            base_url=settings.LINKDING_URL,
            token=settings.LINKDING_TOKEN,
            limit=options["limit"],
        )
        self.stdout.write(
            f"attempted={tally.attempted} succeeded={tally.succeeded} "
            f"transient={tally.transient} permanent={tally.permanent}"
        )
