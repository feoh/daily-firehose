from __future__ import annotations

from datetime import date, timedelta

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
from .models import Article, ArticleReadState, BulkReadMarker, Feed, ReadScope, SavedArticle, UserPreference
from .services import discover_feed_metadata, export_opml, import_opml, save_article


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


def _articles_between(start: date, end: date, feed: Feed | None = None) -> QuerySet[Article]:
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
        ArticleReadState.objects.filter(user=user, article_id__in=ids, is_read=True).values_list(
            "article_id", flat=True
        )
    )
    explicit_unread = set(
        ArticleReadState.objects.filter(user=user, article_id__in=ids, is_read=False).values_list(
            "article_id", flat=True
        )
    )
    bulk_markers = BulkReadMarker.objects.filter(user=user)
    for marker in bulk_markers:
        for article in articles:
            published_day = timezone.localtime(article.published_at).date()
            if marker.scope == ReadScope.FEED and marker.feed_id == article.feed_id:
                explicit_read.add(article.id)
            elif marker.period_start and marker.period_end and marker.period_start <= published_day <= marker.period_end:
                if marker.scope in {ReadScope.DAY, ReadScope.WEEK, ReadScope.MONTH}:
                    explicit_read.add(article.id)
    return explicit_read - explicit_unread


def _article_cards(user, articles: QuerySet[Article]) -> list[dict]:
    read_ids = _read_article_ids(user, articles)
    saved_ids = set(
        SavedArticle.objects.filter(user=user, article_id__in=articles.values_list("id", flat=True)).values_list(
            "article_id", flat=True
        )
    )
    return [
        {"article": article, "is_read": article.id in read_ids, "is_saved": article.id in saved_ids}
        for article in articles
    ]


def _preferences(user) -> UserPreference:
    preferences, _ = UserPreference.objects.get_or_create(user=user)
    return preferences


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
    articles = Article.objects.select_related("feed", "feed__category").filter(feed=feed)[:100]
    return render(
        request,
        "feeds/feed_detail.html",
        {"feed": feed, "cards": _article_cards(request.user, articles), "preferences": _preferences(request.user)},
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
        {"feeds": Feed.objects.select_related("category"), "form": form, "preferences": _preferences(request.user)},
    )


@login_required
def opml_import(request: HttpRequest) -> HttpResponse:
    form = OPMLImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        result = import_opml(form.cleaned_data["opml_file"].read())
        messages.success(request, f"Imported OPML: {result.created} created, {result.updated} updated, {result.skipped} skipped.")
        return redirect("feeds")
    return render(request, "feeds/opml_import.html", {"form": form, "preferences": _preferences(request.user)})


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
    return render(request, "feeds/preferences.html", {"form": form, "preferences": prefs})


@require_POST
@login_required
def mark_article(request: HttpRequest, article_id: int) -> HttpResponse:
    article = get_object_or_404(Article, id=article_id)
    is_read = request.POST.get("state", "read") == "read"
    user_id = request.user.id
    assert user_id is not None
    ArticleReadState.objects.update_or_create(user_id=user_id, article=article, defaults={"is_read": is_read})
    return redirect(request.POST.get("next") or reverse("today"))


@require_POST
@login_required
def mark_period_read(request: HttpRequest) -> HttpResponse:
    scope = request.POST["scope"]
    start = date.fromisoformat(request.POST["period_start"])
    end = date.fromisoformat(request.POST["period_end"])
    user_id = request.user.id
    assert user_id is not None
    BulkReadMarker.objects.update_or_create(
        user_id=user_id,
        scope=scope,
        feed=None,
        period_start=start,
        period_end=end,
        defaults={},
    )
    messages.success(request, "Marked this period read.")
    return redirect(request.POST.get("next") or reverse("today"))


@require_POST
@login_required
def mark_feed_read(request: HttpRequest, feed_id: int) -> HttpResponse:
    feed = get_object_or_404(Feed, id=feed_id)
    user_id = request.user.id
    assert user_id is not None
    BulkReadMarker.objects.update_or_create(
        user_id=user_id,
        scope=ReadScope.FEED,
        feed=feed,
        period_start=None,
        period_end=None,
        defaults={},
    )
    messages.success(request, f"Marked {feed.title} read.")
    return redirect(request.POST.get("next") or reverse("feed-detail", args=[feed.id]))


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
        messages.success(request, "Saved article to Linkding and Daily Firehose.")
    else:
        messages.warning(request, f"Saved locally, but Linkding failed: {saved.linkding_error}")
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
                    "category": card["article"].feed.category.name if card["article"].feed.category else None,
                    "published_at": card["article"].published_at.isoformat(),
                    "summary": card["article"].summary,
                    "is_read": card["is_read"],
                    "is_saved": card["is_saved"],
                }
                for card in cards
            ],
        }
    )
