from __future__ import annotations

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from feeds.models import ApiToken


class Command(BaseCommand):
    help = "Create a Daily Firehose API token for a user. The token is displayed once."

    def add_arguments(self, parser) -> None:
        parser.add_argument("username")
        parser.add_argument(
            "--name", default="agent", help="Human-readable token name."
        )
        parser.add_argument(
            "--capabilities",
            default=",".join(ApiToken.LEGACY_CAPABILITIES),
            help=(
                "Comma-separated capabilities: read, write. Use 'read' for a "
                "briefing token that cannot change anything."
            ),
        )

    def handle(self, *args, **options) -> None:
        username = options["username"]
        name = options["name"]
        capabilities = [
            value.strip() for value in options["capabilities"].split(",") if value.strip()
        ]
        user_model = apps.get_model(settings.AUTH_USER_MODEL)
        try:
            user = user_model.objects.get(username=username)
        except user_model.DoesNotExist as exc:
            raise CommandError(f"User {username!r} does not exist.") from exc
        ApiToken.objects.filter(user=user, name=name).delete()
        try:
            token, key = ApiToken.create_token(
                user=user, name=name, capabilities=capabilities
            )
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Created API token {token.name!r} for {username} with "
                f"capabilities {', '.join(token.capabilities)}."
            )
        )
        self.stdout.write("Use this bearer token; it will not be shown again:")
        self.stdout.write(key)
