"""Validated, atomic mutation commands.

The single write path for read-state changes and refresh orchestration. The
session HTML controllers, the bearer JSON API, and the signed-link actions all
route their writes through this module, so the three surfaces cannot validate
the same change differently or commit only half of it.

Adapters keep their own wire-format parsing and error rendering; a command takes
already-typed arguments, re-validates them against the domain as a backstop, and
either commits the whole change or none of it. Domain rejections surface as
Django ``ValidationError`` so every adapter maps one exception type.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Article, ArticleReadState, BulkReadMarker, Feed, ReadScope
from .queries import PERIOD_SCOPES, articles_between, mark_articles_read
from .services import RefreshResult, refresh_active_feeds


@dataclass(frozen=True)
class ArticleReadResult:
    article: Article
    is_read: bool


@dataclass(frozen=True)
class PeriodReadResult:
    scope: str
    period_start: date
    period_end: date
    marked_read_at: datetime


@dataclass(frozen=True)
class FeedReadResult:
    feed: Feed
    marked_read_at: datetime


@dataclass(frozen=True)
class RefreshTally:
    """Every aggregate both refresh adapters report, counted once."""

    results: list[RefreshResult]
    failures: list[RefreshResult]
    skipped: list[RefreshResult]
    superseded: list[RefreshResult]
    attempted: list[RefreshResult]

    @property
    def checked(self) -> int:
        return len(self.results)

    @property
    def succeeded(self) -> int:
        return sum(result.status == "succeeded" for result in self.results)

    @property
    def feeds_with_new_articles(self) -> int:
        return sum(
            1 for result in self.results if result.success and result.created > 0
        )

    @property
    def created(self) -> int:
        return sum(result.created for result in self.results)

    @property
    def updated(self) -> int:
        return sum(result.updated for result in self.results)


def parse_period_scope(value: object) -> str:
    if value not in set(PERIOD_SCOPES):
        raise ValidationError({"scope": "scope must be one of: day, week, month."})
    return str(value)


def parse_iso_date(value: object, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationError({field: f"{field} must be an ISO 8601 date."}) from exc


def mark_article_read(*, user, article: Article, is_read: bool) -> ArticleReadResult:
    state = ArticleReadState(user=user, article=article, is_read=is_read)
    state.full_clean(validate_unique=False, validate_constraints=False)
    ArticleReadState.objects.update_or_create(
        user=user, article=article, defaults={"is_read": is_read}
    )
    return ArticleReadResult(article=article, is_read=is_read)


def mark_period_read(
    *, user, scope: str, period_start: date, period_end: date
) -> PeriodReadResult:
    marker = BulkReadMarker(
        user=user,
        scope=scope,
        feed=None,
        period_start=period_start,
        period_end=period_end,
    )
    marker.full_clean(validate_unique=False, validate_constraints=False)
    marked_read_at = timezone.now()
    # The cutoff and the marker are one change: a reader must never end up with
    # articles materialized read but no marker to explain it, or the reverse.
    with transaction.atomic():
        mark_articles_read(
            user,
            articles_between(period_start, period_end).filter(
                fetched_at__lte=marked_read_at
            ),
        )
        BulkReadMarker.objects.update_or_create(
            user=user,
            scope=scope,
            feed=None,
            period_start=period_start,
            period_end=period_end,
            defaults={"marked_read_at": marked_read_at},
        )
    return PeriodReadResult(
        scope=scope,
        period_start=period_start,
        period_end=period_end,
        marked_read_at=marked_read_at,
    )


def mark_feed_read(*, user, feed: Feed) -> FeedReadResult:
    marker = BulkReadMarker(
        user=user,
        scope=ReadScope.FEED,
        feed=feed,
        period_start=None,
        period_end=None,
    )
    marker.full_clean(validate_unique=False, validate_constraints=False)
    marked_read_at = timezone.now()
    with transaction.atomic():
        mark_articles_read(
            user,
            Article.objects.filter(feed=feed, fetched_at__lte=marked_read_at),
        )
        BulkReadMarker.objects.update_or_create(
            user=user,
            scope=ReadScope.FEED,
            feed=feed,
            period_start=None,
            period_end=None,
            defaults={"marked_read_at": marked_read_at},
        )
    return FeedReadResult(feed=feed, marked_read_at=marked_read_at)


def run_feed_refresh() -> RefreshTally:
    results = refresh_active_feeds()
    return RefreshTally(
        results=results,
        failures=[result for result in results if result.status == "failed"],
        skipped=[result for result in results if result.status == "skipped"],
        superseded=[result for result in results if result.status == "superseded"],
        attempted=[result for result in results if result.status != "skipped"],
    )
