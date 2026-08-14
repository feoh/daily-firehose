"""Article visibility and read-state resolution.

The single source of truth for which articles a reader should see and which
count as read. Both the HTML views and the JSON API consume this module, so the
two surfaces cannot answer the same question differently.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, cast

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


def _covering_markers(user, article_ids: list[int]) -> list[BulkReadMarker]:
    """Bulk markers that could possibly cover any of these articles.

    Narrowed in SQL before any row is examined: a marker set before the oldest
    article, naming a feed none of them belong to, or covering a disjoint period
    can never apply, so marker work stays proportional to the window on screen
    rather than to the reader's whole history of marking things read.
    """

    articles = Article.objects.filter(id__in=article_ids)
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


def _bulk_read_article_ids(user, article_ids: list[int]) -> set[int]:
    covered = Q()
    applicable = False
    for marker in _covering_markers(user, article_ids):
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
        applicable = True
    if not applicable:
        return set()
    return set(
        Article.objects.filter(id__in=article_ids)
        .filter(covered)
        .values_list("id", flat=True)
    )


def read_article_ids(user, articles: QuerySet[Article]) -> set[int]:
    article_ids = list(articles.values_list("id", flat=True))
    if not article_ids:
        return set()
    explicit_read: set[int] = set()
    explicit_unread: set[int] = set()
    for article_id, is_read in ArticleReadState.objects.filter(
        user=user, article_id__in=article_ids
    ).values_list("article_id", "is_read"):
        (explicit_read if is_read else explicit_unread).add(article_id)
    # An explicit unread always wins: it is the reader overriding a bulk mark.
    return (explicit_read | _bulk_read_article_ids(user, article_ids)) - explicit_unread


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
    read_ids = read_article_ids(user, articles)
    saved_ids = set(
        SavedArticle.objects.filter(
            user=user, article_id__in=articles.values_list("id", flat=True)
        ).values_list("article_id", flat=True)
    )
    return [
        {"article": article, "is_read": False, "is_saved": False}
        for article in articles
        if _pk(article) not in read_ids and _pk(article) not in saved_ids
    ]


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
