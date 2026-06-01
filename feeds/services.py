from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone as datetime_timezone
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree

import feedparser
import requests
from django.utils import timezone
from django.utils.text import slugify

from .models import Article, Category, Feed, SavedArticle


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
    parsed = feedparser.parse(feed.feed_url)
    feed_info = parsed.get("feed", {})
    feed.title = feed_info.get("title") or feed.title or feed.feed_url
    feed.site_url = feed_info.get("link") or feed.site_url
    feed.description = feed_info.get("subtitle") or feed_info.get("description") or feed.description
    feed.last_fetched_at = timezone.now()
    feed.save(update_fields=["title", "site_url", "description", "last_fetched_at", "updated_at"])

    created = 0
    updated = 0
    for entry in parsed.get("entries", []):
        url = entry.get("link") or entry.get("id")
        if not url:
            continue
        guid = entry.get("id") or url
        defaults = {
            "title": entry.get("title") or url,
            "url": url,
            "author": entry.get("author", ""),
            "summary": entry.get("summary", ""),
            "published_at": _aware_datetime(entry.get("published_parsed") or entry.get("updated_parsed") or entry.get("published") or entry.get("updated")),
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


def refresh_active_feeds() -> list[RefreshResult]:
    return [refresh_feed(feed) for feed in Feed.objects.filter(is_active=True)]


def discover_feed_metadata(feed_url: str) -> dict[str, str]:
    parsed = feedparser.parse(feed_url)
    info = parsed.get("feed", {})
    return {
        "title": info.get("title") or feed_url,
        "site_url": info.get("link") or "",
        "description": info.get("subtitle") or info.get("description") or "",
    }


def _opml_outlines(element: ElementTree.Element, category_name: str = "") -> list[tuple[ElementTree.Element, str]]:
    outlines = []
    for child in element:
        if not child.tag.lower().endswith("outline"):
            outlines.extend(_opml_outlines(child, category_name))
            continue
        feed_url = child.attrib.get("xmlUrl") or child.attrib.get("xmlurl")
        if feed_url:
            outlines.append((child, category_name))
        else:
            child_category = child.attrib.get("title") or child.attrib.get("text") or category_name
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
            defaults={"title": title, "site_url": site_url, "category": category, "is_active": True},
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


def save_article(*, user: Any, article: Article, base_url: str, token: str) -> SavedArticle:
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
    saved.save(update_fields=["url", "title", "feed", "category", "linkding_saved", "linkding_error", "updated_at"])
    return saved


def save_to_linkding(*, base_url: str, token: str, article: Article) -> None:
    if not token:
        raise ValueError("LINKDING_TOKEN is not configured")
    response = requests.post(
        f"{base_url.rstrip('/')}/api/bookmarks/",
        headers={"Authorization": f"Token {token}"},
        json={
            "url": article.url,
            "title": article.title,
            "description": article.summary,
        },
        timeout=15,
    )
    response.raise_for_status()
