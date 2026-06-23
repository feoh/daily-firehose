from __future__ import annotations

from datetime import date, timedelta
from typing import Any, cast

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import FeedForm, OPMLImportForm, ThemeForm
from .models import (
    Article,
    ArticleReadState,
    BulkReadMarker,
    Feed,
    NewsletterIssue,
    ReadScope,
    SavedArticle,
    UserPreference,
)
from .services import (
    discover_feed_metadata,
    export_opml,
    import_opml,
    refresh_active_feeds,
    sanitize_newsletter_html,
    save_article,
)


def _week_bounds(day: date) -> tuple[date, date]:
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def _month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1) - timedelta(days=1)
    else:
        end = start.replace(month=start.month + 1) - timedelta(days=1)
    return start, end


def _pk(model: Any) -> int:
    return cast(int, model.id)


def _model_field_id(model: Any, field_name: str) -> int | None:
    value = getattr(model, field_name)
    return cast(int | None, value)


def _articles_between(
    start: date, end: date, feed: Feed | None = None
) -> QuerySet[Article]:
    queryset = Article.objects.select_related("feed", "feed__category").filter(
        published_at__date__gte=start,
        published_at__date__lte=end,
    )
    if feed is not None:
        queryset = queryset.filter(feed=feed)
    return queryset.order_by("feed__title", "-published_at", "title")


def _read_article_ids(user, articles: QuerySet[Article]) -> set[int]:
    ids = list(articles.values_list("id", flat=True))
    explicit_read = set(
        ArticleReadState.objects.filter(
            user=user, article_id__in=ids, is_read=True
        ).values_list("article_id", flat=True)
    )
    explicit_unread = set(
        ArticleReadState.objects.filter(
            user=user, article_id__in=ids, is_read=False
        ).values_list("article_id", flat=True)
    )
    bulk_markers = BulkReadMarker.objects.filter(user=user)
    for marker in bulk_markers:
        marker_feed_id = _model_field_id(marker, "feed_id")
        for article in articles:
            if article.fetched_at > marker.marked_read_at:
                continue
            article_id = _pk(article)
            published_day = timezone.localtime(article.published_at).date()
            feed_marked = (
                marker.scope == ReadScope.FEED
                and marker_feed_id == _model_field_id(article, "feed_id")
            )
            period_marked = (
                marker.period_start
                and marker.period_end
                and marker.period_start <= published_day <= marker.period_end
                and marker.scope in {ReadScope.DAY, ReadScope.WEEK, ReadScope.MONTH}
            )
            if feed_marked or period_marked:
                explicit_read.add(article_id)
    return explicit_read - explicit_unread


def _mark_articles_read(user, articles: QuerySet[Article]) -> None:
    user_id = _pk(user)
    article_ids = list(articles.values_list("id", flat=True))
    if not article_ids:
        return
    updated_at = timezone.now()
    ArticleReadState.objects.bulk_create(
        [
            ArticleReadState(
                user_id=user_id,
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


def _article_cards(user, articles: QuerySet[Article]) -> list[dict]:
    read_ids = _read_article_ids(user, articles)
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


def _preferences(user) -> UserPreference:
    preferences, _ = UserPreference.objects.get_or_create(user=user)
    return preferences


def _wants_json(request: HttpRequest) -> bool:
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def newsletter_detail(request: HttpRequest, public_id) -> HttpResponse:  # noqa: ANN001
    issue = get_object_or_404(
        NewsletterIssue.objects.select_related("article", "article__feed"),
        public_id=public_id,
    )
    response = render(
        request,
        "feeds/newsletter_detail.html",
        {
            "issue": issue,
            "article": issue.article,
            "sanitized_html": sanitize_newsletter_html(issue.html_body)
            if issue.html_body
            else "",
        },
    )
    response["X-Robots-Tag"] = "noindex"
    return response


@login_required
def today(request: HttpRequest) -> HttpResponse:
    current = timezone.localdate()
    articles = _articles_between(current, current)
    cards = _article_cards(request.user, articles)
    return render(
        request,
        "feeds/digest.html",
        {
            "title": "Today’s Firehose",
            "period_label": current.strftime("%B %-d, %Y"),
            "cards": cards,
            "scope": ReadScope.DAY,
            "period_start": current,
            "period_end": current,
            "preferences": _preferences(request.user),
        },
    )


@login_required
def week(request: HttpRequest) -> HttpResponse:
    start, end = _week_bounds(timezone.localdate())
    articles = _articles_between(start, end)
    return render(
        request,
        "feeds/digest.html",
        {
            "title": "This Week’s Firehose",
            "period_label": f"{start:%B %-d} – {end:%B %-d, %Y}",
            "cards": _article_cards(request.user, articles),
            "scope": ReadScope.WEEK,
            "period_start": start,
            "period_end": end,
            "preferences": _preferences(request.user),
        },
    )


@login_required
def month(request: HttpRequest) -> HttpResponse:
    start, end = _month_bounds(timezone.localdate())
    articles = _articles_between(start, end)
    return render(
        request,
        "feeds/digest.html",
        {
            "title": "This Month’s Firehose",
            "period_label": f"{start:%B %Y}",
            "cards": _article_cards(request.user, articles),
            "scope": ReadScope.MONTH,
            "period_start": start,
            "period_end": end,
            "preferences": _preferences(request.user),
        },
    )


@login_required
def feed_detail(request: HttpRequest, feed_id: int) -> HttpResponse:
    feed = get_object_or_404(Feed, id=feed_id)
    articles = Article.objects.select_related("feed", "feed__category").filter(
        feed=feed
    )[:100]
    return render(
        request,
        "feeds/feed_detail.html",
        {
            "feed": feed,
            "cards": _article_cards(request.user, articles),
            "preferences": _preferences(request.user),
        },
    )


@login_required
def feed_list(request: HttpRequest) -> HttpResponse:
    form = FeedForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        feed = form.save(commit=False)
        if not feed.title:
            metadata = discover_feed_metadata(feed.feed_url)
            feed.title = metadata["title"]
            feed.site_url = feed.site_url or metadata["site_url"]
            feed.description = feed.description or metadata["description"]
        feed.save()
        messages.success(request, f"Added feed {feed.title}.")
        return redirect("feeds")
    return render(
        request,
        "feeds/feed_list.html",
        {
            "feeds": Feed.objects.select_related("category").order_by(
                "category__name", "title", "feed_url"
            ),
            "form": form,
            "postmark_inbound_email": settings.POSTMARK_INBOUND_EMAIL,
            "preferences": _preferences(request.user),
        },
    )


@login_required
def opml_import(request: HttpRequest) -> HttpResponse:
    form = OPMLImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        result = import_opml(form.cleaned_data["opml_file"].read())
        messages.success(
            request,
            f"Imported OPML: {result.created} created, {result.updated} updated, {result.skipped} skipped.",
        )
        return redirect("feeds")
    return render(
        request,
        "feeds/opml_import.html",
        {"form": form, "preferences": _preferences(request.user)},
    )


@login_required
def opml_export(request: HttpRequest) -> HttpResponse:
    response = HttpResponse(export_opml(), content_type="text/x-opml; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="daily-firehose-feeds.opml"'
    return response


@login_required
def preferences(request: HttpRequest) -> HttpResponse:
    prefs = _preferences(request.user)
    form = ThemeForm(request.POST or None, instance=prefs)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Preferences saved.")
        return redirect("preferences")
    return render(
        request, "feeds/preferences.html", {"form": form, "preferences": prefs}
    )


@require_POST
@login_required
def refresh_feeds(request: HttpRequest) -> HttpResponse:
    results = refresh_active_feeds()
    created = sum(result.created for result in results)
    updated = sum(result.updated for result in results)
    feeds_with_new_articles = sum(1 for result in results if result.created > 0)
    messages.success(
        request,
        (
            f"Refresh complete: checked {len(results)} feeds; "
            f"{feeds_with_new_articles} feeds had new articles; "
            f"{created} new articles; {updated} existing articles updated."
        ),
    )
    return redirect(request.POST.get("next") or reverse("today"))


@require_POST
@login_required
def mark_article(request: HttpRequest, article_id: int) -> HttpResponse:
    article = get_object_or_404(Article, id=article_id)
    is_read = request.POST.get("state", "read") == "read"
    user_id = _pk(request.user)
    ArticleReadState.objects.update_or_create(
        user_id=user_id, article=article, defaults={"is_read": is_read}
    )
    message = "Marked article read." if is_read else "Marked article unread."
    if _wants_json(request):
        return JsonResponse({"message": message, "level": "success", "remove": is_read})
    messages.success(request, message)
    return redirect(request.POST.get("next") or reverse("today"))


@require_POST
@login_required
def mark_period_read(request: HttpRequest) -> HttpResponse:
    scope = request.POST["scope"]
    start = date.fromisoformat(request.POST["period_start"])
    end = date.fromisoformat(request.POST["period_end"])
    user_id = _pk(request.user)
    marked_read_at = timezone.now()
    _mark_articles_read(
        request.user,
        _articles_between(start, end).filter(fetched_at__lte=marked_read_at),
    )
    BulkReadMarker.objects.update_or_create(
        user_id=user_id,
        scope=scope,
        feed=None,
        period_start=start,
        period_end=end,
        defaults={"marked_read_at": marked_read_at},
    )
    messages.success(request, "Marked this period read.")
    return redirect(request.POST.get("next") or reverse("today"))


@require_POST
@login_required
def mark_feed_read(request: HttpRequest, feed_id: int) -> HttpResponse:
    feed = get_object_or_404(Feed, id=feed_id)
    user_id = _pk(request.user)
    marked_read_at = timezone.now()
    _mark_articles_read(
        request.user,
        Article.objects.filter(feed=feed, fetched_at__lte=marked_read_at),
    )
    BulkReadMarker.objects.update_or_create(
        user_id=user_id,
        scope=ReadScope.FEED,
        feed=feed,
        period_start=None,
        period_end=None,
        defaults={"marked_read_at": marked_read_at},
    )
    messages.success(request, f"Marked {feed.title} read.")
    return redirect(
        request.POST.get("next") or reverse("feed-detail", args=[_pk(feed)])
    )


@require_POST
@login_required
def save_article_view(request: HttpRequest, article_id: int) -> HttpResponse:
    article = get_object_or_404(Article, id=article_id)
    saved = save_article(
        user=request.user,
        article=article,
        base_url=settings.LINKDING_URL,
        token=settings.LINKDING_TOKEN,
    )
    if saved.linkding_saved:
        message = "Saved article to Linkding and Daily Firehose."
        level = "success"
    else:
        message = f"Saved locally, but Linkding failed: {saved.linkding_error}"
        level = "warning"
    if _wants_json(request):
        return JsonResponse({"message": message, "level": level, "remove": True})
    if saved.linkding_saved:
        messages.success(request, message)
    else:
        messages.warning(request, message)
    return redirect(request.POST.get("next") or reverse("today"))


@login_required
def digest_json(request: HttpRequest) -> JsonResponse:
    current = timezone.localdate()
    articles = _articles_between(current, current)
    cards = _article_cards(request.user, articles)
    return JsonResponse(
        {
            "title": "Today’s Firehose",
            "date": current.isoformat(),
            "articles": [
                {
                    "id": card["article"].id,
                    "title": card["article"].title,
                    "url": card["article"].url,
                    "feed": card["article"].feed.title,
                    "category": card["article"].feed.category.name
                    if card["article"].feed.category
                    else None,
                    "published_at": card["article"].published_at.isoformat(),
                    "summary": card["article"].summary,
                    "is_read": card["is_read"],
                    "is_saved": card["is_saved"],
                }
                for card in cards
            ],
        }
    )
