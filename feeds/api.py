from __future__ import annotations

import hmac
import json
from collections.abc import Callable
from datetime import date
from functools import wraps
from typing import Any, Concatenate, ParamSpec, cast

from django.apps import apps
from django.conf import settings
from django.db import IntegrityError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import (
    ApiToken,
    Article,
    ArticleReadState,
    BulkReadMarker,
    Category,
    Feed,
    ReadScope,
    SavedArticle,
    UserPreference,
)
from .services import (
    discover_feed_metadata,
    import_postmark_newsletter,
    refresh_active_feeds,
    save_article,
)
from .views import (
    _article_cards,
    _articles_between,
    _mark_articles_read,
    _month_bounds,
    _preferences,
    _read_article_ids,
    _week_bounds,
)

P = ParamSpec("P")


def _json_error(
    message: str, status: int = 400, *, code: str = "bad_request"
) -> JsonResponse:
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def _parse_json(request: HttpRequest) -> dict[str, Any]:
    if not request.body:
        return {}
    try:
        value = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Request body must be valid JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError("Request body must be a JSON object.")
    return value


def _authenticated_api_user(request: HttpRequest):
    header = request.headers.get("Authorization", "")
    scheme, _, key = header.partition(" ")
    if scheme.lower() not in {"bearer", "token"} or not key:
        return None
    token = (
        ApiToken.objects.select_related("user")
        .filter(
            key_hash=ApiToken.hash_key(key),
            is_active=True,
            user__is_active=True,
        )
        .first()
    )
    if token is None:
        return None
    token.last_used_at = timezone.now()
    token.save(update_fields=["last_used_at"])
    return token.user


def api_view(
    methods: set[str],
) -> Callable[
    [Callable[Concatenate[HttpRequest, Any, P], HttpResponse]],
    Callable[Concatenate[HttpRequest, P], HttpResponse],
]:
    def decorator(
        view: Callable[Concatenate[HttpRequest, Any, P], HttpResponse],
    ) -> Callable[Concatenate[HttpRequest, P], HttpResponse]:
        @csrf_exempt
        @wraps(view)
        def wrapped(
            request: HttpRequest, *args: P.args, **kwargs: P.kwargs
        ) -> HttpResponse:
            if request.method not in methods:
                return _json_error(
                    "Method not allowed.", status=405, code="method_not_allowed"
                )
            user = _authenticated_api_user(request)
            if user is None:
                return _json_error(
                    "Provide a valid API token in the Authorization header.",
                    status=401,
                    code="unauthorized",
                )
            request.user = user
            try:
                return view(request, user, *args, **kwargs)
            except ValueError as exc:
                return _json_error(str(exc))

        return wrapped

    return decorator


def _pk(model: Any) -> int:
    return cast(int, model.id)


@csrf_exempt
def postmark_inbound(request: HttpRequest, secret: str) -> JsonResponse:
    if request.method != "POST":
        return _json_error("Method not allowed.", status=405, code="method_not_allowed")
    configured_secret = settings.POSTMARK_INBOUND_SECRET
    if not configured_secret or not hmac.compare_digest(secret, configured_secret):
        return _json_error(
            "Invalid inbound email secret.", status=403, code="forbidden"
        )
    try:
        payload = _parse_json(request)
        result = import_postmark_newsletter(
            payload=payload, base_url=request.build_absolute_uri("/")
        )
    except ValueError as exc:
        return _json_error(str(exc))
    return JsonResponse(
        {"id": _pk(result.issue), "created": result.created},
        status=201 if result.created else 200,
    )


def _category_payload(category: Category | None) -> dict[str, Any] | None:
    if category is None:
        return None
    return {"id": _pk(category), "name": category.name, "slug": category.slug}


def _feed_payload(feed: Feed) -> dict[str, Any]:
    return {
        "id": _pk(feed),
        "title": feed.title,
        "feed_url": feed.feed_url,
        "site_url": feed.site_url,
        "description": feed.description,
        "category": _category_payload(feed.category),
        "is_active": feed.is_active,
        "last_fetched_at": feed.last_fetched_at.isoformat()
        if feed.last_fetched_at
        else None,
    }


def _article_payload(
    article: Article, *, is_read: bool, is_saved: bool
) -> dict[str, Any]:
    return {
        "id": _pk(article),
        "title": article.title,
        "url": article.url,
        "guid": article.guid,
        "author": article.author,
        "summary": article.summary,
        "published_at": article.published_at.isoformat(),
        "feed": _feed_payload(article.feed),
        "is_read": is_read,
        "is_saved": is_saved,
    }


def _article_window(period: str) -> tuple[date, date]:
    current = timezone.localdate()
    if period == "today":
        return current, current
    if period == "week":
        return _week_bounds(current)
    if period == "month":
        return _month_bounds(current)
    raise ValueError("period must be one of: today, week, month.")


@api_view({"GET"})
def article_list(request: HttpRequest, user) -> JsonResponse:
    period = request.GET.get("period", "today")
    start_value = request.GET.get("start")
    end_value = request.GET.get("end")
    if start_value or end_value:
        if not start_value or not end_value:
            raise ValueError("Provide both start and end dates, or neither.")
        start = date.fromisoformat(start_value)
        end = date.fromisoformat(end_value)
    else:
        start, end = _article_window(period)
    feed_id = request.GET.get("feed_id")
    feed = get_object_or_404(Feed, id=feed_id) if feed_id else None
    include_read = request.GET.get("include_read", "false").lower() == "true"
    include_saved = request.GET.get("include_saved", "false").lower() == "true"
    articles = _articles_between(start, end, feed).select_related(
        "feed", "feed__category"
    )
    read_ids = _read_article_ids(user, articles)
    article_ids = list(articles.values_list("id", flat=True))
    saved_ids = set(
        SavedArticle.objects.filter(user=user, article_id__in=article_ids).values_list(
            "article_id", flat=True
        )
    )
    payload = []
    for article in articles:
        article_id = _pk(article)
        is_read = article_id in read_ids
        is_saved = article_id in saved_ids
        if (is_read and not include_read) or (is_saved and not include_saved):
            continue
        payload.append(_article_payload(article, is_read=is_read, is_saved=is_saved))
    return JsonResponse(
        {
            "period": period,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "articles": payload,
        }
    )


@api_view({"GET"})
def morning_briefing(request: HttpRequest, user) -> JsonResponse:
    current = timezone.localdate()
    articles = _articles_between(current, current).select_related(
        "feed", "feed__category"
    )
    cards = _article_cards(user, articles)
    return JsonResponse(
        {
            "title": "Today’s Firehose",
            "date": current.isoformat(),
            "articles": [
                _article_payload(
                    card["article"], is_read=card["is_read"], is_saved=card["is_saved"]
                )
                for card in cards
            ],
            "actions": {
                "mark_read": "/api/v1/articles/{id}/read/",
                "save": "/api/v1/articles/{id}/saved/",
            },
        }
    )


def _agent_link_signature(article_id: int) -> str:
    return hmac.new(
        settings.AGENT_LINK_SECRET.encode("utf-8"),
        f"save-and-go:{article_id}".encode(),
        "sha256",
    ).hexdigest()


def _agent_link_period_read_signature(scope: str) -> str:
    return hmac.new(
        settings.AGENT_LINK_SECRET.encode("utf-8"),
        f"mark-period-read:{scope}".encode(),
        "sha256",
    ).hexdigest()


def _agent_link_user():
    if not settings.AGENT_LINK_SECRET or not settings.AGENT_LINK_USERNAME:
        return None
    user_model = apps.get_model(settings.AUTH_USER_MODEL)
    return user_model.objects.filter(
        username=settings.AGENT_LINK_USERNAME, is_active=True
    ).first()


def article_save_and_go(request: HttpRequest, article_id: int) -> HttpResponse:
    """Save an article from a signed GET link, then redirect to the article URL."""

    signature = request.GET.get("sig", "")
    if not settings.AGENT_LINK_SECRET or not hmac.compare_digest(
        signature, _agent_link_signature(article_id)
    ):
        return _json_error("Invalid article save link.", status=403, code="forbidden")
    user = _agent_link_user()
    if user is None:
        return _json_error(
            "Agent link saving is not configured.",
            status=503,
            code="not_configured",
        )
    article = get_object_or_404(
        Article.objects.select_related("feed", "feed__category"), id=article_id
    )
    save_article(
        user=user,
        article=article,
        base_url=settings.LINKDING_URL,
        token=settings.LINKDING_TOKEN,
    )
    return redirect(article.url)


def mark_period_read_and_go(request: HttpRequest) -> HttpResponse:
    """Mark a day, week, or month read from a signed GET link."""

    scope = request.GET.get("scope", ReadScope.DAY)
    if scope not in {ReadScope.DAY, ReadScope.WEEK, ReadScope.MONTH}:
        return _json_error(
            "scope must be one of: day, week, month.", status=400, code="bad_request"
        )
    signature = request.GET.get("sig", "")
    if not settings.AGENT_LINK_SECRET or not hmac.compare_digest(
        signature, _agent_link_period_read_signature(scope)
    ):
        return _json_error("Invalid period read link.", status=403, code="forbidden")
    user = _agent_link_user()
    if user is None:
        return _json_error(
            "Agent link marking is not configured.",
            status=503,
            code="not_configured",
        )
    if scope == ReadScope.DAY:
        period = "today"
    elif scope == ReadScope.WEEK:
        period = "week"
    else:
        period = "month"
    start, end = _article_window(period)
    marked_read_at = timezone.now()
    _mark_articles_read(
        user,
        _articles_between(start, end).filter(fetched_at__lte=marked_read_at),
    )
    BulkReadMarker.objects.update_or_create(
        user=user,
        scope=scope,
        feed=None,
        period_start=start,
        period_end=end,
        defaults={"marked_read_at": marked_read_at},
    )
    return redirect("today")


@api_view({"POST", "PATCH"})
def article_read_state(request: HttpRequest, user, article_id: int) -> JsonResponse:
    article = get_object_or_404(Article, id=article_id)
    data = _parse_json(request)
    is_read = bool(data.get("is_read", True))
    ArticleReadState.objects.update_or_create(
        user=user, article=article, defaults={"is_read": is_read}
    )
    return JsonResponse(
        {
            "article": _article_payload(
                article,
                is_read=is_read,
                is_saved=SavedArticle.objects.filter(
                    user=user, article=article
                ).exists(),
            )
        }
    )


@api_view({"POST", "PATCH", "DELETE"})
def article_saved_state(request: HttpRequest, user, article_id: int) -> JsonResponse:
    article = get_object_or_404(
        Article.objects.select_related("feed", "feed__category"), id=article_id
    )
    if request.method == "DELETE":
        SavedArticle.objects.filter(user=user, article=article).delete()
        return JsonResponse(
            {
                "article": _article_payload(
                    article,
                    is_read=_pk(article)
                    in _read_article_ids(user, Article.objects.filter(id=_pk(article))),
                    is_saved=False,
                )
            }
        )
    data = _parse_json(request)
    saved_value = bool(data.get("is_saved", data.get("saved", True)))
    if not saved_value:
        SavedArticle.objects.filter(user=user, article=article).delete()
        return JsonResponse(
            {"article": _article_payload(article, is_read=False, is_saved=False)}
        )
    saved = save_article(
        user=user,
        article=article,
        base_url=settings.LINKDING_URL,
        token=settings.LINKDING_TOKEN,
    )
    update_fields = []
    if "notes" in data:
        saved.notes = str(data["notes"])
        update_fields.append("notes")
    if "interest_score" in data:
        saved.interest_score = data["interest_score"]
        update_fields.append("interest_score")
    if update_fields:
        update_fields.append("updated_at")
        saved.save(update_fields=update_fields)
    return JsonResponse(
        {
            "saved_article": {
                "id": _pk(saved),
                "linkding_saved": saved.linkding_saved,
                "linkding_error": saved.linkding_error,
            },
            "article": _article_payload(
                article,
                is_read=_pk(article)
                in _read_article_ids(user, Article.objects.filter(id=_pk(article))),
                is_saved=True,
            ),
        }
    )


@api_view({"POST"})
def mark_period_read_api(request: HttpRequest, user) -> JsonResponse:
    data = _parse_json(request)
    scope = data.get("scope", ReadScope.DAY)
    if scope not in {ReadScope.DAY, ReadScope.WEEK, ReadScope.MONTH}:
        raise ValueError("scope must be one of: day, week, month.")
    if "period_start" in data and "period_end" in data:
        start = date.fromisoformat(str(data["period_start"]))
        end = date.fromisoformat(str(data["period_end"]))
    else:
        start, end = _article_window(
            {ReadScope.DAY: "today", ReadScope.WEEK: "week", ReadScope.MONTH: "month"}[
                scope
            ]
        )
    marked_read_at = timezone.now()
    _mark_articles_read(
        user,
        _articles_between(start, end).filter(fetched_at__lte=marked_read_at),
    )
    BulkReadMarker.objects.update_or_create(
        user=user,
        scope=scope,
        feed=None,
        period_start=start,
        period_end=end,
        defaults={"marked_read_at": marked_read_at},
    )
    return JsonResponse(
        {
            "marked_read": {
                "scope": scope,
                "period_start": start.isoformat(),
                "period_end": end.isoformat(),
            }
        }
    )


@api_view({"GET", "POST"})
def feed_collection(request: HttpRequest, user) -> JsonResponse:
    if request.method == "GET":
        feeds = Feed.objects.select_related("category").order_by(
            "category__name", "title", "feed_url"
        )
        return JsonResponse({"feeds": [_feed_payload(feed) for feed in feeds]})
    data = _parse_json(request)
    feed_url = str(data.get("feed_url", ""))
    if not feed_url:
        raise ValueError("feed_url is required.")
    title = str(data.get("title") or "")
    metadata = discover_feed_metadata(feed_url) if not title else {}
    category = None
    if data.get("category_id"):
        category = get_object_or_404(Category, id=data["category_id"])
    feed, created = Feed.objects.update_or_create(
        feed_url=feed_url,
        defaults={
            "title": title or metadata.get("title", feed_url),
            "site_url": str(data.get("site_url") or metadata.get("site_url", "")),
            "description": str(
                data.get("description") or metadata.get("description", "")
            ),
            "category": category,
            "is_active": bool(data.get("is_active", True)),
        },
    )
    return JsonResponse(
        {"created": created, "feed": _feed_payload(feed)},
        status=201 if created else 200,
    )


@api_view({"GET", "PATCH", "DELETE"})
def feed_detail_api(request: HttpRequest, user, feed_id: int) -> JsonResponse:
    feed = get_object_or_404(Feed.objects.select_related("category"), id=feed_id)
    if request.method == "GET":
        return JsonResponse({"feed": _feed_payload(feed)})
    if request.method == "DELETE":
        feed.is_active = False
        feed.save(update_fields=["is_active", "updated_at"])
        return JsonResponse({"feed": _feed_payload(feed)})
    data = _parse_json(request)
    for field in ["title", "feed_url", "site_url", "description", "is_active"]:
        if field in data:
            setattr(feed, field, data[field])
    if "category_id" in data:
        feed.category = (
            get_object_or_404(Category, id=data["category_id"])
            if data["category_id"]
            else None
        )
    feed.save()
    return JsonResponse({"feed": _feed_payload(feed)})


@api_view({"POST"})
def mark_feed_read_api(request: HttpRequest, user, feed_id: int) -> JsonResponse:
    feed = get_object_or_404(Feed, id=feed_id)
    marked_read_at = timezone.now()
    _mark_articles_read(
        user,
        Article.objects.filter(feed=feed, fetched_at__lte=marked_read_at),
    )
    BulkReadMarker.objects.update_or_create(
        user=user,
        scope=ReadScope.FEED,
        feed=feed,
        period_start=None,
        period_end=None,
        defaults={"marked_read_at": marked_read_at},
    )
    return JsonResponse(
        {"marked_read": {"scope": ReadScope.FEED, "feed": _feed_payload(feed)}}
    )


@api_view({"GET", "POST"})
def category_collection(request: HttpRequest, user) -> JsonResponse:
    if request.method == "GET":
        return JsonResponse(
            {
                "categories": [
                    _category_payload(category) for category in Category.objects.all()
                ]
            }
        )
    data = _parse_json(request)
    name = str(data.get("name", ""))
    slug = str(data.get("slug", ""))
    if not name or not slug:
        raise ValueError("name and slug are required.")
    try:
        category, created = Category.objects.get_or_create(
            slug=slug, defaults={"name": name}
        )
    except IntegrityError as exc:
        raise ValueError("Category name or slug already exists.") from exc
    return JsonResponse(
        {"created": created, "category": _category_payload(category)},
        status=201 if created else 200,
    )


@api_view({"GET", "PATCH"})
def preferences_api(request: HttpRequest, user) -> JsonResponse:
    preferences = _preferences(user)
    if request.method == "PATCH":
        data = _parse_json(request)
        if "theme" in data:
            theme = data["theme"]
            valid_themes = {choice[0] for choice in UserPreference.Theme.choices}
            if theme not in valid_themes:
                raise ValueError(
                    f"theme must be one of: {', '.join(sorted(valid_themes))}."
                )
            preferences.theme = theme
        if "compact" in data:
            preferences.compact = bool(data["compact"])
        if "focus_mode" in data:
            preferences.focus_mode = bool(data["focus_mode"])
        preferences.save()
    return JsonResponse(
        {
            "preferences": {
                "theme": preferences.theme,
                "compact": preferences.compact,
                "focus_mode": preferences.focus_mode,
            }
        }
    )


@api_view({"POST"})
def refresh_feeds_api(request: HttpRequest, user) -> JsonResponse:
    results = refresh_active_feeds()
    return JsonResponse(
        {
            "checked": len(results),
            "feeds_with_new_articles": sum(
                1 for result in results if result.created > 0
            ),
            "created": sum(result.created for result in results),
            "updated": sum(result.updated for result in results),
            "feeds": [
                {
                    "id": _pk(result.feed),
                    "title": result.feed.title,
                    "created": result.created,
                    "updated": result.updated,
                }
                for result in results
            ],
        }
    )
