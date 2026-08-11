from __future__ import annotations

import logging
import math
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Literal, cast
from urllib.parse import urljoin
from xml.etree import ElementTree

import bleach
import feedparser
import requests
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from .feed_fetch import FeedFetchError, fetch_feed_document
from .models import Article, Category, Feed, NewsletterIssue, SavedArticle

LINKDING_TOREAD_TAG = "toread"
NEWSLETTER_FEED_URL = "https://daily-firehose.local/feeds/email-newsletters"
NEWSLETTER_FEED_TITLE = "Email Newsletters"

logger = logging.getLogger(__name__)
_REFRESH_BACKOFF_BASE = timedelta(minutes=5)
_REFRESH_BACKOFF_CAP = timedelta(hours=24)
_SAFE_FEED_TITLE_MAX_LENGTH = 160


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
        if self.success:
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
    def status(self) -> Literal["succeeded", "failed", "skipped"]:
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
    if isinstance(exc, IntegrityError):
        return (
            "integrity_error",
            "The feed conflicted with existing article data.",
            False,
        )
    return "unexpected_error", "An unexpected feed refresh error occurred.", True


def _retry_delay(consecutive_failures: int) -> timedelta:
    multiplier = 2 ** min(max(consecutive_failures - 1, 0), 16)
    return min(_REFRESH_BACKOFF_BASE * multiplier, _REFRESH_BACKOFF_CAP)


def _record_refresh_failure(
    feed: Feed, *, code: str, message: str, failed_at: datetime
) -> datetime:
    with transaction.atomic():
        stored_feed = Feed.objects.select_for_update().get(pk=feed.pk)
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
    return cast(datetime, feed.next_retry_at)


def refresh_feed(feed: Feed) -> RefreshResult:
    started = time.monotonic()
    attempted_at = timezone.now()
    Feed.objects.filter(pk=feed.pk).update(last_attempt_at=attempted_at)
    feed.last_attempt_at = attempted_at

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
            feed.title = feed_info.get("title") or feed.title or feed.feed_url
            feed.site_url = feed_info.get("link") or feed.site_url
            feed.description = (
                feed_info.get("subtitle")
                or feed_info.get("description")
                or feed.description
            )

            created = 0
            updated = 0
            for entry in entries:
                url = _entry_article_url(entry)
                if not url:
                    continue
                guid = entry.get("id") or url
                defaults = {
                    "title": entry.get("title") or url,
                    "url": url,
                    "author": entry.get("author", ""),
                    "summary": entry.get("summary", ""),
                    "published_at": _aware_datetime(
                        entry.get("published_parsed")
                        or entry.get("updated_parsed")
                        or entry.get("published")
                        or entry.get("updated")
                    ),
                }
                _, was_created = Article.objects.update_or_create(
                    feed=feed,
                    guid=guid,
                    defaults=defaults,
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

            succeeded_at = timezone.now()
            feed.last_fetched_at = succeeded_at
            feed.last_error_code = ""
            feed.last_error_message = ""
            feed.consecutive_failures = 0
            feed.next_retry_at = None
            feed.save(
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
    except Exception as exc:
        code, message, unexpected = _refresh_failure(exc)
        failed_at = timezone.now()
        next_retry_at = _record_refresh_failure(
            feed, code=code, message=message, failed_at=failed_at
        )
        result = RefreshResult(
            feed=feed,
            success=False,
            duration_seconds=max(0.0, time.monotonic() - started),
            error_code=code,
            error_message=message,
            next_retry_at=next_retry_at,
        )
        log_message, log_context = _refresh_log_context(
            result, consecutive_failures=feed.consecutive_failures
        )
        if unexpected:
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


def refresh_active_feeds() -> list[RefreshResult]:
    results: list[RefreshResult] = []
    for feed in Feed.objects.filter(is_active=True):
        eligibility_time = timezone.now()
        if feed.next_retry_at is not None and feed.next_retry_at > eligibility_time:
            results.append(
                RefreshResult(
                    feed=feed,
                    success=False,
                    skipped=True,
                    error_code=feed.last_error_code,
                    error_message=feed.last_error_message,
                    next_retry_at=feed.next_retry_at,
                )
            )
            continue
        results.append(refresh_feed(feed))
    return results


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


def import_postmark_newsletter(
    *, payload: dict[str, Any], base_url: str
) -> NewsletterImportResult:
    message_id = str(payload.get("MessageID") or payload.get("MessageId") or "")
    if not message_id:
        raise ValueError("Postmark payload is missing MessageID.")

    subject = str(payload.get("Subject") or "Untitled newsletter")
    received_at = _aware_datetime(payload.get("Date"))
    existing = (
        NewsletterIssue.objects.select_related("article")
        .filter(message_id=message_id)
        .first()
    )
    if existing is not None:
        return NewsletterImportResult(issue=existing, created=False)

    public_id = uuid.uuid4()
    archive_url = newsletter_archive_url(base_url=base_url, public_id=public_id)
    feed = newsletter_feed()
    article = Article.objects.create(
        feed=feed,
        title=subject,
        url=archive_url,
        guid=message_id,
        author=_postmark_address(payload, "From"),
        summary=str(payload.get("TextBody") or payload.get("HtmlBody") or ""),
        published_at=received_at,
    )
    issue = NewsletterIssue.objects.create(
        article=article,
        public_id=public_id,
        message_id=message_id,
        from_email=_postmark_address(payload, "From"),
        from_name=_postmark_name(payload, "From"),
        to_email=_postmark_address(payload, "To"),
        subject=subject,
        html_body=str(payload.get("HtmlBody") or ""),
        text_body=str(payload.get("TextBody") or ""),
        received_at=received_at,
    )
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
        "img": ["src", "alt", "title", "width", "height"],
        "td": ["colspan", "rowspan"],
        "th": ["colspan", "rowspan", "scope"],
    }
    cleaned = bleach.clean(
        html,
        tags=tags,
        attributes=attributes,
        protocols={"http", "https", "mailto"},
        strip=True,
    )
    return bleach.linkify(
        cleaned,
        callbacks=[bleach.callbacks.nofollow, _newsletter_link_attrs],
        skip_tags={"pre", "code"},
    )


def _newsletter_link_attrs(attrs, new=False):
    attrs[(None, "target")] = "_blank"
    attrs[(None, "rel")] = "noopener noreferrer"
    return attrs


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


def _opml_outlines(
    element: ElementTree.Element, category_name: str = ""
) -> list[tuple[ElementTree.Element, str]]:
    outlines = []
    for child in element:
        if not child.tag.lower().endswith("outline"):
            outlines.extend(_opml_outlines(child, category_name))
            continue
        feed_url = child.attrib.get("xmlUrl") or child.attrib.get("xmlurl")
        if feed_url:
            outlines.append((child, category_name))
        else:
            child_category = (
                child.attrib.get("title") or child.attrib.get("text") or category_name
            )
            outlines.extend(_opml_outlines(child, child_category))
    return outlines


def _category_from_name(name: str) -> Category | None:
    if not name:
        return None
    base_slug = slugify(name) or "category"
    slug = base_slug
    suffix = 2
    while True:
        category = Category.objects.filter(slug=slug).first()
        if category is None:
            return Category.objects.create(name=name, slug=slug)
        if category.name == name:
            return category
        slug = f"{base_slug}-{suffix}"
        suffix += 1


def import_opml(content: bytes) -> ImportResult:
    root = ElementTree.fromstring(content)
    created = updated = skipped = 0
    for outline, category_name in _opml_outlines(root):
        feed_url = outline.attrib.get("xmlUrl") or outline.attrib.get("xmlurl")
        if not feed_url:
            skipped += 1
            continue
        title = outline.attrib.get("title") or outline.attrib.get("text") or feed_url
        site_url = outline.attrib.get("htmlUrl") or outline.attrib.get("htmlurl") or ""
        category = _category_from_name(category_name)
        _, was_created = Feed.objects.update_or_create(
            feed_url=feed_url,
            defaults={
                "title": title,
                "site_url": site_url,
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
    for feed in Feed.objects.filter(is_active=True).order_by("title", "feed_url"):
        attrs = {
            "text": feed.title,
            "title": feed.title,
            "type": "rss",
            "xmlUrl": feed.feed_url,
        }
        if feed.site_url:
            attrs["htmlUrl"] = feed.site_url
        ElementTree.SubElement(body, "outline", attrs)
    return ElementTree.tostring(root, encoding="unicode", xml_declaration=True)


def save_article(
    *, user: Any, article: Article, base_url: str, token: str
) -> SavedArticle:
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
    try:
        save_to_linkding(base_url=base_url, token=token, article=article)
    except Exception as exc:  # noqa: BLE001 - record external integration errors for the user.
        saved.linkding_saved = False
        saved.linkding_error = str(exc)
    else:
        saved.linkding_saved = True
        saved.linkding_error = ""
    saved.save(
        update_fields=[
            "url",
            "title",
            "feed",
            "category",
            "linkding_saved",
            "linkding_error",
            "updated_at",
        ]
    )
    return saved


def _linkding_description(article: Article) -> str:
    """Return a Linkding description without feed-only comments links."""

    parser = _TextExtractor()
    parser.feed(article.summary or "")
    description = parser.text()
    if description.lower() == "comments":
        return ""
    return description


def save_to_linkding(*, base_url: str, token: str, article: Article) -> dict[str, Any]:
    if not token:
        raise ValueError("LINKDING_TOKEN is not configured")
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
    if returned_url != article.url:
        raise ValueError(
            "Linkding returned a different bookmark URL: "
            f"expected {article.url!r}, received {returned_url!r}"
        )
    return bookmark
