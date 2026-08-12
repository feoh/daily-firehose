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
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import bleach
import feedparser
import requests
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
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
OPML_MAX_BYTES = 1024 * 1024
OPML_MAX_OUTLINES = 1000
OPML_MAX_DEPTH = 32
_OPML_UNSAFE_DECLARATION = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


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

    existing = (
        NewsletterIssue.objects.select_related("article")
        .filter(message_id=message_id)
        .first()
    )
    if existing is not None:
        return NewsletterImportResult(issue=existing, created=False)

    try:
        with transaction.atomic():
            subject = str(payload.get("Subject") or "Untitled newsletter")
            received_at = _aware_datetime(payload.get("Date"))
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


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _opml_attribute(element: ElementTree.Element, name: str) -> str:
    name = name.lower()
    return next(
        (value for key, value in element.attrib.items() if key.lower() == name), ""
    ).strip()


def _validated_opml_feed(outline: ElementTree.Element, category_name: str) -> _OPMLFeed:
    feed_url = _opml_attribute(outline, "xmlurl")
    title = (
        _opml_attribute(outline, "title")
        or _opml_attribute(outline, "text")
        or feed_url
    )
    site_url = _opml_attribute(outline, "htmlurl")
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
            feed_url = _opml_attribute(child, "xmlurl")
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
            if not child_category or not len(child):
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


@transaction.atomic
def _category_from_name(name: str) -> Category | None:
    if not name:
        return None
    if connection.vendor == "postgresql":
        # There may be no row to lock yet. A transaction-scoped advisory lock gives
        # every importer of the same category name one stable creation boundary.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", [name]
            )
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
    with transaction.atomic():
        for planned in planned_feeds:
            category = _category_from_name(planned.category_name)
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
    _enforce_article_save_policy(article)

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
