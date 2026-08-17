from __future__ import annotations

import logging
import math
import re
import time
import unicodedata
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Literal, cast
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

import bleach
import feedparser
import requests
from bleach.html5lib_shim import Filter
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from .feed_fetch import FeedFetchError, fetch_feed_document
from .models import (
    Article,
    Category,
    Feed,
    LinkdingDelivery,
    NewsletterIssue,
    SavedArticle,
)

LINKDING_TOREAD_TAG = "toread"
# Postmark field bounds, matching the columns they are stored in.
MAX_MESSAGE_ID_LENGTH = 1000
MAX_SUBJECT_LENGTH = 500
MAX_EMAIL_LENGTH = 254
MAX_NAME_LENGTH = 255
MAX_AUTHOR_LENGTH = 255
NEWSLETTER_FEED_URL = "https://daily-firehose.local/feeds/email-newsletters"
NEWSLETTER_FEED_TITLE = "Email Newsletters"

logger = logging.getLogger(__name__)
_REFRESH_BACKOFF_BASE = timedelta(minutes=5)
_REFRESH_BACKOFF_CAP = timedelta(hours=24)
_DELIVERY_BACKOFF_BASE = timedelta(minutes=5)
_DELIVERY_BACKOFF_CAP = timedelta(hours=6)
# How long a claimed delivery stays reserved before another worker may retry it.
_DELIVERY_ATTEMPT_LEASE = timedelta(minutes=15)
# One worker cycle attempts at most this many bookmarks. Each attempt can burn the
# full 15-second timeout, so an unbounded backlog against a dead Linkding would
# otherwise stall the cycle that refreshes feeds.
DELIVERY_DRAIN_LIMIT = 50
_SAFE_FEED_TITLE_MAX_LENGTH = 160
OPML_MAX_BYTES = 1024 * 1024
OPML_MAX_OUTLINES = 1000
OPML_MAX_DEPTH = 32
_OPML_UNSAFE_DECLARATION = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


class _ArticleIdentityConflict(Exception):
    """Incoming GUID and URL identify two different Articles in one Feed."""


class LinkdingNotConfigured(ValueError):
    """No Linkding token is configured, so no bookmark can be delivered."""


class LinkdingBookmarkUrlMismatch(ValueError):
    """Linkding acknowledged a bookmark for a URL we did not ask it to save."""


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return " ".join(part.strip() for part in self.parts if part.strip())


@dataclass(frozen=True)
class ImportResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0


class OPMLImportError(ValueError):
    """The uploaded OPML document cannot be imported safely."""


@dataclass(frozen=True)
class _OPMLFeed:
    title: str
    feed_url: str
    site_url: str
    category_name: str


@dataclass(frozen=True)
class ArticleSaveCapability:
    allowed: bool
    code: str | None = None
    message: str | None = None


class ArticleSaveNotAllowed(Exception):
    code = "save_not_allowed"
    message = (
        "Newsletter articles cannot be saved to Linkding. Open the newsletter instead."
    )

    def __init__(self) -> None:
        super().__init__(self.message)


def article_save_capability(article: Article) -> ArticleSaveCapability:
    if not hasattr(article, "newsletter_issue"):
        return ArticleSaveCapability(allowed=True)
    return ArticleSaveCapability(
        allowed=False,
        code=ArticleSaveNotAllowed.code,
        message=ArticleSaveNotAllowed.message,
    )


def _enforce_article_save_policy(article: Article) -> None:
    # Do not trust a possibly stale reverse-one-to-one cache on a long-lived
    # Article instance. The command boundary must check current persisted state.
    if (
        article.pk is not None
        and NewsletterIssue.objects.filter(article_id=article.pk).exists()
    ):
        raise ArticleSaveNotAllowed


@dataclass(frozen=True)
class RefreshResult:
    feed: Feed
    created: int = 0
    updated: int = 0
    success: bool = True
    skipped: bool = False
    duration_seconds: float = 0.0
    error_code: str = ""
    error_message: str = ""
    next_retry_at: datetime | None = None
    superseded: bool = False

    def __post_init__(self) -> None:
        for field_name, value in (("created", self.created), ("updated", self.updated)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if (
            not isinstance(self.duration_seconds, (int, float))
            or isinstance(self.duration_seconds, bool)
            or not math.isfinite(self.duration_seconds)
            or self.duration_seconds < 0
        ):
            raise ValueError("duration_seconds must be finite and non-negative")
        if self.superseded:
            if self.success or self.skipped:
                raise ValueError("a superseded refresh cannot succeed or be skipped")
            if self.created or self.updated:
                raise ValueError("a superseded refresh cannot contain write counts")
            if not self.error_code or not self.error_message:
                raise ValueError("a superseded refresh requires safe status metadata")
        elif self.success:
            if self.skipped:
                raise ValueError("a successful refresh cannot be skipped")
            if self.error_code or self.error_message or self.next_retry_at is not None:
                raise ValueError("a successful refresh cannot contain failure metadata")
        else:
            if self.created or self.updated:
                raise ValueError(
                    "a failed or skipped refresh cannot contain write counts"
                )
            if not self.error_code or not self.error_message:
                raise ValueError(
                    "a failed or skipped refresh requires safe error metadata"
                )
            if self.next_retry_at is None:
                raise ValueError("a failed or skipped refresh requires next_retry_at")

    @property
    def status(self) -> Literal["succeeded", "failed", "skipped", "superseded"]:
        if self.superseded:
            return "superseded"
        if self.skipped:
            return "skipped"
        return "succeeded" if self.success else "failed"


class _UnusableFeedError(Exception):
    """A parsed document without usable feed or entry data."""


def safe_feed_title(title: object) -> str:
    """Return a bounded single-line title safe for logs and command output."""

    normalized = unicodedata.normalize("NFKC", str(title))
    without_controls = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in normalized
    )
    cleaned = " ".join(without_controls.split()) or "(untitled feed)"
    if len(cleaned) > _SAFE_FEED_TITLE_MAX_LENGTH:
        return f"{cleaned[: _SAFE_FEED_TITLE_MAX_LENGTH - 1]}…"
    return cleaned


def _refresh_log_context(
    result: RefreshResult, *, consecutive_failures: int
) -> tuple[str, dict[str, object]]:
    title = safe_feed_title(result.feed.title)
    next_retry = (
        result.next_retry_at.isoformat() if result.next_retry_at is not None else "none"
    )
    context: dict[str, object] = {
        "feed_id": result.feed.pk,
        "feed_title": title,
        "status": result.status,
        "duration_seconds": result.duration_seconds,
        "consecutive_failures": consecutive_failures,
        "next_retry_at": result.next_retry_at,
    }
    message = (
        f"feed_refresh_completed feed_id={result.feed.pk} title={title!r} "
        f"status={result.status} duration_seconds={result.duration_seconds:.6f} "
    )
    if result.success:
        context.update(
            articles_created=result.created,
            articles_updated=result.updated,
        )
        message += f"created={result.created} updated={result.updated} "
    else:
        context["error_code"] = result.error_code
        message += f"error_code={result.error_code} "
    message += f"consecutive_failures={consecutive_failures} next_retry_at={next_retry}"
    return message, context


@dataclass(frozen=True)
class NewsletterImportResult:
    issue: NewsletterIssue
    created: bool


def _aware_datetime(value: Any) -> datetime:
    if value is None:
        return timezone.now()
    if isinstance(value, str):
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return timezone.now()
    else:
        try:
            parsed = datetime(*value[:6])  # noqa: DTZ001
        except (TypeError, ValueError):
            return timezone.now()
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone=UTC)
    return parsed


def _refresh_failure(exc: Exception) -> tuple[str, str, bool]:
    if isinstance(exc, FeedFetchError):
        return exc.code, str(exc), False
    if isinstance(exc, _UnusableFeedError):
        return "parse_error", "The feed document could not be parsed.", False
    if isinstance(exc, ValidationError):
        return "validation_error", "The feed contained invalid article data.", False
    if isinstance(exc, (IntegrityError, _ArticleIdentityConflict)):
        return (
            "integrity_error",
            "The feed conflicted with existing article data.",
            False,
        )
    return "unexpected_error", "An unexpected feed refresh error occurred.", True


def _retry_delay(consecutive_failures: int) -> timedelta:
    multiplier = 2 ** min(max(consecutive_failures - 1, 0), 16)
    return min(_REFRESH_BACKOFF_BASE * multiplier, _REFRESH_BACKOFF_CAP)


def _claim_refresh_attempt(feed: Feed, *, attempted_at: datetime) -> int:
    """Allocate a monotonically increasing completion fence for one Feed."""

    with transaction.atomic():
        stored_feed = Feed.objects.select_for_update().get(pk=feed.pk)
        stored_feed.refresh_generation += 1
        stored_feed.last_attempt_at = attempted_at
        stored_feed.save(
            update_fields=["refresh_generation", "last_attempt_at", "updated_at"]
        )
        generation = stored_feed.refresh_generation
    feed.refresh_from_db()
    return generation


def _record_refresh_failure(
    feed: Feed,
    *,
    generation: int,
    code: str,
    message: str,
    failed_at: datetime,
) -> bool:
    """Persist failure state only when ``generation`` still owns completion."""

    with transaction.atomic():
        stored_feed = Feed.objects.select_for_update().get(pk=feed.pk)
        owns_terminal_state = stored_feed.refresh_generation == generation
        if owns_terminal_state:
            stored_feed.consecutive_failures += 1
            stored_feed.last_error_code = code
            stored_feed.last_error_message = message
            stored_feed.next_retry_at = failed_at + _retry_delay(
                stored_feed.consecutive_failures
            )
            stored_feed.save(
                update_fields=[
                    "consecutive_failures",
                    "last_error_code",
                    "last_error_message",
                    "next_retry_at",
                    "updated_at",
                ]
            )
    feed.refresh_from_db()
    return owns_terminal_state


@dataclass(frozen=True)
class _ArticleWrite:
    position: int
    article: Article | None
    guid: str
    url: str
    defaults: dict[str, object]


def _lock_feed_for_reconciliation(feed_id: int) -> Feed:
    """Acquire the serialization lock for one Feed's complete write plan."""

    return Feed.objects.select_for_update().get(pk=feed_id)


def _plan_article_reconciliation(
    *, feed: Feed, entries: list[Any]
) -> list[_ArticleWrite]:
    """Resolve the complete document against one immutable identity snapshot."""

    snapshot = list(Article.objects.filter(feed=feed).order_by("pk"))
    by_guid = {article.guid: article for article in snapshot}
    by_url = {article.url: article for article in snapshot}
    reserved_guids: set[str] = set()
    reserved_urls: set[str] = set()
    reserved_articles: set[int] = set()
    writes: list[_ArticleWrite] = []

    for position, entry in enumerate(entries):
        url = _entry_article_url(entry)
        if not url:
            continue
        guid = str(entry.get("id") or url)
        guid_match = by_guid.get(guid)
        url_match = by_url.get(url)
        if (
            guid_match is not None
            and url_match is not None
            and guid_match.pk != url_match.pk
        ):
            raise _ArticleIdentityConflict
        article = guid_match or url_match
        if guid in reserved_guids or url in reserved_urls:
            raise _ArticleIdentityConflict
        if article is not None:
            article_id = cast(int, article.pk)
            if article_id in reserved_articles:
                raise _ArticleIdentityConflict
            reserved_articles.add(article_id)
        reserved_guids.add(guid)
        reserved_urls.add(url)
        writes.append(
            _ArticleWrite(
                position=position,
                article=article,
                guid=guid,
                url=url,
                defaults={
                    "title": entry.get("title") or url,
                    "author": entry.get("author", ""),
                    "summary": entry.get("summary", ""),
                    "published_at": _aware_datetime(
                        entry.get("published_parsed")
                        or entry.get("updated_parsed")
                        or entry.get("published")
                        or entry.get("updated")
                    ),
                },
            )
        )

    return sorted(
        writes,
        key=lambda write: (
            write.article is None,
            write.article.pk if write.article is not None else write.guid,
            write.url,
            write.position,
        ),
    )


def _apply_article_reconciliation(
    *, feed: Feed, writes: list[_ArticleWrite]
) -> tuple[int, int]:
    created = 0
    updated = 0
    for write in writes:
        if write.article is None:
            Article.objects.create(
                feed=feed,
                guid=write.guid,
                url=write.url,
                **write.defaults,
            )
            created += 1
            continue
        article = write.article
        article.guid = write.guid
        article.url = write.url
        for field, value in write.defaults.items():
            setattr(article, field, value)
        article.save(update_fields=["guid", "url", *write.defaults, "updated_at"])
        updated += 1
    return created, updated


def _superseded_result(*, feed: Feed, started: float) -> RefreshResult:
    return RefreshResult(
        feed=feed,
        success=False,
        superseded=True,
        duration_seconds=max(0.0, time.monotonic() - started),
        error_code="superseded",
        error_message="A newer refresh attempt owns the persisted feed status.",
        next_retry_at=feed.next_retry_at,
    )


def refresh_feed(feed: Feed) -> RefreshResult:
    started = time.monotonic()
    attempted_at = timezone.now()
    generation = _claim_refresh_attempt(feed, attempted_at=attempted_at)

    try:
        document = fetch_feed_document(feed.feed_url)
        try:
            parsed = cast(
                Any,
                feedparser.parse(
                    document.content,
                    response_headers=document.response_headers,
                ),
            )
        except Exception as exc:
            raise _UnusableFeedError from exc
        feed_info = parsed.get("feed", {})
        entries = parsed.get("entries", [])
        if parsed.get("bozo") and not feed_info and not entries:
            raise _UnusableFeedError

        with transaction.atomic():
            # The per-Feed row lock makes the complete identity snapshot and
            # write plan immutable relative to every other refresh for this Feed.
            stored_feed = _lock_feed_for_reconciliation(cast(int, feed.pk))
            if stored_feed.refresh_generation != generation:
                feed.refresh_from_db()
                result = _superseded_result(feed=feed, started=started)
                log_message, log_context = _refresh_log_context(
                    result, consecutive_failures=feed.consecutive_failures
                )
                logger.info(log_message, extra=log_context)
                return result

            writes = _plan_article_reconciliation(
                feed=stored_feed, entries=list(entries)
            )
            created, updated = _apply_article_reconciliation(
                feed=stored_feed, writes=writes
            )
            stored_feed.title = (
                feed_info.get("title") or stored_feed.title or stored_feed.feed_url
            )
            stored_feed.site_url = feed_info.get("link") or stored_feed.site_url
            stored_feed.description = (
                feed_info.get("subtitle")
                or feed_info.get("description")
                or stored_feed.description
            )
            succeeded_at = timezone.now()
            stored_feed.last_fetched_at = succeeded_at
            stored_feed.last_error_code = ""
            stored_feed.last_error_message = ""
            stored_feed.consecutive_failures = 0
            stored_feed.next_retry_at = None
            stored_feed.save(
                update_fields=[
                    "title",
                    "site_url",
                    "description",
                    "last_fetched_at",
                    "last_error_code",
                    "last_error_message",
                    "consecutive_failures",
                    "next_retry_at",
                    "updated_at",
                ]
            )
            feed.refresh_from_db()
    except Exception as exc:
        code, message, unexpected = _refresh_failure(exc)
        failed_at = timezone.now()
        owns_terminal_state = _record_refresh_failure(
            feed,
            generation=generation,
            code=code,
            message=message,
            failed_at=failed_at,
        )
        if owns_terminal_state:
            result = RefreshResult(
                feed=feed,
                success=False,
                duration_seconds=max(0.0, time.monotonic() - started),
                error_code=code,
                error_message=message,
                next_retry_at=feed.next_retry_at,
            )
        else:
            result = _superseded_result(feed=feed, started=started)
        log_message, log_context = _refresh_log_context(
            result, consecutive_failures=feed.consecutive_failures
        )
        if result.superseded:
            logger.info(log_message, extra=log_context)
        elif unexpected:
            logger.exception(log_message, extra=log_context)
        else:
            logger.warning(log_message, extra=log_context)
        return result

    result = RefreshResult(
        feed=feed,
        created=created,
        updated=updated,
        duration_seconds=max(0.0, time.monotonic() - started),
    )
    log_message, log_context = _refresh_log_context(
        result, consecutive_failures=feed.consecutive_failures
    )
    logger.info(log_message, extra=log_context)
    return result


def _entry_article_url(entry: Any) -> str:
    """Return the canonical article URL from a parsed feed entry.

    Some feed generators expose their local/permalink URL as ``entry.link`` while
    putting the original article URL in an ``alternate`` link. Prefer the
    explicit HTML alternate link so downstream Linkding saves bookmark the
    original article rather than a Daily Firehose/intermediary URL.
    """

    entry_id = str(entry.get("id") or "")
    entry_link = str(entry.get("link") or "")
    for link in entry.get("links", []):
        href = str(link.get("href") or "")
        if href and link.get("rel") == "alternate" and link.get("type") == "text/html":
            return href
    return entry_link or entry_id


def iter_refresh_active_feeds() -> Iterator[RefreshResult]:
    """Yield each active Feed's result as soon as that Feed is finished.

    A long-running worker consumes this so it can heartbeat and observe a stop
    request between Feeds instead of only at the end of a whole cycle.
    """

    for feed in Feed.objects.filter(is_active=True):
        eligibility_time = timezone.now()
        if feed.next_retry_at is not None and feed.next_retry_at > eligibility_time:
            yield RefreshResult(
                feed=feed,
                success=False,
                skipped=True,
                error_code=feed.last_error_code,
                error_message=feed.last_error_message,
                next_retry_at=feed.next_retry_at,
            )
            continue
        yield refresh_feed(feed)


def refresh_active_feeds() -> list[RefreshResult]:
    return list(iter_refresh_active_feeds())


def newsletter_feed() -> Feed:
    feed, _ = Feed.objects.get_or_create(
        feed_url=NEWSLETTER_FEED_URL,
        defaults={
            "title": NEWSLETTER_FEED_TITLE,
            "site_url": "",
            "description": "Email newsletters received through Postmark inbound email.",
            "is_active": False,
        },
    )
    return feed


def newsletter_archive_url(*, base_url: str, public_id: Any) -> str:
    return urljoin(
        base_url.rstrip("/") + "/",
        reverse("newsletter-detail", args=[public_id]).lstrip("/"),
    )


def _postmark_address(payload: dict[str, Any], field: str) -> str:
    full_value = payload.get(f"{field}Full")
    if isinstance(full_value, dict):
        email = full_value.get("Email")
        if email:
            return str(email)
    if isinstance(full_value, list) and full_value:
        first = full_value[0]
        if isinstance(first, dict) and first.get("Email"):
            return str(first["Email"])
    value = payload.get(field)
    return str(value or "")


def _postmark_name(payload: dict[str, Any], field: str) -> str:
    full_value = payload.get(f"{field}Full")
    if isinstance(full_value, dict):
        return str(full_value.get("Name") or "")
    return ""


def _bounded(value: object, limit: int) -> str:
    """Return text truncated to a column's limit.

    Display fields are truncated rather than rejected: losing a newsletter is a
    worse outcome than losing the tail of an overlong subject.
    """

    return str(value or "")[:limit]


def import_postmark_newsletter(
    *, payload: dict[str, Any], base_url: str
) -> NewsletterImportResult:
    message_id = str(payload.get("MessageID") or payload.get("MessageId") or "")
    if not message_id:
        raise ValueError("Postmark payload is missing MessageID.")
    # Identity is never truncated: a shortened MessageID would collide with a
    # different message and corrupt replay detection.
    if len(message_id) > MAX_MESSAGE_ID_LENGTH:
        raise ValueError("Postmark MessageID exceeds the supported length.")

    existing = (
        NewsletterIssue.objects.select_related("article")
        .filter(message_id=message_id)
        .first()
    )
    if existing is not None:
        return NewsletterImportResult(issue=existing, created=False)

    try:
        with transaction.atomic():
            subject = _bounded(
                payload.get("Subject") or "Untitled newsletter", MAX_SUBJECT_LENGTH
            )
            html_body = str(payload.get("HtmlBody") or "")
            text_body = str(payload.get("TextBody") or "")
            received_at = _aware_datetime(payload.get("Date"))
            public_id = uuid.uuid4()
            archive_url = newsletter_archive_url(base_url=base_url, public_id=public_id)
            feed = newsletter_feed()
            article = Article.objects.create(
                feed=feed,
                title=subject,
                url=archive_url,
                guid=message_id,
                author=_bounded(_postmark_address(payload, "From"), MAX_AUTHOR_LENGTH),
                summary=text_body or html_body,
                published_at=received_at,
            )
            issue = NewsletterIssue.objects.create(
                article=article,
                public_id=public_id,
                message_id=message_id,
                from_email=_bounded(
                    _postmark_address(payload, "From"), MAX_EMAIL_LENGTH
                ),
                from_name=_bounded(_postmark_name(payload, "From"), MAX_NAME_LENGTH),
                to_email=_bounded(_postmark_address(payload, "To"), MAX_EMAIL_LENGTH),
                subject=subject,
                html_body=html_body,
                text_body=text_body,
                sanitized_html=sanitize_newsletter_html(html_body)
                if html_body
                else "",
                received_at=received_at,
            )
    except IntegrityError as integrity_error:
        # The failed transaction must be exited before reading the concurrent
        # winner. If there is no committed issue for this MessageID, the error
        # was unrelated to replay and must retain its normal API/error contract.
        try:
            winner = NewsletterIssue.objects.select_related("article").get(
                message_id=message_id
            )
        except NewsletterIssue.DoesNotExist:
            raise integrity_error
        return NewsletterImportResult(issue=winner, created=False)

    return NewsletterImportResult(issue=issue, created=True)


def sanitize_newsletter_html(html: str) -> str:
    html = re.sub(
        r"<\s*(script|style)\b[^>]*>.*?<\s*/\s*\1\s*>",
        "",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    tags = set(bleach.sanitizer.ALLOWED_TAGS) | {
        "article",
        "aside",
        "br",
        "caption",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "img",
        "p",
        "section",
        "span",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
    }
    attributes = {
        **bleach.sanitizer.ALLOWED_ATTRIBUTES,
        "a": ["href", "title", "target", "rel"],
        "img": [
            "src",
            "alt",
            "title",
            "width",
            "height",
            "referrerpolicy",
            "loading",
            "decoding",
        ],
        "td": ["colspan", "rowspan"],
        "th": ["colspan", "rowspan", "scope"],
    }
    cleaner = bleach.sanitizer.Cleaner(
        tags=tags,
        attributes=attributes,
        protocols={"http", "https", "mailto"},
        strip=True,
        filters=[_NewsletterImageFilter],
    )
    cleaned = cleaner.clean(html)
    return bleach.linkify(
        cleaned,
        callbacks=[bleach.callbacks.nofollow, _newsletter_link_attrs],
        skip_tags={"pre", "code"},
    )


def _newsletter_link_attrs(attrs, new=False):
    attrs[(None, "target")] = "_blank"
    attrs[(None, "rel")] = "noopener noreferrer"
    return attrs


class _NewsletterImageFilter(Filter):
    """Bound what a remote newsletter image can learn about the reader.

    Newsletter images are deliberately still loaded, so a tracking pixel does
    reveal that the issue was opened. `no-referrer` keeps the archive URL itself
    out of the sender's logs, which is what makes an unlisted public link
    guessable-by-disclosure rather than merely unlisted.
    """

    def __iter__(self):
        for token in super().__iter__():
            if token.get("type") in {"StartTag", "EmptyTag"} and token.get(
                "name"
            ) == "img":
                data = token.setdefault("data", {})
                data[(None, "referrerpolicy")] = "no-referrer"
                data[(None, "loading")] = "lazy"
                data[(None, "decoding")] = "async"
            yield token


def discover_feed_metadata(feed_url: str) -> dict[str, str]:
    document = fetch_feed_document(feed_url)
    parsed = cast(
        Any,
        feedparser.parse(
            document.content,
            response_headers=document.response_headers,
        ),
    )
    info = parsed.get("feed", {})
    return {
        "title": info.get("title") or feed_url,
        "site_url": info.get("link") or "",
        "description": info.get("subtitle") or info.get("description") or "",
    }


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _opml_attribute(
    element: ElementTree.Element, name: str, *, strip: bool = False
) -> str:
    name = name.lower()
    value = next(
        (value for key, value in element.attrib.items() if key.lower() == name), ""
    )
    return value.strip() if strip else value


def _validated_opml_feed(outline: ElementTree.Element, category_name: str) -> _OPMLFeed:
    feed_url = _opml_attribute(outline, "xmlurl", strip=True)
    title = (
        _opml_attribute(outline, "title")
        or _opml_attribute(outline, "text")
        or feed_url
    )
    site_url = _opml_attribute(outline, "htmlurl", strip=True)
    if urlsplit(feed_url).scheme.lower() not in {"http", "https"}:
        raise OPMLImportError("Feed URLs must use HTTP or HTTPS.")
    if site_url and urlsplit(site_url).scheme.lower() not in {"http", "https"}:
        raise OPMLImportError("Site URLs must use HTTP or HTTPS.")
    candidate = Feed(
        title=title,
        feed_url=feed_url,
        site_url=site_url,
        category=None,
        is_active=True,
    )
    try:
        candidate.full_clean(validate_unique=False, validate_constraints=False)
    except ValidationError as exc:
        raise OPMLImportError("An outline contains invalid feed fields.") from exc
    return _OPMLFeed(
        title=title,
        feed_url=feed_url,
        site_url=site_url,
        category_name=category_name,
    )


def _parse_opml(content: bytes) -> tuple[list[_OPMLFeed], int]:
    if not content or len(content) > OPML_MAX_BYTES:
        raise OPMLImportError("The OPML file is empty or exceeds the upload limit.")
    try:
        source = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OPMLImportError("The OPML file must use UTF-8 encoding.") from exc
    if _OPML_UNSAFE_DECLARATION.search(source):
        raise OPMLImportError("DTD and entity declarations are not allowed.")
    try:
        root = ElementTree.fromstring(source)
    except ElementTree.ParseError as exc:
        raise OPMLImportError("The OPML XML is malformed.") from exc
    if _xml_local_name(root.tag) != "opml":
        raise OPMLImportError("The document root must be an OPML element.")
    bodies = [child for child in root if _xml_local_name(child.tag) == "body"]
    if len(bodies) != 1:
        raise OPMLImportError("The OPML document must contain exactly one body.")

    feeds: list[_OPMLFeed] = []
    duplicate_count = 0
    by_url: dict[str, _OPMLFeed] = {}
    outline_count = 0

    def visit(parent: ElementTree.Element, category_name: str, depth: int) -> None:
        nonlocal duplicate_count, outline_count
        if depth > OPML_MAX_DEPTH:
            raise OPMLImportError("The OPML outline nesting is too deep.")
        for child in parent:
            if _xml_local_name(child.tag) != "outline":
                raise OPMLImportError("The OPML body may contain only outlines.")
            outline_count += 1
            if outline_count > OPML_MAX_OUTLINES:
                raise OPMLImportError("The OPML document contains too many outlines.")
            feed_url = _opml_attribute(child, "xmlurl", strip=True)
            if feed_url:
                if len(child):
                    raise OPMLImportError(
                        "Feed outlines cannot contain child outlines."
                    )
                parsed = _validated_opml_feed(child, category_name)
                existing = by_url.get(parsed.feed_url)
                if existing is not None:
                    if existing != parsed:
                        raise OPMLImportError(
                            "Duplicate feed URLs must have identical fields."
                        )
                    duplicate_count += 1
                    continue
                by_url[parsed.feed_url] = parsed
                feeds.append(parsed)
                continue
            child_category = _opml_attribute(child, "title") or _opml_attribute(
                child, "text"
            )
            if not child_category.strip() or not len(child):
                raise OPMLImportError(
                    "Category outlines need a name and at least one child outline."
                )
            category = Category(
                name=child_category, slug=slugify(child_category) or "category"
            )
            try:
                category.full_clean(validate_unique=False, validate_constraints=False)
            except ValidationError as exc:
                raise OPMLImportError(
                    "An outline contains invalid category fields."
                ) from exc
            visit(child, child_category, depth + 1)

    visit(bodies[0], "", 1)
    return feeds, duplicate_count


def _lock_category_names(names: list[str]) -> None:
    if connection.vendor != "postgresql" or not names:
        return
    # Category rows may not exist yet, so row locks cannot serialize creation. Resolve
    # every distinct name to its advisory-lock key first, then acquire those keys in
    # numeric order. Importers with opposite outline order therefore cannot deadlock.
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT hashtextextended(category_name, 0) AS lock_key
            FROM unnest(%s::text[]) AS categories(category_name)
            ORDER BY lock_key
            """,
            [names],
        )
        lock_keys = [row[0] for row in cursor.fetchall()]
        for lock_key in lock_keys:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_key])


@transaction.atomic
def _category_from_name(
    name: str, *, acquire_advisory_lock: bool = True
) -> Category | None:
    if not name:
        return None
    if acquire_advisory_lock:
        _lock_category_names([name])
    existing = Category.objects.filter(name=name).first()
    if existing is not None:
        return existing
    base_slug = (slugify(name) or "category")[:140]
    suffix = 1
    while True:
        suffix_text = "" if suffix == 1 else f"-{suffix}"
        slug = f"{base_slug[: 140 - len(suffix_text)]}{suffix_text}"
        same_slug = Category.objects.filter(slug=slug).first()
        if same_slug is not None:
            if same_slug.name == name:
                return same_slug
            suffix += 1
            continue
        candidate = Category(name=name, slug=slug)
        candidate.full_clean(validate_unique=False, validate_constraints=False)
        try:
            with transaction.atomic():
                return Category.objects.create(name=name, slug=slug)
        except IntegrityError:
            try:
                return Category.objects.get(name=name)
            except Category.DoesNotExist:
                suffix += 1


def import_opml(content: bytes) -> ImportResult:
    planned_feeds, skipped = _parse_opml(content)
    created = updated = 0
    category_names = sorted(
        {planned.category_name for planned in planned_feeds if planned.category_name}
    )
    ordered_feeds = sorted(
        planned_feeds,
        key=lambda planned: (
            planned.category_name,
            planned.feed_url,
            planned.title,
            planned.site_url,
        ),
    )
    with transaction.atomic():
        _lock_category_names(category_names)
        categories = {
            name: _category_from_name(name, acquire_advisory_lock=False)
            for name in category_names
        }
        for planned in ordered_feeds:
            category = categories.get(planned.category_name)
            _, was_created = Feed.objects.update_or_create(
                feed_url=planned.feed_url,
                defaults={
                    "title": planned.title,
                    "site_url": planned.site_url,
                    "category": category,
                    "is_active": True,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1
    return ImportResult(created=created, updated=updated, skipped=skipped)


def export_opml() -> str:
    root = ElementTree.Element("opml", {"version": "2.0"})
    head = ElementTree.SubElement(root, "head")
    ElementTree.SubElement(head, "title").text = "Daily Firehose feeds"
    body = ElementTree.SubElement(root, "body")
    category_outlines: dict[int, ElementTree.Element] = {}
    feeds = (
        Feed.objects.filter(is_active=True)
        .select_related("category")
        .order_by("category__name", "category__slug", "title", "feed_url")
    )
    for feed in feeds:
        parent: ElementTree.Element = body
        if feed.category is not None:
            category_id = feed.category.pk
            category_parent = category_outlines.get(category_id)
            if category_parent is None:
                category_parent = ElementTree.SubElement(
                    body,
                    "outline",
                    {"text": feed.category.name, "title": feed.category.name},
                )
                category_outlines[category_id] = category_parent
            parent = category_parent
        attrs = {
            "text": feed.title,
            "title": feed.title,
            "type": "rss",
            "xmlUrl": feed.feed_url,
        }
        if feed.site_url:
            attrs["htmlUrl"] = feed.site_url
        ElementTree.SubElement(parent, "outline", attrs)
    return ElementTree.tostring(root, encoding="unicode", xml_declaration=True)


def save_article(
    *, user: Any, article: Article, base_url: str, token: str
) -> SavedArticle:
    """Record the local save intent, then attempt remote delivery once.

    The local row and its owed delivery commit together, so a crash between them
    cannot leave a save with nothing tracking its bookmark. The remote attempt
    happens afterwards and outside that transaction: it is slow, it can fail, and
    the whole point of the delivery row is that failing it is survivable.
    """

    _enforce_article_save_policy(article)

    with transaction.atomic():
        saved, _ = SavedArticle.objects.update_or_create(
            user=user,
            article=article,
            defaults={
                "url": article.url,
                "title": article.title,
                "feed": article.feed,
                "category": article.feed.category,
            },
        )
        # A re-save is a fresh intent, so it restarts the retry budget. That is
        # also the manual recovery path for a permanently failed delivery.
        delivery, _ = LinkdingDelivery.objects.update_or_create(
            saved_article=saved,
            defaults={
                "url": article.url,
                "state": LinkdingDelivery.State.QUEUED,
                "attempts": 0,
                "next_attempt_at": timezone.now(),
                "last_attempt_at": None,
                "delivered_at": None,
                "bookmark_id": "",
                "error_class": "",
                "error_message": "",
            },
        )

    delivery.saved_article = saved
    attempt_linkding_delivery(delivery, base_url=base_url, token=token, article=article)
    return saved


def _linkding_description(article: Article) -> str:
    """Return a Linkding description without feed-only comments links."""

    parser = _TextExtractor()
    parser.feed(article.summary or "")
    description = parser.text()
    if description.lower() == "comments":
        return ""
    return description


def canonical_bookmark_url(url: str) -> str:
    """Normalize a URL for identity comparison only.

    Never used for what gets sent to Linkding: the bookmark keeps the article's
    exact URL. This only decides whether two URLs name the same bookmark, so a
    case-different host or a bare-vs-slash root does not read as a mismatch.
    """

    parts = urlsplit(url.strip())
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, "")
    )


def save_to_linkding(*, base_url: str, token: str, article: Article) -> dict[str, Any]:
    if not token:
        raise LinkdingNotConfigured("LINKDING_TOKEN is not configured")
    payload = {
        "url": article.url,
        "title": article.title,
        "description": _linkding_description(article),
        "tag_names": [LINKDING_TOREAD_TAG],
    }
    response = requests.post(
        f"{base_url.rstrip('/')}/api/bookmarks/",
        headers={"Authorization": f"Token {token}"},
        json=payload,
        timeout=15,
    )
    response.raise_for_status()
    bookmark = cast(dict[str, Any], response.json())
    returned_url = str(bookmark.get("url") or "")
    if canonical_bookmark_url(returned_url) != canonical_bookmark_url(article.url):
        raise LinkdingBookmarkUrlMismatch(
            "Linkding returned a different bookmark URL: "
            f"expected {article.url!r}, received {returned_url!r}"
        )
    return bookmark


def find_linkding_bookmark(
    *, base_url: str, token: str, url: str
) -> dict[str, Any] | None:
    """Return the existing bookmark for ``url``, or ``None`` if there is none.

    Deliberately total: any transport error, rejection, or response shape this
    does not recognize returns ``None`` rather than raising. A negative answer
    only costs a create attempt, which is what the caller would have done anyway,
    so reconciliation can never make delivery worse than not reconciling.
    """

    if not token:
        return None
    try:
        response = requests.get(
            f"{base_url.rstrip('/')}/api/bookmarks/check/",
            headers={"Authorization": f"Token {token}"},
            params={"url": url},
            timeout=15,
        )
        if response.status_code >= 400:
            return None
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    bookmark = payload.get("bookmark")
    if not isinstance(bookmark, dict):
        return None
    if canonical_bookmark_url(str(bookmark.get("url") or "")) != canonical_bookmark_url(
        url
    ):
        return None
    return cast(dict[str, Any], bookmark)


def _classify_linkding_error(exc: Exception) -> tuple[str, bool]:
    """Map a delivery exception to its error class and whether retrying can help."""

    classes = LinkdingDelivery.ErrorClass
    if isinstance(exc, LinkdingNotConfigured):
        # Retryable because an operator supplying the token later is exactly the
        # case that should recover without the user re-saving.
        return classes.NOT_CONFIGURED, True
    if isinstance(exc, LinkdingBookmarkUrlMismatch):
        return classes.URL_MISMATCH, False
    if isinstance(exc, requests.Timeout):
        return classes.TIMEOUT, True
    if isinstance(exc, requests.ConnectionError):
        return classes.CONNECTION, True
    if isinstance(exc, requests.HTTPError):
        status = getattr(exc.response, "status_code", None)
        if status == 429:
            return classes.RATE_LIMITED, True
        if status in (401, 403):
            return classes.AUTH, True
        if status is not None and 500 <= status < 600:
            return classes.SERVER_ERROR, True
        return classes.CLIENT_ERROR, False
    if isinstance(exc, ValueError):
        # Includes JSON decode failures, which an intercepting proxy can cause.
        return classes.INVALID_RESPONSE, True
    if isinstance(exc, requests.RequestException):
        return classes.CONNECTION, True
    return classes.UNEXPECTED, True


def _delivery_retry_delay(attempts: int) -> timedelta:
    multiplier = 2 ** min(max(attempts - 1, 0), 16)
    return min(_DELIVERY_BACKOFF_BASE * multiplier, _DELIVERY_BACKOFF_CAP)


def _max_delivery_attempts() -> int:
    return int(settings.LINKDING_MAX_DELIVERY_ATTEMPTS)


def _deliver_bookmark(
    *, base_url: str, token: str, article: Article, delivery: LinkdingDelivery
) -> dict[str, Any]:
    """Reconcile before creating, but only when a previous attempt could have created.

    A first attempt cannot have left a bookmark behind, so it posts directly and
    costs no extra request. A retry might be following an attempt that created
    the bookmark and then failed to hear back — the timeout-after-create case —
    so it looks the URL up first and adopts what is already there instead of
    creating a duplicate.
    """

    if delivery.attempts > 1:
        existing = find_linkding_bookmark(
            base_url=base_url, token=token, url=delivery.url
        )
        if existing is not None:
            return existing
    return save_to_linkding(base_url=base_url, token=token, article=article)


def attempt_linkding_delivery(
    delivery: LinkdingDelivery,
    *,
    base_url: str,
    token: str,
    article: Article | None = None,
) -> LinkdingDelivery:
    """Make one delivery attempt and record its outcome on the delivery row."""

    now = timezone.now()
    if article is None:
        article = delivery.saved_article.article
    delivery.attempts += 1
    delivery.last_attempt_at = now
    try:
        bookmark = _deliver_bookmark(
            base_url=base_url, token=token, article=article, delivery=delivery
        )
    except Exception as exc:  # noqa: BLE001 - every failure is classified and recorded.
        error_class, retryable = _classify_linkding_error(exc)
        delivery.error_class = error_class
        delivery.error_message = str(exc)
        delivery.delivered_at = None
        if retryable and delivery.attempts < _max_delivery_attempts():
            delivery.state = LinkdingDelivery.State.TRANSIENT_FAILED
            delivery.next_attempt_at = now + _delivery_retry_delay(delivery.attempts)
        else:
            delivery.state = LinkdingDelivery.State.PERMANENT_FAILED
            delivery.next_attempt_at = None
    else:
        delivery.state = LinkdingDelivery.State.SUCCEEDED
        delivery.delivered_at = now
        delivery.next_attempt_at = None
        delivery.bookmark_id = str(bookmark.get("id") or "")[:64]
        delivery.error_class = ""
        delivery.error_message = ""
    delivery.save(
        update_fields=[
            "state",
            "attempts",
            "last_attempt_at",
            "next_attempt_at",
            "delivered_at",
            "bookmark_id",
            "error_class",
            "error_message",
            "updated_at",
        ]
    )
    _project_delivery(delivery)
    logger.info(
        "linkding_delivery_attempted",
        extra={
            "saved_article_id": delivery.saved_article_id,
            "state": delivery.state,
            "attempts": delivery.attempts,
            "error_class": delivery.error_class,
        },
    )
    return delivery


def _project_delivery(delivery: LinkdingDelivery) -> None:
    """Mirror delivery state onto the SavedArticle fields every adapter reads."""

    saved = delivery.saved_article
    saved.linkding_saved = delivery.state == LinkdingDelivery.State.SUCCEEDED
    saved.linkding_error = delivery.error_message
    SavedArticle.objects.filter(pk=saved.pk).update(
        linkding_saved=saved.linkding_saved,
        linkding_error=saved.linkding_error,
        updated_at=timezone.now(),
    )


def _claim_delivery(delivery: LinkdingDelivery, *, now: datetime) -> bool:
    """Reserve a due delivery by compare-and-set on the schedule it was read at.

    Pushing ``next_attempt_at`` out to a lease claims the row without a state
    that a dying process could strand, and without holding a row lock across a
    slow HTTP call. A racing worker's update matches zero rows and it moves on.
    """

    claimed = LinkdingDelivery.objects.filter(
        pk=delivery.pk,
        state__in=LinkdingDelivery.RETRYABLE_STATES,
        next_attempt_at=delivery.next_attempt_at,
    ).update(next_attempt_at=now + _DELIVERY_ATTEMPT_LEASE, updated_at=now)
    return claimed == 1


@dataclass(frozen=True)
class DeliveryTally:
    attempted: int = 0
    succeeded: int = 0
    transient: int = 0
    permanent: int = 0


def deliver_pending_saved_articles(
    *, base_url: str, token: str, limit: int | None = None
) -> DeliveryTally:
    """Attempt every delivery that is owed and due, newest schedule first."""

    now = timezone.now()
    attempted = succeeded = transient = permanent = 0
    due = (
        LinkdingDelivery.objects.filter(
            state__in=LinkdingDelivery.RETRYABLE_STATES, next_attempt_at__lte=now
        )
        .select_related("saved_article", "saved_article__article__feed")
        .order_by("next_attempt_at", "id")
    )
    if limit is not None:
        due = due[:limit]
    for delivery in list(due):
        if not _claim_delivery(delivery, now=timezone.now()):
            continue
        attempt_linkding_delivery(delivery, base_url=base_url, token=token)
        attempted += 1
        if delivery.state == LinkdingDelivery.State.SUCCEEDED:
            succeeded += 1
        elif delivery.state == LinkdingDelivery.State.TRANSIENT_FAILED:
            transient += 1
        else:
            permanent += 1
    return DeliveryTally(
        attempted=attempted,
        succeeded=succeeded,
        transient=transient,
        permanent=permanent,
    )
