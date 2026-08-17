from __future__ import annotations

import hashlib
import secrets
import uuid
from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class JobRun(models.Model):
    """One background job cycle, and the lock that keeps cycles from overlapping.

    The partial unique constraint on running rows is the overlap lock itself: a
    second worker's insert conflicts instead of starting a concurrent cycle.
    """

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        INTERRUPTED = "interrupted", "Interrupted"

    name = models.CharField(max_length=64)
    correlation_id = models.CharField(max_length=64, blank=True)
    owner = models.CharField(max_length=128, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.RUNNING
    )
    started_at = models.DateTimeField(default=timezone.now)
    heartbeat_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(blank=True, null=True)
    checked = models.PositiveIntegerField(default=0)
    attempted = models.PositiveIntegerField(default=0)
    succeeded = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    skipped = models.PositiveIntegerField(default=0)
    superseded = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at", "-id"]
        indexes = [models.Index(fields=["name", "-started_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(status="running"),
                name="unique_running_job_run",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} {self.status} at {self.started_at.isoformat()}"


class Category(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.name


class Feed(models.Model):
    title = models.CharField(max_length=255)
    feed_url = models.URLField(unique=True)
    site_url = models.URLField(blank=True)
    description = models.TextField(blank=True)
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, blank=True, null=True, related_name="feeds"
    )
    is_active = models.BooleanField(default=True)
    last_fetched_at = models.DateTimeField(blank=True, null=True)
    last_attempt_at = models.DateTimeField(blank=True, null=True)
    last_error_code = models.CharField(max_length=64, blank=True)
    last_error_message = models.TextField(blank=True)
    consecutive_failures = models.PositiveIntegerField(default=0)
    next_retry_at = models.DateTimeField(blank=True, null=True)
    refresh_generation = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title", "feed_url"]

    def __str__(self) -> str:
        return self.title or self.feed_url


class Article(models.Model):
    feed = models.ForeignKey(Feed, on_delete=models.CASCADE, related_name="articles")
    title = models.CharField(max_length=500)
    url = models.URLField(max_length=1000)
    guid = models.CharField(max_length=1000)
    author = models.CharField(max_length=255, blank=True)
    summary = models.TextField(blank=True)
    published_at = models.DateTimeField(default=timezone.now)
    fetched_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at", "title"]
        # Every digest selects a fetched_at window, and feed markers narrow that
        # window to one feed.
        indexes = [
            models.Index(fields=["fetched_at"]),
            models.Index(fields=["feed", "fetched_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["feed", "guid"], name="unique_article_guid_per_feed"
            ),
            models.UniqueConstraint(
                fields=["feed", "url"], name="unique_article_url_per_feed"
            ),
        ]

    def __str__(self) -> str:
        return self.title


class NewsletterIssue(models.Model):
    article = models.OneToOneField(
        Article, on_delete=models.CASCADE, related_name="newsletter_issue"
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    message_id = models.CharField(max_length=1000, unique=True)
    from_email = models.EmailField(blank=True)
    from_name = models.CharField(max_length=255, blank=True)
    to_email = models.EmailField(blank=True)
    subject = models.CharField(max_length=500)
    html_body = models.TextField(blank=True)
    text_body = models.TextField(blank=True)
    # Derived at ingest so a public page never re-sanitizes attacker-supplied
    # markup on every request.
    sanitized_html = models.TextField(blank=True)
    received_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-received_at", "subject"]

    def __str__(self) -> str:
        return self.subject


class SavedArticle(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_articles",
    )
    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="saves")
    url = models.URLField(max_length=1000)
    title = models.CharField(max_length=500)
    feed = models.ForeignKey(Feed, on_delete=models.SET_NULL, blank=True, null=True)
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, blank=True, null=True
    )
    linkding_saved = models.BooleanField(default=False)
    linkding_error = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    interest_score = models.FloatField(blank=True, null=True)
    saved_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-saved_at"]
        indexes = [models.Index(fields=["user", "-saved_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "article"], name="unique_saved_article"
            )
        ]

    def __str__(self) -> str:
        return self.title


class LinkdingDelivery(models.Model):
    """Remote delivery of one local save intent.

    Separating this from ``SavedArticle`` is what makes a failed bookmark
    recoverable: the local row records that the user wants the article, this row
    records how far the remote side has got, so a transient outage leaves work
    owed instead of silently dropping it.

    There is deliberately no ``attempting`` state. A worker claims a row by
    compare-and-set on ``next_attempt_at``, so a process that dies mid-attempt
    simply becomes due again instead of stranding a row in a state that nothing
    is left alive to clear.
    """

    class State(models.TextChoices):
        QUEUED = "queued", "Queued"
        SUCCEEDED = "succeeded", "Succeeded"
        TRANSIENT_FAILED = "transient_failed", "Transient failure"
        PERMANENT_FAILED = "permanent_failed", "Permanent failure"

    class ErrorClass(models.TextChoices):
        NOT_CONFIGURED = "not_configured", "Not configured"
        TIMEOUT = "timeout", "Timeout"
        CONNECTION = "connection", "Connection error"
        RATE_LIMITED = "rate_limited", "Rate limited"
        SERVER_ERROR = "server_error", "Remote server error"
        AUTH = "auth", "Rejected credentials"
        CLIENT_ERROR = "client_error", "Rejected request"
        INVALID_RESPONSE = "invalid_response", "Unreadable response"
        URL_MISMATCH = "url_mismatch", "Bookmark URL mismatch"
        UNEXPECTED = "unexpected", "Unexpected error"
        UNKNOWN = "unknown", "Unknown (predates delivery tracking)"

    RETRYABLE_STATES: ClassVar[tuple[str, ...]] = (
        State.QUEUED,
        State.TRANSIENT_FAILED,
    )

    saved_article = models.OneToOneField(
        SavedArticle, on_delete=models.CASCADE, related_name="delivery"
    )
    url = models.URLField(max_length=1000)
    state = models.CharField(max_length=20, choices=State.choices, default=State.QUEUED)
    attempts = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(blank=True, null=True)
    next_attempt_at = models.DateTimeField(blank=True, null=True)
    delivered_at = models.DateTimeField(blank=True, null=True)
    bookmark_id = models.CharField(max_length=64, blank=True)
    error_class = models.CharField(
        max_length=32, choices=ErrorClass.choices, blank=True
    )
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Linkding deliveries"
        # Serves the drain's "owed and due" scan.
        indexes = [models.Index(fields=["state", "next_attempt_at"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    state="succeeded",
                    delivered_at__isnull=False,
                    next_attempt_at__isnull=True,
                    error_class="",
                )
                | models.Q(
                    state="queued",
                    delivered_at__isnull=True,
                    next_attempt_at__isnull=False,
                )
                | models.Q(
                    state="transient_failed",
                    delivered_at__isnull=True,
                    next_attempt_at__isnull=False,
                )
                | models.Q(
                    state="permanent_failed",
                    delivered_at__isnull=True,
                    next_attempt_at__isnull=True,
                ),
                name="linkding_delivery_state_fields_agree",
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=["queued", "succeeded"])
                | ~models.Q(error_class=""),
                name="linkding_delivery_failure_is_classified",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.url} ({self.state})"


class ArticleReadState(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    article = models.ForeignKey(
        Article, on_delete=models.CASCADE, related_name="read_states"
    )
    is_read = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Serves the archived view's most-recently-read ordering.
        indexes = [models.Index(fields=["user", "is_read", "-updated_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "article"], name="unique_article_read_state"
            )
        ]

    def __str__(self) -> str:
        state = "read" if self.is_read else "unread"
        return f"{self.user} marked {self.article} {state}"


class ReadScope(models.TextChoices):
    DAY = "day", "Day"
    WEEK = "week", "Week"
    MONTH = "month", "Month"
    FEED = "feed", "Feed"


class BulkReadMarker(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    scope = models.CharField(max_length=10, choices=ReadScope.choices)
    feed = models.ForeignKey(Feed, on_delete=models.CASCADE, blank=True, null=True)
    period_start = models.DateField(blank=True, null=True)
    period_end = models.DateField(blank=True, null=True)
    marked_read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-marked_read_at"]
        # Marker relevance is filtered by owner and by when the mark was set.
        indexes = [models.Index(fields=["user", "marked_read_at"])]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        scope=ReadScope.FEED,
                        feed__isnull=False,
                        period_start__isnull=True,
                        period_end__isnull=True,
                    )
                    | models.Q(
                        scope__in=(
                            ReadScope.DAY,
                            ReadScope.WEEK,
                            ReadScope.MONTH,
                        ),
                        feed__isnull=True,
                        period_start__isnull=False,
                        period_end__isnull=False,
                        period_start__lte=models.F("period_end"),
                    )
                ),
                name="bulk_marker_valid_scope_shape",
            ),
            models.UniqueConstraint(
                fields=["user", "scope", "feed"],
                condition=models.Q(scope=ReadScope.FEED),
                name="unique_bulk_feed_marker",
            ),
            models.UniqueConstraint(
                fields=["user", "scope", "period_start", "period_end"],
                condition=models.Q(
                    scope__in=(ReadScope.DAY, ReadScope.WEEK, ReadScope.MONTH)
                ),
                name="unique_bulk_period_marker",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.scope == ReadScope.FEED:
            if self.feed_id is None:
                errors["feed"] = "Feed markers require a feed."
            if self.period_start is not None:
                errors["period_start"] = "Feed markers cannot have period dates."
            if self.period_end is not None:
                errors["period_end"] = "Feed markers cannot have period dates."
        elif self.scope in {ReadScope.DAY, ReadScope.WEEK, ReadScope.MONTH}:
            if self.feed_id is not None:
                errors["feed"] = "Period markers cannot have a feed."
            if self.period_start is None:
                errors["period_start"] = "Period markers require both dates."
            if self.period_end is None:
                errors["period_end"] = "Period markers require both dates."
            if (
                self.period_start is not None
                and self.period_end is not None
                and self.period_start > self.period_end
            ):
                errors["period_end"] = "Period end must not precede period start."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        if self.scope == ReadScope.FEED and self.feed:
            return f"{self.user} marked {self.feed} read"
        return f"{self.user} marked {self.scope} {self.period_start}–{self.period_end} read"


class ApiToken(models.Model):
    class Capability(models.TextChoices):
        READ = "read", "Read"
        WRITE = "write", "Write"

    # Tokens that predate capabilities were all-powerful, so the migration maps
    # them here rather than silently narrowing a token already in use.
    LEGACY_CAPABILITIES: ClassVar[list[str]] = [Capability.READ, Capability.WRITE]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_tokens"
    )
    name = models.CharField(max_length=120)
    key_hash = models.CharField(max_length=64, unique=True)
    prefix = models.CharField(max_length=12)
    capabilities = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "name"], name="unique_api_token_name_per_user"
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.user})"

    def clean(self) -> None:
        super().clean()
        valid = {choice[0] for choice in self.Capability.choices}
        if not isinstance(self.capabilities, list) or not self.capabilities:
            raise ValidationError(
                {"capabilities": "A token requires at least one capability."}
            )
        unknown = [value for value in self.capabilities if value not in valid]
        if unknown:
            raise ValidationError(
                {"capabilities": f"Unknown capabilities: {', '.join(map(str, unknown))}."}
            )
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValidationError(
                {"capabilities": "Capabilities must not repeat."}
            )

    def allows(self, capability: str) -> bool:
        return capability in self.capabilities

    @staticmethod
    def hash_key(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    @classmethod
    def create_token(
        cls, *, user, name: str, capabilities: list[str] | None = None
    ) -> tuple[ApiToken, str]:
        key = secrets.token_urlsafe(32)
        token = cls(
            user=user,
            name=name,
            key_hash=cls.hash_key(key),
            prefix=key[:12],
            # An explicitly empty list is a caller error, not a request for the
            # default, so it must reach validation rather than widen the token.
            capabilities=list(
                cls.LEGACY_CAPABILITIES if capabilities is None else capabilities
            ),
        )
        token.full_clean(validate_unique=False, validate_constraints=False)
        token.save()
        return token, key


class SignedActionNonce(models.Model):
    """One spent single-use signed action.

    The unique constraint is the replay check itself: consuming a nonce is an
    insert, so a second attempt conflicts instead of repeating the mutation.
    """

    nonce = models.CharField(max_length=64, unique=True)
    purpose = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-used_at"]
        indexes = [models.Index(fields=["expires_at"])]

    def __str__(self) -> str:
        return f"{self.purpose} nonce spent at {self.used_at.isoformat()}"


class UserPreference(models.Model):
    class Theme(models.TextChoices):
        SYSTEM = "system", "Use system setting"
        LIGHT = "light", "Accessible light"
        DARK = "dark", "Accessible dark"
        CATPPUCCIN_MOCHA = "catppuccin-mocha", "Catppuccin Mocha"
        TOKYO_NIGHT = "tokyo-night", "Tokyo Night"
        DRACULA = "dracula", "Dracula"
        GRUVBOX_DARK = "gruvbox-dark", "Gruvbox Dark"
        ONE_DARK = "one-dark", "One Dark"
        NORD = "nord", "Nord"
        SOLARIZED_DARK = "solarized-dark", "Solarized Dark"
        ROSE_PINE = "rose-pine", "Rosé Pine"
        KANAGAWA = "kanagawa", "Kanagawa"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="feed_preferences",
    )
    theme = models.CharField(max_length=32, choices=Theme.choices, default=Theme.SYSTEM)
    compact = models.BooleanField(default=False)
    focus_mode = models.BooleanField(default=False)

    def __str__(self) -> str:
        return f"Preferences for {self.user}"
