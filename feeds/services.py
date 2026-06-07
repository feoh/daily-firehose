from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone as datetime_timezone
import uuid
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import re
from typing import Any, cast
from urllib.parse import urljoin
from xml.etree import ElementTree

import bleach
import feedparser
import requests
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from .models import Article, Category, Feed, NewsletterIssue, SavedArticle

LINKDING_TOREAD_TAG = "toread"
NEWSLETTER_FEED_URL = "https://daily-firehose.local/feeds/email-newsletters"
NEWSLETTER_FEED_TITLE = "Email Newsletters"


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
    created: int
    updated: int


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
            parsed = datetime(*value[:6])
        except (TypeError, ValueError):
            return timezone.now()
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone=datetime_timezone.utc)
    return parsed


def refresh_feed(feed: Feed) -> RefreshResult:
    parsed = cast(Any, feedparser.parse(feed.feed_url))
    feed_info = parsed.get("feed", {})
    feed.title = feed_info.get("title") or feed.title or feed.feed_url
    feed.site_url = feed_info.get("link") or feed.site_url
    feed.description = (
        feed_info.get("subtitle") or feed_info.get("description") or feed.description
    )
    feed.last_fetched_at = timezone.now()
    feed.save(
        update_fields=[
            "title",
            "site_url",
            "description",
            "last_fetched_at",
            "updated_at",
        ]
    )

    created = 0
    updated = 0
    for entry in parsed.get("entries", []):
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
    return RefreshResult(feed=feed, created=created, updated=updated)


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
    return [refresh_feed(feed) for feed in Feed.objects.filter(is_active=True)]


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


def _newsletter_link_attrs(attrs, new=False):  # noqa: ANN001, ANN202
    attrs[(None, "target")] = "_blank"
    attrs[(None, "rel")] = "noopener noreferrer"
    return attrs


def discover_feed_metadata(feed_url: str) -> dict[str, str]:
    parsed = cast(Any, feedparser.parse(feed_url))
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


def save_to_linkding(*, base_url: str, token: str, article: Article) -> None:
    if not token:
        raise ValueError("LINKDING_TOKEN is not configured")
    response = requests.post(
        f"{base_url.rstrip('/')}/api/bookmarks/",
        headers={"Authorization": f"Token {token}"},
        json={
            "url": article.url,
            "title": article.title,
            "description": _linkding_description(article),
            "tag_names": [LINKDING_TOREAD_TAG],
        },
        timeout=15,
    )
    response.raise_for_status()
