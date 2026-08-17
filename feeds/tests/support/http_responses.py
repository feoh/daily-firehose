from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import requests

from feeds.models import Article

from .builders import fixture_json


def configure_json_response(
    mock_request: Mock,
    *,
    payload: dict[str, Any],
    status_code: int = 200,
) -> Mock:
    mock_request.side_effect = None
    response = mock_request.return_value
    response.status_code = status_code
    response.json.return_value = payload
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
    else:
        response.raise_for_status.side_effect = None
        response.raise_for_status.return_value = None
    return response


def configure_request_timeout(
    mock_request: Mock, message: str = "request timed out"
) -> requests.Timeout:
    error = requests.Timeout(message)
    mock_request.side_effect = error
    return error


def configure_linkding_lookup(
    mock_get: Mock,
    *,
    bookmark: dict[str, Any] | None,
    status_code: int = 200,
) -> Mock:
    """Configure the ``/api/bookmarks/check/`` reconciliation lookup.

    The envelope is the one a live Linkding returned on 2026-08-17: a `bookmark`
    that is the object or ``null``, alongside `metadata` and `auto_tags` the
    delivery code must ignore.
    """

    payload = fixture_json("linkding-check.json")
    payload["bookmark"] = bookmark
    return configure_json_response(mock_get, payload=payload, status_code=status_code)


def configure_linkding_response(
    mock_post: Mock,
    *,
    article: Article,
    status_code: int = 201,
    **overrides: Any,
) -> dict[str, Any]:
    payload = fixture_json("linkding-bookmark.json")
    payload.update({"url": article.url, "title": article.title})
    payload.update(overrides)
    configure_json_response(
        mock_post,
        payload=payload,
        status_code=status_code,
    )
    return payload
