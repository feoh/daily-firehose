from __future__ import annotations

import re
import unicodedata
from urllib.parse import unquote, urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme

_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_ABSOLUTE_HTTP_URL = URLValidator(schemes=("http", "https"))


def _decoded_url_variants(value: str):
    """Yield a URL and every distinct recursive percent-decoding of it."""

    current = value
    while True:
        yield current
        decoded = unquote(current)
        if decoded == current:
            return
        current = decoded


def _has_invalid_original_syntax(value: str) -> bool:
    return bool(_INVALID_PERCENT_ESCAPE.search(value)) or _has_dangerous_syntax(value)


def _has_dangerous_syntax(value: str) -> bool:
    return "\\" in value or any(
        unicodedata.category(character).startswith("C") for character in value
    )


def _is_scheme_relative(value: str) -> bool:
    return value.startswith("//")


def safe_redirect_target(
    request: HttpRequest, target: str | None, *, fallback: str
) -> str:
    """Return a same-origin browser target or a known local fallback.

    Django's host/scheme utility is the primary policy. Recursive decoding closes
    browser/proxy interpretation gaps for encoded separators, schemes, credentials,
    backslashes, and control characters.
    """

    if not target or target != target.strip() or _has_invalid_original_syntax(target):
        return fallback
    allowed_hosts = {request.get_host()}
    for variant in _decoded_url_variants(target):
        if _has_dangerous_syntax(variant) or _is_scheme_relative(variant):
            return fallback
        if not url_has_allowed_host_and_scheme(
            variant,
            allowed_hosts=allowed_hosts,
            require_https=request.is_secure(),
        ):
            return fallback
        try:
            parsed = urlsplit(variant)
            port = parsed.port
        except ValueError:
            return fallback
        if port is not None and not 0 < port <= 65535:
            return fallback
        if parsed.username is not None or parsed.password is not None:
            return fallback
        if parsed.scheme and parsed.scheme != request.scheme:
            return fallback
    return target


def safe_article_navigation_url(target: str | None, *, fallback: str) -> str:
    """Validate the intentional outbound destination of a signed save-and-go link."""

    if not target or target != target.strip() or _has_invalid_original_syntax(target):
        return fallback
    try:
        _ABSOLUTE_HTTP_URL(target)
    except ValidationError:
        return fallback
    for variant in _decoded_url_variants(target):
        if _has_dangerous_syntax(variant) or _is_scheme_relative(variant):
            return fallback
        try:
            parsed = urlsplit(variant)
            port = parsed.port
        except ValueError:
            return fallback
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or (port is not None and not 0 < port <= 65535)
        ):
            return fallback
    return target
