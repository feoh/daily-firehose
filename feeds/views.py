from __future__ import annotations

from datetime import date, timedelta
from typing import Any, cast

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from daily_firehose.redirects import safe_redirect_target

from .feed_fetch import FeedFetchError
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
    ArticleSaveNotAllowed,
    OPMLImportError,
    discover_feed_metadata,
    export_opml,
    import_opml,
    refresh_active_feeds,
    save_article,
)

ARCHIVED_ARTICLE_LIMIT = 50
SAVED_ARTICLE_LIMIT = 50


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
        fetched_at__date__gte=start,
        fetched_at__date__lte=end,
    )
    if feed is not None:
        queryset = queryset.filter(feed=feed)
    return queryset.order_by("feed__title", "-fetched_at", "title")


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
            seen_day = timezone.localtime(article.fetched_at).date()
            feed_marked = (
                marker.scope == ReadScope.FEED
                and marker_feed_id == _model_field_id(article, "feed_id")
            )
            period_marked = (
                marker.period_start
                and marker.period_end
                and marker.period_start <= seen_day <= marker.period_end
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


def _archived_article_cards(user) -> list[dict]:
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


def _saved_article_cards(user) -> list[dict]:
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
    read_ids = _read_article_ids(user, articles)
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


def _preferences(user) -> UserPreference:
    preferences, _ = UserPreference.objects.get_or_create(user=user)
    return preferences


def _wants_json(request: HttpRequest) -> bool:
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def _redirect_to_posted_next(request: HttpRequest, *, fallback: str) -> HttpResponse:
    return redirect(
        safe_redirect_target(request, request.POST.get("next"), fallback=fallback)
    )


def newsletter_detail(request: HttpRequest, public_id) -> HttpResponse:
    issue = get_object_or_404(
        NewsletterIssue.objects.select_related("article", "article__feed"),
        public_id=public_id,
    )
    article = issue.article
    context: dict[str, Any] = {
        "issue": issue,
        "article": article,
        "is_read": False,
        # Sanitized once at ingest; a public page never re-cleans sender markup.
        "sanitized_html": issue.sanitized_html,
    }
    if request.user.is_authenticated:
        context["preferences"] = _preferences(request.user)
        context["is_read"] = _pk(article) in _read_article_ids(
            request.user, Article.objects.filter(pk=_pk(article))
        )

    response = render(
        request,
        "feeds/newsletter_detail.html",
        context,
    )
    response["X-Robots-Tag"] = "noindex"
    # Only this page renders third-party markup. Remote images stay loadable by
    # documented policy; scripts, frames, and form posts to other origins do not.
    response["Content-Security-Policy"] = (
        "default-src 'self'; "
        "img-src 'self' https: data:; "
        "style-src 'self'; "
        "script-src 'self'; "
        "object-src 'none'; "
        "frame-src 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'none'; "
        "form-action 'self'"
    )
    return response


@login_required
@never_cache
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
def archived(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "feeds/digest.html",
        {
            "title": "Archived (Marked Read)",
            "period_label": "Recently marked read",
            "cards": _archived_article_cards(request.user),
            "remove_on_success": True,
            "preferences": _preferences(request.user),
        },
    )


@login_required
def saved_links(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "feeds/digest.html",
        {
            "title": "Saved (L)inks",
            "period_label": "Recently saved to Linkding",
            "cards": _saved_article_cards(request.user),
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
        try:
            if not feed.title:
                metadata = discover_feed_metadata(feed.feed_url)
                feed.title = metadata["title"]
                feed.site_url = feed.site_url or metadata["site_url"]
                feed.description = feed.description or metadata["description"]
        except FeedFetchError as exc:
            form.add_error("feed_url", str(exc))
        else:
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
        try:
            result = import_opml(form.cleaned_data["opml_file"].read())
        except OPMLImportError:
            form.add_error("opml_file", "Upload a valid OPML file.")
        else:
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
    attempted = [result for result in results if result.status != "skipped"]
    failures = [result for result in results if result.status == "failed"]
    skipped = [result for result in results if result.status == "skipped"]
    superseded = [result for result in results if result.status == "superseded"]
    created = sum(result.created for result in results)
    updated = sum(result.updated for result in results)
    succeeded = sum(result.status == "succeeded" for result in results)
    feeds_with_new_articles = sum(
        1 for result in results if result.success and result.created > 0
    )
    summary = (
        f"Refresh complete: checked {len(results)} feeds; attempted {len(attempted)}; "
        f"succeeded {succeeded}; failed {len(failures)}; skipped {len(skipped)}; "
        f"superseded {len(superseded)}; {feeds_with_new_articles} feeds had new "
        f"articles; {created} new articles; {updated} existing articles updated."
    )
    if failures:
        failed_titles = ", ".join(result.feed.title for result in failures)
        messages.warning(request, f"{summary} Failed feeds: {failed_titles}.")
    elif skipped:
        skipped_titles = ", ".join(result.feed.title for result in skipped)
        messages.warning(request, f"{summary} Backoff feeds: {skipped_titles}.")
    else:
        messages.success(request, summary)
    if superseded:
        superseded_titles = ", ".join(result.feed.title for result in superseded)
        messages.info(request, f"Superseded feeds: {superseded_titles}.")
    return _redirect_to_posted_next(request, fallback=reverse("today"))


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
        remove = is_read or request.POST.get("remove_on_success") == "true"
        return JsonResponse({"message": message, "level": "success", "remove": remove})
    messages.success(request, message)
    return _redirect_to_posted_next(request, fallback=reverse("today"))


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
    return _redirect_to_posted_next(request, fallback=reverse("today"))


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
    return _redirect_to_posted_next(
        request, fallback=reverse("feed-detail", args=[_pk(feed)])
    )


@require_POST
@login_required
def save_article_view(request: HttpRequest, article_id: int) -> HttpResponse:
    article = get_object_or_404(
        Article.objects.select_related("feed", "feed__category", "newsletter_issue"),
        id=article_id,
    )
    posted_article_id = request.POST.get("article_id")
    posted_article_url = request.POST.get("article_url")
    if (posted_article_id and posted_article_id != str(article_id)) or (
        posted_article_url and posted_article_url != article.url
    ):
        message = "Article verification failed. Please refresh and try again."
        if _wants_json(request):
            return JsonResponse(
                {"message": message, "level": "error", "remove": False}, status=400
            )
        return HttpResponseBadRequest(message)

    try:
        saved = save_article(
            user=request.user,
            article=article,
            base_url=settings.LINKDING_URL,
            token=settings.LINKDING_TOKEN,
        )
    except ArticleSaveNotAllowed as exc:
        if _wants_json(request):
            return JsonResponse(
                {
                    "message": exc.message,
                    "level": "error",
                    "remove": False,
                    "error": {"code": exc.code, "message": exc.message},
                }
            )
        messages.error(request, exc.message)
        return _redirect_to_posted_next(request, fallback=reverse("today"))
    if saved.linkding_saved:
        message = f"Saved “{saved.title}” to Linkding and Daily Firehose."
        level = "success"
    else:
        message = f"Saved locally, but Linkding failed: {saved.linkding_error}"
        level = "warning"
    if _wants_json(request):
        return JsonResponse(
            {
                "message": message,
                "level": level,
                "remove": saved.linkding_saved,
                "article": {
                    "id": article_id,
                    "title": saved.title,
                    "url": saved.url,
                },
            }
        )
    if saved.linkding_saved:
        messages.success(request, message)
    else:
        messages.warning(request, message)
    return _redirect_to_posted_next(request, fallback=reverse("today"))


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
