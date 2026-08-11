from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.http import JsonResponse

_MISSING = object()
_MAX_DATABASE_ID = 2**63 - 1
_HTTP_URL_VALIDATOR = URLValidator(schemes=["http", "https"])


@dataclass
class ApiProblem(Exception):
    message: str
    status: int = 400
    code: str = "bad_request"
    fields: Mapping[str, Sequence[str]] | None = None

    def response(self) -> JsonResponse:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.fields:
            error["fields"] = {
                name: list(messages) for name, messages in self.fields.items()
            }
        return JsonResponse({"error": error}, status=self.status)


def bad_request(message: str) -> ApiProblem:
    return ApiProblem(message)


def semantic_error(message: str, *, field: str | None = None) -> ApiProblem:
    fields = {field: [message]} if field else None
    return ApiProblem(message, status=422, code="validation_error", fields=fields)


def conflict(message: str) -> ApiProblem:
    return ApiProblem(message, status=409, code="conflict")


def not_found(resource: str) -> ApiProblem:
    return ApiProblem(f"{resource} not found.", status=404, code="not_found")


def validation_problem(exc: ValidationError) -> ApiProblem:
    if hasattr(exc, "message_dict"):
        fields = {
            name: [str(message) for message in messages]
            for name, messages in exc.message_dict.items()
        }
    else:
        fields = {"__all__": [str(message) for message in exc.messages]}
    return ApiProblem(
        "Request fields failed validation.",
        status=422,
        code="validation_error",
        fields=fields,
    )


def reject_unknown(data: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise bad_request(f"Unknown field(s): {', '.join(unknown)}.")


def boolean(
    data: Mapping[str, Any], name: str, *, default: bool | object = _MISSING
) -> bool:
    value = data.get(name, _MISSING)
    if value is _MISSING:
        if default is _MISSING:
            raise bad_request(f"{name} is required.")
        return bool(default)
    if not isinstance(value, bool):
        raise bad_request(f"{name} must be a JSON boolean.")
    return value


def string(
    data: Mapping[str, Any],
    name: str,
    *,
    required: bool = False,
    default: str | object = _MISSING,
    allow_blank: bool = True,
    nullable: bool = False,
) -> str | None:
    value = data.get(name, _MISSING)
    if value is _MISSING:
        if required:
            raise bad_request(f"{name} is required.")
        if default is _MISSING:
            return None
        return str(default)
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise bad_request(
            f"{name} must be a JSON string{', or null' if nullable else ''}."
        )
    if not allow_blank and not value.strip():
        raise semantic_error(f"{name} must not be blank.", field=name)
    return value


def database_id(value: int, name: str) -> int:
    if value <= 0 or value > _MAX_DATABASE_ID:
        raise semantic_error(f"{name} must be a positive 64-bit integer.", field=name)
    return value


def integer(
    data: Mapping[str, Any], name: str, *, nullable: bool = False
) -> int | None:
    if name not in data:
        return None
    value = data[name]
    if value is None and nullable:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise bad_request(
            f"{name} must be a JSON integer{', or null' if nullable else ''}."
        )
    return database_id(value, name)


def query_boolean(
    query: Mapping[str, str], name: str, *, default: bool = False
) -> bool:
    value = query.get(name)
    if value is None:
        return default
    if value == "true":
        return True
    if value == "false":
        return False
    raise bad_request(f"{name} must be either true or false.")


def iso_date(value: Any, name: str) -> date:
    if not isinstance(value, str):
        raise bad_request(f"{name} must be an ISO date string (YYYY-MM-DD).")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise semantic_error(
            f"{name} must be a valid ISO date (YYYY-MM-DD).", field=name
        ) from exc
    if value != parsed.isoformat():
        raise semantic_error(f"{name} must use ISO date format YYYY-MM-DD.", field=name)
    return parsed


def ordered_dates(start: date, end: date) -> None:
    if start > end:
        raise semantic_error(
            "period_start/start must not be after period_end/end.", field="period_end"
        )


def http_url(value: str, name: str) -> str:
    try:
        _HTTP_URL_VALIDATOR(value)
        parsed = urlsplit(value)
        if parsed.username is not None or parsed.password is not None:
            raise ValidationError("Credentials are not permitted in URLs.")
    except (ValueError, ValidationError) as exc:
        raise semantic_error(
            f"{name} must be a credential-free HTTP(S) URL.", field=name
        ) from exc
    return value


def finite_score(
    data: Mapping[str, Any], name: str = "interest_score"
) -> float | None | object:
    if name not in data:
        return _MISSING
    value = data[name]
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise bad_request(f"{name} must be a JSON number or null.")
    try:
        score = float(value)
    except (OverflowError, ValueError) as exc:
        raise semantic_error(
            f"{name} must be finite and between 0 and 5.", field=name
        ) from exc
    if not math.isfinite(score) or not 0 <= score <= 5:
        raise semantic_error(f"{name} must be finite and between 0 and 5.", field=name)
    return score


def is_missing(value: object) -> bool:
    return value is _MISSING
