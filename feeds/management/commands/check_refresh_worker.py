"""Report refresh-worker progress, exiting nonzero when it is stale."""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from feeds.jobs import job_health


class Command(BaseCommand):
    help = "Exit nonzero when the refresh worker has no recent healthy progress."

    def handle(self, *args: Any, **options: Any) -> None:
        health = job_health()
        self.stdout.write(json.dumps(health.payload(), sort_keys=True))
        if health.stale:
            raise CommandError(f"refresh worker is stale: status={health.status}")
