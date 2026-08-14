from __future__ import annotations

import base64
import hmac
import json
import logging
from collections.abc import Callable
from datetime import date
from functools import wraps
from typing import Any, Concatenate, ParamSpec, cast
from urllib.parse import unquote_plus

from django.apps import apps
from django.conf import settings
from django.core.exceptions import (
    RequestDataTooBig,
    TooManyFieldsSent,
    ValidationError,
)
from django.db import IntegrityError, transaction
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from daily_firehose.redirects import safe_article_navigation_url

from .api_validation import (
    ApiProblem,
    bad_request,
    boolean,
    conflict,
    database_id,
    finite_score,
    http_url,
    integer,
    is_missing,
    iso_date,
    not_found,
    ordered_dates,
    query_boolean,
    reject_unknown,
    semantic_error,
    string,
    validation_problem,
)
from .feed_fetch import FeedFetchError
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
    ArticleSaveNotAllowed,
    article_save_capability,
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

logger = logging.getLogger("daily_firehose.webhook")


def _json_error(
    message: str, status: int = 400, *, code: str = "bad_request"
) -> JsonResponse:
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-standard JSON constant {value!r} is not permitted.")


def _is_json_media_type(request: HttpRequest) -> bool:
    media_type = (request.content_type or "").lower()
    return media_type == "application/json" or (
        media_type.startswith("application/") and media_type.endswith("+json")
    )


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for name, field_value in pairs:
        if name in value:
            raise ValueError(f"Duplicate JSON field {name!r} is not permitted.")
        value[name] = field_value
    return value


def _request_body(request: HttpRequest) -> bytes:
    try:
        return request.body
    except RequestDataTooBig as exc:
        raise bad_request("Request body exceeds the configured size limit.") from exc


def _parse_json(request: HttpRequest) -> dict[str, Any]:
    body = _request_body(request)
    if not body:
        return {}
    if not _is_json_media_type(request):
        raise bad_request(
            "Content-Type must be application/json or application/*+json."
        )
    try:
        value = json.loads(
            body.decode("utf-8"),
            parse_constant=_reject_json_constant,
            object_pairs_hook=_json_object,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        OverflowError,
        RecursionError,
        ValueError,
    ) as exc:
        raise bad_request("Request body must be valid UTF-8 JSON.") from exc
    if not isinstance(value, dict):
        raise bad_request("Request body must be a JSON object.")
    return value


def _validate_empty_body(request: HttpRequest) -> None:
    body = _request_body(request)
    if not body:
        return
    if (request.content_type or "").lower() == "multipart/form-data":
        boundary = (request.content_params or {}).get("boundary")
        if boundary:
            closing_boundary = f"--{boundary}--".encode()
            if body.rstrip(b"\r\n") == closing_boundary:
                return
    raise bad_request("This endpoint does not accept a request body.")


def _raw_query_value(request: HttpRequest, name: str) -> str:
    """Read one auth field without constructing a potentially oversized QueryDict."""

    for field in request.META.get("QUERY_STRING", "").split("&"):
        raw_name, separator, raw_value = field.partition("=")
        if unquote_plus(raw_name) == name:
            return unquote_plus(raw_value) if separator else ""
    return ""


def _reject_query_fields(request: HttpRequest, allowed: set[str]) -> None:
    try:
        query = request.GET
        unknown = sorted(set(query) - allowed)
        repeated = sorted(name for name in query if len(query.getlist(name)) > 1)
    except TooManyFieldsSent as exc:
        raise bad_request("Too many query parameters were provided.") from exc
    if unknown:
        raise bad_request(f"Unknown query parameter(s): {', '.join(unknown)}.")
    if repeated:
        raise bad_request(
            f"Query parameter(s) must appear once: {', '.join(repeated)}."
        )


def _get_api_object(query: Any, resource: str, **lookup: Any) -> Any:
    if isinstance(lookup.get("id"), int):
        lookup["id"] = database_id(lookup["id"], f"{resource.lower()}_id")
    try:
        return query.get(**lookup)
    except query.model.DoesNotExist as exc:
        raise not_found(resource) from exc


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
                response = _json_error(
                    "Provide a valid API token in the Authorization header.",
                    status=401,
                    code="unauthorized",
                )
                response["WWW-Authenticate"] = "Bearer"
                return response
            request.user = user
            try:
                return view(request, user, *args, **kwargs)
            except ApiProblem as exc:
                return exc.response()
            except ArticleSaveNotAllowed as exc:
                return _json_error(exc.message, status=422, code=exc.code)
            except Http404:
                return _json_error("Resource not found.", status=404, code="not_found")
            except ValidationError as exc:
                return validation_problem(exc).response()
            except BulkReadMarker.MultipleObjectsReturned:
                return conflict(
                    "Stored read-marker state conflicts with this request."
                ).response()
            except IntegrityError:
                return conflict(
                    "The requested write conflicts with existing data."
                ).response()
            except FeedFetchError as exc:
                return _json_error(str(exc), code=exc.code)

        return wrapped

    return decorator


def _pk(model: Any) -> int:
    return cast(int, model.id)


def _postmark_basic_credentials_match(request: HttpRequest) -> bool:
    username = settings.POSTMARK_WEBHOOK_USERNAME
    password = settings.POSTMARK_WEBHOOK_PASSWORD
    if not username or not password:
        return False
    scheme, _, encoded = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return False
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    supplied_username, separator, supplied_password = decoded.partition(":")
    if not separator:
        return False
    # Both halves are always compared so a timing difference cannot reveal which
    # one was wrong.
    username_matches = hmac.compare_digest(supplied_username, username)
    password_matches = hmac.compare_digest(supplied_password, password)
    return username_matches and password_matches


@csrf_exempt
def postmark_inbound_basic(request: HttpRequest) -> JsonResponse:
    """Accept inbound mail authenticated by HTTP Basic credentials.

    This route carries no secret in its path. It exists so the Postmark
    configuration can be moved off the legacy secret-in-URL route below without
    an interruption in delivery.
    """

    if request.method != "POST":
        return _json_error("Method not allowed.", status=405, code="method_not_allowed")
    if not _postmark_basic_credentials_match(request):
        response = _json_error(
            "Invalid inbound email credentials.", status=401, code="unauthorized"
        )
        response["WWW-Authenticate"] = 'Basic realm="postmark-inbound"'
        return response
    return _import_postmark_delivery(request, mechanism="basic")


@csrf_exempt
def postmark_inbound(request: HttpRequest, secret: str) -> JsonResponse:
    """Legacy route whose path segment is the shared secret.

    Retained only for the rotation window; prefer ``postmark_inbound_basic``.
    """

    if request.method != "POST":
        return _json_error("Method not allowed.", status=405, code="method_not_allowed")
    configured_secret = settings.POSTMARK_INBOUND_SECRET
    if not configured_secret or not hmac.compare_digest(secret, configured_secret):
        return _json_error(
            "Invalid inbound email secret.", status=403, code="forbidden"
        )
    return _import_postmark_delivery(request, mechanism="path_secret")


def _import_postmark_delivery(
    request: HttpRequest, *, mechanism: str
) -> JsonResponse:
    # Recording the mechanism is what lets an operator confirm the legacy route
    # is unused before it is removed.
    logger.info("postmark_delivery_authenticated", extra={"mechanism": mechanism})
    try:
        _reject_query_fields(request, set())
        body = _request_body(request)
        if len(body) > settings.POSTMARK_MAX_BODY_BYTES:
            raise ApiProblem(
                "Inbound email exceeds the configured size limit.",
                status=413,
                code="payload_too_large",
            )
        payload = _parse_json(request)
        result = import_postmark_newsletter(
            payload=payload, base_url=request.build_absolute_uri("/")
        )
    except ApiProblem as exc:
        return exc.response()
    except ValidationError as exc:
        return validation_problem(exc).response()
    except IntegrityError:
        return conflict("Inbound newsletter conflicts with existing data.").response()
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
    capability = article_save_capability(article)
    article_id = _pk(article)
    actions = {"mark_read": f"/api/v1/articles/{article_id}/read/"}
    if capability.allowed:
        actions["save"] = f"/api/v1/articles/{article_id}/saved/"
    return {
        "id": article_id,
        "title": article.title,
        "url": article.url,
        "guid": article.guid,
        "author": article.author,
        "summary": article.summary,
        "published_at": article.published_at.isoformat(),
        "fetched_at": article.fetched_at.isoformat(),
        "feed": _feed_payload(article.feed),
        "is_read": is_read,
        "is_saved": is_saved,
        "capabilities": {
            "mark_read": {"allowed": True, "code": None, "message": None},
            "save": {
                "allowed": capability.allowed,
                "code": capability.code,
                "message": capability.message,
            },
        },
        "actions": actions,
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
    _validate_empty_body(request)
    _reject_query_fields(
        request,
        {"period", "start", "end", "feed_id", "include_read", "include_saved"},
    )
    period = request.GET.get("period", "today")
    if period not in {"today", "week", "month"}:
        raise semantic_error(
            "period must be one of: today, week, month.", field="period"
        )
    start_value = request.GET.get("start")
    end_value = request.GET.get("end")
    if start_value is not None or end_value is not None:
        if start_value is None or end_value is None:
            raise bad_request("Provide both start and end dates, or neither.")
        start = iso_date(start_value, "start")
        end = iso_date(end_value, "end")
        ordered_dates(start, end)
    else:
        start, end = _article_window(period)
    feed_id_value = request.GET.get("feed_id")
    feed = None
    if feed_id_value is not None:
        try:
            feed_id = int(feed_id_value)
        except ValueError as exc:
            raise bad_request("feed_id must be a positive integer.") from exc
        feed_id = database_id(feed_id, "feed_id")
        feed = _get_api_object(Feed.objects.all(), "Feed", id=feed_id)
    include_read = query_boolean(request.GET, "include_read")
    include_saved = query_boolean(request.GET, "include_saved")
    articles = _articles_between(start, end, feed).select_related(
        "feed", "feed__category", "newsletter_issue"
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
    _validate_empty_body(request)
    _reject_query_fields(request, set())
    current = timezone.localdate()
    articles = _articles_between(current, current).select_related(
        "feed", "feed__category", "newsletter_issue"
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

    if request.method != "GET":
        return _json_error("Method not allowed.", status=405, code="method_not_allowed")
    signature = _raw_query_value(request, "sig")
    if not settings.AGENT_LINK_SECRET or not hmac.compare_digest(
        signature, _agent_link_signature(article_id)
    ):
        return _json_error("Invalid article save link.", status=403, code="forbidden")
    try:
        _reject_query_fields(request, {"sig"})
        _validate_empty_body(request)
    except ApiProblem as exc:
        return exc.response()
    user = _agent_link_user()
    if user is None:
        return _json_error(
            "Agent link saving is not configured.",
            status=503,
            code="not_configured",
        )
    try:
        article = _get_api_object(
            Article.objects.select_related(
                "feed", "feed__category", "newsletter_issue"
            ),
            "Article",
            id=article_id,
        )
        save_article(
            user=user,
            article=article,
            base_url=settings.LINKDING_URL,
            token=settings.LINKDING_TOKEN,
        )
    except ApiProblem as exc:
        return exc.response()
    except ArticleSaveNotAllowed as exc:
        return _json_error(exc.message, status=422, code=exc.code)
    except ValidationError as exc:
        return validation_problem(exc).response()
    except IntegrityError:
        return conflict("The requested write conflicts with existing data.").response()
    return redirect(safe_article_navigation_url(article.url, fallback=reverse("today")))


def mark_period_read_and_go(request: HttpRequest) -> HttpResponse:
    """Mark a day, week, or month read from a signed GET link."""

    if request.method != "GET":
        return _json_error("Method not allowed.", status=405, code="method_not_allowed")
    scope = _raw_query_value(request, "scope") or ReadScope.DAY
    signature = _raw_query_value(request, "sig")
    if not settings.AGENT_LINK_SECRET or not hmac.compare_digest(
        signature, _agent_link_period_read_signature(scope)
    ):
        return _json_error("Invalid period read link.", status=403, code="forbidden")
    try:
        _reject_query_fields(request, {"scope", "sig"})
        _validate_empty_body(request)
    except ApiProblem as exc:
        return exc.response()
    if scope not in {ReadScope.DAY, ReadScope.WEEK, ReadScope.MONTH}:
        return semantic_error(
            "scope must be one of: day, week, month.", field="scope"
        ).response()
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
    try:
        with transaction.atomic():
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
    except BulkReadMarker.MultipleObjectsReturned:
        return conflict(
            "Stored read-marker state conflicts with this request."
        ).response()
    except IntegrityError:
        return conflict("The requested write conflicts with existing data.").response()
    return redirect("today")


@api_view({"POST", "PATCH"})
def article_read_state(request: HttpRequest, user, article_id: int) -> JsonResponse:
    _reject_query_fields(request, set())
    article = _get_api_object(
        Article.objects.select_related("feed", "feed__category", "newsletter_issue"),
        "Article",
        id=article_id,
    )
    data = _parse_json(request)
    reject_unknown(data, {"is_read"})
    is_read = boolean(data, "is_read", default=True)
    ArticleReadState(user=user, article=article, is_read=is_read).full_clean(
        validate_unique=False, validate_constraints=False
    )
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
    _reject_query_fields(request, set())
    article = _get_api_object(
        Article.objects.select_related("feed", "feed__category", "newsletter_issue"),
        "Article",
        id=article_id,
    )
    if request.method == "DELETE":
        _validate_empty_body(request)
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
    reject_unknown(data, {"is_saved", "saved", "notes", "interest_score"})
    if "is_saved" in data and "saved" in data:
        raise bad_request("Provide is_saved or saved, not both.")
    saved_value = (
        boolean(data, "is_saved")
        if "is_saved" in data
        else boolean(data, "saved", default=True)
    )
    notes = string(data, "notes") if "notes" in data else None
    score = finite_score(data)
    if not saved_value:
        if "notes" in data or "interest_score" in data:
            raise bad_request("notes and interest_score cannot be set while unsaving.")
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
    validation_saved = SavedArticle.objects.filter(
        user=user, article=article
    ).first() or SavedArticle(
        user=user,
        article=article,
        url=article.url,
        title=article.title,
        feed=article.feed,
        category=article.feed.category,
    )
    if notes is not None:
        validation_saved.notes = notes
    if not is_missing(score):
        validation_saved.interest_score = cast(float | None, score)
    validation_saved.full_clean(validate_unique=False, validate_constraints=False)
    saved = save_article(
        user=user,
        article=article,
        base_url=settings.LINKDING_URL,
        token=settings.LINKDING_TOKEN,
    )
    update_fields = []
    if notes is not None:
        saved.notes = notes
        update_fields.append("notes")
    if not is_missing(score):
        saved.interest_score = cast(float | None, score)
        update_fields.append("interest_score")
    if update_fields:
        saved.full_clean()
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
    _reject_query_fields(request, set())
    data = _parse_json(request)
    reject_unknown(data, {"scope", "period_start", "period_end"})
    scope = cast(str, string(data, "scope", default=ReadScope.DAY))
    if scope not in {ReadScope.DAY, ReadScope.WEEK, ReadScope.MONTH}:
        raise semantic_error("scope must be one of: day, week, month.", field="scope")
    has_start = "period_start" in data
    has_end = "period_end" in data
    if has_start != has_end:
        raise bad_request("Provide both period_start and period_end, or neither.")
    if has_start:
        start = iso_date(data["period_start"], "period_start")
        end = iso_date(data["period_end"], "period_end")
        ordered_dates(start, end)
    else:
        start, end = _article_window(
            {"day": "today", "week": "week", "month": "month"}[scope]
        )
    marked_read_at = timezone.now()
    BulkReadMarker(
        user=user,
        scope=scope,
        feed=None,
        period_start=start,
        period_end=end,
    ).full_clean(validate_unique=False, validate_constraints=False)
    with transaction.atomic():
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
    _reject_query_fields(request, set())
    if request.method == "GET":
        _validate_empty_body(request)
        feeds = Feed.objects.select_related("category").order_by(
            "category__name", "title", "feed_url"
        )
        return JsonResponse({"feeds": [_feed_payload(feed) for feed in feeds]})
    data = _parse_json(request)
    reject_unknown(
        data,
        {"feed_url", "title", "site_url", "description", "category_id", "is_active"},
    )
    feed_url_value = string(data, "feed_url", required=True, allow_blank=False)
    feed_url = http_url(cast(str, feed_url_value), "feed_url")
    title = string(data, "title", default="")
    site_url = string(data, "site_url", default="")
    description = string(data, "description", default="")
    category_id = integer(data, "category_id", nullable=True)
    is_active = boolean(data, "is_active", default=True)
    category = (
        _get_api_object(Category.objects.all(), "Category", id=category_id)
        if category_id is not None
        else None
    )
    metadata = discover_feed_metadata(feed_url) if not title else {}
    feed = Feed.objects.filter(feed_url=feed_url).first() or Feed(feed_url=feed_url)
    created = feed.pk is None
    feed.title = title or str(metadata.get("title") or feed_url)
    feed.site_url = site_url or str(metadata.get("site_url") or "")
    feed.description = description or str(metadata.get("description") or "")
    feed.category = category
    feed.is_active = is_active
    if feed.site_url:
        http_url(feed.site_url, "site_url")
    feed.full_clean(validate_unique=False)
    feed.save()
    return JsonResponse(
        {"created": created, "feed": _feed_payload(feed)},
        status=201 if created else 200,
    )


@api_view({"GET", "PATCH", "DELETE"})
def feed_detail_api(request: HttpRequest, user, feed_id: int) -> JsonResponse:
    _reject_query_fields(request, set())
    feed = _get_api_object(Feed.objects.select_related("category"), "Feed", id=feed_id)
    if request.method == "GET":
        _validate_empty_body(request)
        return JsonResponse({"feed": _feed_payload(feed)})
    if request.method == "DELETE":
        _validate_empty_body(request)
        feed.is_active = False
        feed.save(update_fields=["is_active", "updated_at"])
        return JsonResponse({"feed": _feed_payload(feed)})
    data = _parse_json(request)
    reject_unknown(
        data,
        {"title", "feed_url", "site_url", "description", "category_id", "is_active"},
    )
    for field in ("title", "feed_url", "site_url", "description"):
        if field in data:
            value = cast(str, string(data, field))
            if field in {"feed_url", "site_url"} and value:
                http_url(value, field)
            setattr(feed, field, value)
    if "is_active" in data:
        feed.is_active = boolean(data, "is_active")
    if "category_id" in data:
        category_id = integer(data, "category_id", nullable=True)
        feed.category = (
            _get_api_object(Category.objects.all(), "Category", id=category_id)
            if category_id is not None
            else None
        )
    if Feed.objects.exclude(pk=feed.pk).filter(feed_url=feed.feed_url).exists():
        raise conflict("A feed with this feed_url already exists.")
    feed.full_clean(validate_unique=False)
    feed.save()
    return JsonResponse({"feed": _feed_payload(feed)})


@api_view({"POST"})
def mark_feed_read_api(request: HttpRequest, user, feed_id: int) -> JsonResponse:
    _reject_query_fields(request, set())
    _validate_empty_body(request)
    feed = _get_api_object(Feed.objects.all(), "Feed", id=feed_id)
    marked_read_at = timezone.now()
    BulkReadMarker(
        user=user,
        scope=ReadScope.FEED,
        feed=feed,
        period_start=None,
        period_end=None,
    ).full_clean(validate_unique=False, validate_constraints=False)
    with transaction.atomic():
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
    _reject_query_fields(request, set())
    if request.method == "GET":
        _validate_empty_body(request)
        return JsonResponse(
            {
                "categories": [
                    _category_payload(category) for category in Category.objects.all()
                ]
            }
        )
    data = _parse_json(request)
    reject_unknown(data, {"name", "slug"})
    name = cast(str, string(data, "name", required=True, allow_blank=False))
    slug = cast(str, string(data, "slug", required=True, allow_blank=False))
    existing = Category.objects.filter(slug=slug).first()
    if existing is not None:
        if existing.name != name:
            raise conflict("Category name or slug already exists.")
        return JsonResponse({"created": False, "category": _category_payload(existing)})
    if Category.objects.filter(name=name).exists():
        raise conflict("Category name or slug already exists.")
    category = Category(name=name, slug=slug)
    category.full_clean(validate_unique=False)
    category.save()
    return JsonResponse(
        {"created": True, "category": _category_payload(category)}, status=201
    )


@api_view({"GET", "PATCH"})
def preferences_api(request: HttpRequest, user) -> JsonResponse:
    _reject_query_fields(request, set())
    if request.method == "GET":
        _validate_empty_body(request)
    data: dict[str, Any] = {}
    theme: str | None = None
    compact: bool | None = None
    focus_mode: bool | None = None
    if request.method == "PATCH":
        data = _parse_json(request)
        reject_unknown(data, {"theme", "compact", "focus_mode"})
        if "theme" in data:
            theme = cast(str, string(data, "theme"))
            valid_themes = {choice[0] for choice in UserPreference.Theme.choices}
            if theme not in valid_themes:
                raise semantic_error(
                    f"theme must be one of: {', '.join(sorted(valid_themes))}.",
                    field="theme",
                )
        if "compact" in data:
            compact = boolean(data, "compact")
        if "focus_mode" in data:
            focus_mode = boolean(data, "focus_mode")
    preferences = _preferences(user)
    if request.method == "PATCH":
        if theme is not None:
            preferences.theme = theme
        if compact is not None:
            preferences.compact = compact
        if focus_mode is not None:
            preferences.focus_mode = focus_mode
        preferences.full_clean()
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
    _reject_query_fields(request, set())
    _validate_empty_body(request)
    results = refresh_active_feeds()
    attempted = [result for result in results if result.status != "skipped"]
    return JsonResponse(
        {
            "checked": len(results),
            "attempted": len(attempted),
            "succeeded": sum(result.status == "succeeded" for result in results),
            "failed": sum(result.status == "failed" for result in results),
            "skipped": sum(result.status == "skipped" for result in results),
            "superseded": sum(result.status == "superseded" for result in results),
            "feeds_with_new_articles": sum(
                1 for result in results if result.success and result.created > 0
            ),
            "created": sum(result.created for result in results),
            "updated": sum(result.updated for result in results),
            "feeds": [
                {
                    "id": _pk(result.feed),
                    "title": result.feed.title,
                    "status": result.status,
                    "created": result.created,
                    "updated": result.updated,
                    "duration_seconds": result.duration_seconds,
                    "error": (
                        {
                            "code": result.error_code,
                            "message": result.error_message,
                        }
                        if not result.success
                        else None
                    ),
                    "next_retry_at": (
                        result.next_retry_at.isoformat()
                        if result.next_retry_at is not None
                        else None
                    ),
                }
                for result in results
            ],
        }
    )
