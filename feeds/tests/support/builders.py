from __future__ import annotations

import hmac
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, tzinfo
from itertools import count
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.utils import timezone

from feeds.models import (
    ApiToken,
    Article,
    ArticleReadState,
    BulkReadMarker,
    Category,
    Feed,
    NewsletterIssue,
    ReadScope,
    SavedArticle,
)

FIXED_NOW = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
FIXED_TODAY = date(2026, 1, 5)
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_IDENTITY_SEQUENCES: dict[str, Iterator[int]] = {}
_PERIOD_SCOPES = {ReadScope.DAY, ReadScope.WEEK, ReadScope.MONTH}


@dataclass(frozen=True)
class FrozenClock:
    now: datetime
    today: date


@contextmanager
def frozen_time(moment: datetime = FIXED_NOW) -> Iterator[FrozenClock]:
    """Scope Django's current time, local date, and automatic model timestamps."""

    if timezone.is_naive(moment):
        raise ValueError("frozen_time requires an aware datetime")

    original_localdate = timezone.localdate

    def localdate(
        value: datetime | None = None, timezone: tzinfo | None = None
    ) -> date:
        return original_localdate(value or moment, timezone)

    clock = FrozenClock(now=moment, today=localdate())
    with (
        patch("django.utils.timezone.now", return_value=moment),
        patch("django.utils.timezone.localdate", side_effect=localdate),
    ):
        yield clock


def _identity(kind: str) -> str:
    sequence = _IDENTITY_SEQUENCES.setdefault(kind, count(1))
    return f"{kind}-{next(sequence)}"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def fixture_text(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def fixture_json(name: str) -> Any:
    return json.loads(fixture_text(name))


def build_user(*, username: str | None = None, **kwargs: Any) -> Any:
    user_model = get_user_model()
    manager = cast(Any, user_model.objects)
    return manager.create_user(username=username or _identity("reader"), **kwargs)


def build_category(
    *, name: str | None = None, slug: str | None = None, **kwargs: Any
) -> Category:
    identity = _identity("category")
    return Category.objects.create(
        name=name or f"Category {identity}",
        slug=slug or identity,
        **kwargs,
    )


def build_feed(
    *,
    title: str | None = None,
    feed_url: str | None = None,
    category: Category | None = None,
    **kwargs: Any,
) -> Feed:
    identity = _identity("feed")
    return Feed.objects.create(
        title=title or f"Example Feed {identity}",
        feed_url=feed_url or f"https://example.com/{identity}.xml",
        category=category,
        **kwargs,
    )


def build_article(
    *,
    feed: Feed | None = None,
    title: str | None = None,
    url: str | None = None,
    guid: str | None = None,
    published_at: datetime = FIXED_NOW,
    **kwargs: Any,
) -> Article:
    identity = _identity("article")
    return Article.objects.create(
        feed=feed or build_feed(),
        title=title or f"Example article {identity}",
        url=url or f"https://example.com/{identity}",
        guid=guid or identity,
        published_at=published_at,
        **kwargs,
    )


def build_read_state(
    *, user: Any, article: Article, is_read: bool = True
) -> ArticleReadState:
    return ArticleReadState.objects.create(
        user=user,
        article=article,
        is_read=is_read,
    )


def build_bulk_marker(
    *,
    user: Any,
    scope: str,
    feed: Feed | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
) -> BulkReadMarker:
    if scope == ReadScope.FEED:
        if feed is None or period_start is not None or period_end is not None:
            raise ValueError("feed markers require a feed and no period dates")
    elif scope in _PERIOD_SCOPES:
        if feed is not None or period_start is None or period_end is None:
            raise ValueError("period markers require dates and no feed")
        if period_start > period_end:
            raise ValueError("period_start must not be after period_end")
    else:
        raise ValueError(f"unsupported read scope: {scope}")

    return BulkReadMarker.objects.create(
        user=user,
        scope=scope,
        feed=feed,
        period_start=period_start,
        period_end=period_end,
    )


def build_period_marker(
    *,
    user: Any,
    scope: str = ReadScope.DAY,
    period_start: date = FIXED_TODAY,
    period_end: date = FIXED_TODAY,
) -> BulkReadMarker:
    return build_bulk_marker(
        user=user,
        scope=scope,
        period_start=period_start,
        period_end=period_end,
    )


def build_feed_marker(*, user: Any, feed: Feed) -> BulkReadMarker:
    return build_bulk_marker(user=user, scope=ReadScope.FEED, feed=feed)


def build_saved_article(
    *,
    user: Any,
    article: Article,
    linkding_saved: bool = False,
    **kwargs: Any,
) -> SavedArticle:
    return SavedArticle.objects.create(
        user=user,
        article=article,
        url=kwargs.pop("url", article.url),
        title=kwargs.pop("title", article.title),
        feed=kwargs.pop("feed", article.feed),
        category=kwargs.pop("category", article.feed.category),
        linkding_saved=linkding_saved,
        **kwargs,
    )


def build_api_token(
    *, user: Any, name: str | None = None, capabilities: list[str] | None = None
) -> tuple[ApiToken, str]:
    return ApiToken.create_token(
        user=user, name=name or _identity("api-token"), capabilities=capabilities
    )


def signed_action_query(
    *,
    purpose: str,
    target: str,
    secret: str = "test-secret",
    lifetime_seconds: int = 60,
    nonce: str | None = None,
    expires: int | None = None,
) -> dict[str, str]:
    """Build the query a caller must present for one single-use signed action.

    Mirrors what an external agent constructs, so a test that tampers with any
    field is tampering with the real contract rather than a test-only shape.
    """

    nonce = nonce or _identity("single-use-test-nonce")
    if expires is None:
        expires = int(timezone.now().timestamp()) + lifetime_seconds
    signature = hmac.new(
        secret.encode("utf-8"),
        f"{purpose}:{target}:{expires}:{nonce}".encode(),
        "sha256",
    ).hexdigest()
    return {"expires": str(expires), "nonce": nonce, "sig": signature}


def signed_action_url(path: str, /, **extra: str) -> str:
    """Attach a signed action's credentials to its URL.

    They travel in the query string because the action itself takes no body, so
    the endpoint can keep rejecting every request body outright.
    """

    return f"{path}?{urlencode(extra)}"


def newsletter_payload(*, message_id: str | None = None) -> dict[str, Any]:
    payload = fixture_json("postmark-inbound.json")
    payload["MessageID"] = message_id or _identity("message")
    return payload


def build_newsletter_issue(
    *,
    article: Article,
    message_id: str | None = None,
    **kwargs: Any,
) -> NewsletterIssue:
    return NewsletterIssue.objects.create(
        article=article,
        message_id=message_id or _identity("message"),
        subject=kwargs.pop("subject", article.title),
        **kwargs,
    )
