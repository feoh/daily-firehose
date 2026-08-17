"""Article visibility and read-state resolution.

The single source of truth for which articles a reader should see and which
count as read. Both the HTML views and the JSON API consume this module, so the
two surfaces cannot answer the same question differently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, cast

from django.conf import settings
from django.db.models import Max, Min, Q, QuerySet
from django.utils import timezone

from .models import (
    Article,
    ArticleReadState,
    BulkReadMarker,
    Feed,
    ReadScope,
    SavedArticle,
    UserPreference,
)

ARCHIVED_ARTICLE_LIMIT = 50
SAVED_ARTICLE_LIMIT = 50

PERIOD_SCOPES = (ReadScope.DAY, ReadScope.WEEK, ReadScope.MONTH)


def _pk(model: Any) -> int:
    return cast(int, model.id)


def digest_article_limit() -> int:
    return int(settings.DIGEST_ARTICLE_LIMIT)


def feed_article_limit() -> int:
    return int(settings.FEED_ARTICLE_LIMIT)


def week_bounds(day: date) -> tuple[date, date]:
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1) - timedelta(days=1)
    else:
        end = start.replace(month=start.month + 1) - timedelta(days=1)
    return start, end


def articles_between(
    start: date, end: date, feed: Feed | None = None
) -> QuerySet[Article]:
    queryset = Article.objects.select_related("feed", "feed__category").filter(
        fetched_at__date__gte=start,
        fetched_at__date__lte=end,
    )
    if feed is not None:
        queryset = queryset.filter(feed=feed)
    return queryset.order_by("feed__title", "-fetched_at", "title")


def _covering_markers(user, articles: QuerySet[Article]) -> list[BulkReadMarker]:
    """Bulk markers that could possibly cover any of these articles.

    Narrowed in SQL before any row is examined: a marker set before the oldest
    article, naming a feed none of them belong to, or covering a disjoint period
    can never apply, so marker work stays proportional to the window on screen
    rather than to the reader's whole history of marking things read.
    """

    bounds = articles.aggregate(earliest=Min("fetched_at"), latest=Max("fetched_at"))
    earliest, latest = bounds["earliest"], bounds["latest"]
    if earliest is None:
        return []
    return list(
        BulkReadMarker.objects.filter(user=user, marked_read_at__gte=earliest).filter(
            Q(scope=ReadScope.FEED, feed_id__in=articles.values("feed_id"))
            | Q(
                scope__in=PERIOD_SCOPES,
                period_start__lte=timezone.localtime(latest).date(),
                period_end__gte=timezone.localtime(earliest).date(),
            )
        )
    )


def _bulk_read_predicate(user, articles: QuerySet[Article]) -> Q:
    # An empty disjunction must be false, not absent: with no applicable marker,
    # nothing is bulk-read. A bare Q() would match every row instead.
    covered = Q(pk__in=[])
    for marker in _covering_markers(user, articles):
        if marker.scope == ReadScope.FEED:
            scope_matches = Q(feed_id=marker.feed_id)
        else:
            scope_matches = Q(
                fetched_at__date__gte=marker.period_start,
                fetched_at__date__lte=marker.period_end,
            )
        # A marker never reaches forward to an article fetched after it was set,
        # which is what keeps newly arrived articles unread.
        covered |= scope_matches & Q(fetched_at__lte=marker.marked_read_at)
    return covered


def _explicit_state_predicate(user, *, is_read: bool) -> Q:
    return Q(
        id__in=ArticleReadState.objects.filter(user=user, is_read=is_read).values(
            "article_id"
        )
    )


def read_predicate(user, articles: QuerySet[Article]) -> Q:
    """The read test, as SQL rather than as a set of materialized ids.

    Expressing it as a predicate is what lets a caller filter and *then* limit,
    so a bound applies to what the reader can actually see. ``articles`` is used
    to narrow candidate markers and so must not already be sliced.
    """

    # An explicit unread always wins: it is the reader overriding a bulk mark.
    return (
        _explicit_state_predicate(user, is_read=True)
        | _bulk_read_predicate(user, articles)
    ) & ~_explicit_state_predicate(user, is_read=False)


def read_article_ids(user, articles: QuerySet[Article]) -> set[int]:
    return set(
        articles.filter(read_predicate(user, articles)).values_list("id", flat=True)
    )


def saved_predicate(user) -> Q:
    return Q(id__in=SavedArticle.objects.filter(user=user).values("article_id"))


def visible_articles(user, articles: QuerySet[Article]) -> QuerySet[Article]:
    """The articles a reader should still see: neither read nor saved.

    Filtering in SQL rather than in Python is what makes a row limit safe. When
    the exclusion happened after the database had already returned a page, a
    window whose first rows were all read looked empty while unread articles sat
    just past the limit.
    """

    return articles.exclude(read_predicate(user, articles) | saved_predicate(user))


def mark_articles_read(user, articles: QuerySet[Article]) -> None:
    article_ids = list(articles.values_list("id", flat=True))
    if not article_ids:
        return
    updated_at = timezone.now()
    ArticleReadState.objects.bulk_create(
        [
            ArticleReadState(
                user_id=_pk(user),
                article_id=article_id,
                is_read=True,
                updated_at=updated_at,
            )
            for article_id in article_ids
        ],
        update_conflicts=True,
        update_fields=["is_read", "updated_at"],
        unique_fields=["user", "article"],
    )


def article_cards(user, articles: QuerySet[Article]) -> list[dict]:
    return [
        {"article": article, "is_read": False, "is_saved": False}
        for article in visible_articles(user, articles)
    ]


@dataclass(frozen=True)
class ArticlePage:
    """One bounded page of visible articles, and whether it was bounded."""

    cards: list[dict]
    has_more: bool
    limit: int


def article_card_page(
    user, articles: QuerySet[Article], *, limit: int | None = None
) -> ArticlePage:
    """Build at most ``limit`` cards, and say whether more were left behind.

    Reads one row past the limit purely to answer that question, so a truncated
    view can say so instead of being indistinguishable from an exhausted one.
    """

    limit = digest_article_limit() if limit is None else limit
    if limit < 1:
        raise ValueError("limit must be at least 1")
    rows = list(visible_articles(user, articles)[: limit + 1])
    return ArticlePage(
        cards=[
            {"article": article, "is_read": False, "is_saved": False}
            for article in rows[:limit]
        ],
        has_more=len(rows) > limit,
        limit=limit,
    )


def archived_article_cards(user) -> list[dict]:
    read_states = list(
        ArticleReadState.objects.select_related(
            "article",
            "article__feed",
            "article__newsletter_issue",
        )
        .filter(user=user, is_read=True)
        .order_by("-updated_at")[:ARCHIVED_ARTICLE_LIMIT]
    )
    article_ids = [_pk(state.article) for state in read_states]
    saved_ids = set(
        SavedArticle.objects.filter(user=user, article_id__in=article_ids).values_list(
            "article_id", flat=True
        )
    )
    return [
        {
            "article": state.article,
            "is_read": True,
            "is_saved": _pk(state.article) in saved_ids,
        }
        for state in read_states
    ]


def saved_article_cards(user) -> list[dict]:
    saved_articles = list(
        SavedArticle.objects.select_related(
            "article",
            "article__feed",
            "article__newsletter_issue",
        )
        .filter(user=user)
        .order_by("-saved_at")[:SAVED_ARTICLE_LIMIT]
    )
    articles = Article.objects.filter(
        id__in=[_pk(saved.article) for saved in saved_articles]
    )
    read_ids = read_article_ids(user, articles)
    return [
        {
            "article": saved.article,
            "saved_article": saved,
            "is_read": _pk(saved.article) in read_ids,
            "is_saved": True,
            "hide_save_action": True,
        }
        for saved in saved_articles
    ]


def user_preference(user) -> UserPreference:
    preference, _ = UserPreference.objects.get_or_create(user=user)
    return preference
