from __future__ import annotations

from typing import Any, cast

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import (
    require_http_methods,
    require_POST,
    require_safe,
)

from daily_firehose.redirects import safe_redirect_target

from . import commands
from .feed_fetch import FeedFetchError
from .forms import FeedForm, OPMLImportForm, ThemeForm
from .models import Article, Feed, NewsletterIssue, ReadScope
from .queries import (
    archived_article_cards,
    article_card_page,
    articles_between,
    feed_article_limit,
    month_bounds,
    read_article_ids,
    saved_article_cards,
    user_preference,
    week_bounds,
)
from .recommendations import recommendation_cards
from .services import (
    ArticleSaveNotAllowed,
    OPMLImportError,
    discover_feed_metadata,
    export_opml,
    import_opml,
    save_article,
)


def _pk(model: Any) -> int:
    return cast(int, model.id)


def _wants_json(request: HttpRequest) -> bool:
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def _redirect_to_posted_next(request: HttpRequest, *, fallback: str) -> HttpResponse:
    return redirect(
        safe_redirect_target(request, request.POST.get("next"), fallback=fallback)
    )


@require_safe
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
        context["preferences"] = user_preference(request.user)
        context["is_read"] = _pk(article) in read_article_ids(
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


@require_safe
@login_required
@never_cache
def today(request: HttpRequest) -> HttpResponse:
    current = timezone.localdate()
    articles = articles_between(current, current)
    page = article_card_page(request.user, articles)
    return render(
        request,
        "feeds/digest.html",
        {
            "title": "Today’s Firehose",
            "period_label": current.strftime("%B %-d, %Y"),
            "cards": page.cards,
            "has_more": page.has_more,
            "article_limit": page.limit,
            "scope": ReadScope.DAY,
            "period_start": current,
            "period_end": current,
            "preferences": user_preference(request.user),
        },
    )


@require_safe
@login_required
def week(request: HttpRequest) -> HttpResponse:
    start, end = week_bounds(timezone.localdate())
    page = article_card_page(request.user, articles_between(start, end))
    return render(
        request,
        "feeds/digest.html",
        {
            "title": "This Week’s Firehose",
            "period_label": f"{start:%B %-d} – {end:%B %-d, %Y}",
            "cards": page.cards,
            "has_more": page.has_more,
            "article_limit": page.limit,
            "scope": ReadScope.WEEK,
            "period_start": start,
            "period_end": end,
            "preferences": user_preference(request.user),
        },
    )


@require_safe
@login_required
def month(request: HttpRequest) -> HttpResponse:
    start, end = month_bounds(timezone.localdate())
    page = article_card_page(request.user, articles_between(start, end))
    return render(
        request,
        "feeds/digest.html",
        {
            "title": "This Month’s Firehose",
            "period_label": f"{start:%B %Y}",
            "cards": page.cards,
            "has_more": page.has_more,
            "article_limit": page.limit,
            "scope": ReadScope.MONTH,
            "period_start": start,
            "period_end": end,
            "preferences": user_preference(request.user),
        },
    )


@require_safe
@login_required
@never_cache
def recommended(request: HttpRequest) -> HttpResponse:
    page = recommendation_cards(request.user)
    return render(
        request,
        "feeds/digest.html",
        {
            "title": "Recommended",
            "period_label": "Your saved-link taste profile",
            "intro": (
                f"Ranked from every article using {page.profile_size} saved "
                f"article{'s' if page.profile_size != 1 else ''}; article age is not "
                "part of the score."
                if page.profile_size
                else "Save some articles and personalized recommendations will appear here."
            ),
            "cards": page.cards,
            "empty_message": (
                "No personalized matches yet. Save articles you value and try again."
            ),
            "preferences": user_preference(request.user),
        },
    )


@require_safe
@login_required
def archived(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "feeds/digest.html",
        {
            "title": "Archived (Marked Read)",
            "period_label": "Recently marked read",
            "cards": archived_article_cards(request.user),
            "remove_on_success": True,
            "preferences": user_preference(request.user),
        },
    )


@require_safe
@login_required
def saved_links(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "feeds/digest.html",
        {
            "title": "Saved (L)inks",
            "period_label": "Recently saved to Linkding",
            "cards": saved_article_cards(request.user),
            "preferences": user_preference(request.user),
        },
    )


@require_safe
@login_required
def feed_detail(request: HttpRequest, feed_id: int) -> HttpResponse:
    feed = get_object_or_404(Feed, id=feed_id)
    articles = Article.objects.select_related("feed", "feed__category").filter(
        feed=feed
    )
    page = article_card_page(request.user, articles, limit=feed_article_limit())
    return render(
        request,
        "feeds/feed_detail.html",
        {
            "feed": feed,
            "cards": page.cards,
            "has_more": page.has_more,
            "article_limit": page.limit,
            "preferences": user_preference(request.user),
        },
    )


@require_http_methods(["GET", "HEAD", "POST"])
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
            "preferences": user_preference(request.user),
        },
    )


@require_http_methods(["GET", "HEAD", "POST"])
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
        {"form": form, "preferences": user_preference(request.user)},
    )


@require_safe
@login_required
def opml_export(request: HttpRequest) -> HttpResponse:
    response = HttpResponse(export_opml(), content_type="text/x-opml; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="daily-firehose-feeds.opml"'
    return response


@require_http_methods(["GET", "HEAD", "POST"])
@login_required
def preferences(request: HttpRequest) -> HttpResponse:
    prefs = user_preference(request.user)
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
    tally = commands.run_feed_refresh()
    summary = (
        f"Refresh complete: checked {tally.checked} feeds; "
        f"attempted {len(tally.attempted)}; succeeded {tally.succeeded}; "
        f"failed {len(tally.failures)}; skipped {len(tally.skipped)}; "
        f"superseded {len(tally.superseded)}; {tally.feeds_with_new_articles} feeds "
        f"had new articles; {tally.created} new articles; "
        f"{tally.updated} existing articles updated."
    )
    if tally.failures:
        failed_titles = ", ".join(result.feed.title for result in tally.failures)
        messages.warning(request, f"{summary} Failed feeds: {failed_titles}.")
    elif tally.skipped:
        skipped_titles = ", ".join(result.feed.title for result in tally.skipped)
        messages.warning(request, f"{summary} Backoff feeds: {skipped_titles}.")
    else:
        messages.success(request, summary)
    if tally.superseded:
        superseded_titles = ", ".join(result.feed.title for result in tally.superseded)
        messages.info(request, f"Superseded feeds: {superseded_titles}.")
    return _redirect_to_posted_next(request, fallback=reverse("today"))


@require_POST
@login_required
def mark_article(request: HttpRequest, article_id: int) -> HttpResponse:
    article = get_object_or_404(Article, id=article_id)
    is_read = request.POST.get("state", "read") == "read"
    commands.mark_article_read(user=request.user, article=article, is_read=is_read)
    message = "Marked article read." if is_read else "Marked article unread."
    if _wants_json(request):
        remove = is_read or request.POST.get("remove_on_success") == "true"
        return JsonResponse({"message": message, "level": "success", "remove": remove})
    messages.success(request, message)
    return _redirect_to_posted_next(request, fallback=reverse("today"))


@require_POST
@login_required
def mark_period_read(request: HttpRequest) -> HttpResponse:
    try:
        commands.mark_period_read(
            user=request.user,
            scope=commands.parse_period_scope(request.POST.get("scope")),
            period_start=commands.parse_iso_date(
                request.POST.get("period_start"), "period_start"
            ),
            period_end=commands.parse_iso_date(
                request.POST.get("period_end"), "period_end"
            ),
        )
    except ValidationError:
        return HttpResponseBadRequest("Invalid period selection.")
    messages.success(request, "Marked this period read.")
    return _redirect_to_posted_next(request, fallback=reverse("today"))


@require_POST
@login_required
def mark_feed_read(request: HttpRequest, feed_id: int) -> HttpResponse:
    feed = get_object_or_404(Feed, id=feed_id)
    commands.mark_feed_read(user=request.user, feed=feed)
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
    articles = articles_between(current, current)
    page = article_card_page(request.user, articles)
    cards = page.cards
    return JsonResponse(
        {
            "title": "Today’s Firehose",
            "date": current.isoformat(),
            "has_more": page.has_more,
            "limit": page.limit,
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
