"""Request-scoped correlation and access logging."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from .observability import (
    CORRELATION_ID_HEADER,
    bind_correlation_id,
    new_correlation_id,
    reset_correlation_id,
    sanitize_correlation_id,
)

logger = logging.getLogger("daily_firehose.request")


def _route(request: HttpRequest) -> str:
    """Return the matched view name.

    The raw path is never logged: the Postmark webhook carries its shared secret
    as a path segment.
    """

    match = getattr(request, "resolver_match", None)
    if match is None:
        return "unmatched"
    return match.view_name or "unmatched"


class CorrelationIdMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        correlation_id = (
            sanitize_correlation_id(request.headers.get(CORRELATION_ID_HEADER, ""))
            or new_correlation_id()
        )
        token = bind_correlation_id(correlation_id)
        started = time.monotonic()
        try:
            response = self.get_response(request)
            response[CORRELATION_ID_HEADER] = correlation_id
            logger.info(
                "http_request_completed",
                extra={
                    "method": request.method,
                    "route": _route(request),
                    "status_code": response.status_code,
                    "duration_seconds": round(max(0.0, time.monotonic() - started), 6),
                    "authenticated": bool(
                        getattr(getattr(request, "user", None), "is_authenticated", False)
                    ),
                },
            )
            return response
        finally:
            reset_correlation_id(token)
