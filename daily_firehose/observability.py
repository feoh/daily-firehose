"""Correlation identifiers and secret-safe structured logging."""

from __future__ import annotations

import json
import logging
import re
import uuid
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any

CORRELATION_ID_HEADER = "X-Correlation-ID"
REDACTED = "[redacted]"
MAX_CORRELATION_ID_LENGTH = 64

_CORRELATION_ID: ContextVar[str] = ContextVar("correlation_id", default="")
_SAFE_CORRELATION_ID = re.compile(r"[A-Za-z0-9._-]+")

# Any field whose name matches is dropped before serialization, so a caller
# cannot leak a credential by passing it as structured log context.
_SENSITIVE_FIELD = re.compile(
    r"secret|token|password|passwd|authorization|cookie|api[_-]?key|credential",
    re.IGNORECASE,
)

# Values shorter than this are common words rather than credentials; scrubbing
# them would corrupt unrelated text without protecting anything.
_MIN_REDACTABLE_SECRET_LENGTH = 8

_RESERVED_RECORD_FIELDS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def sanitize_correlation_id(value: str) -> str:
    """Return a caller-supplied correlation ID reduced to safe log content."""

    match = _SAFE_CORRELATION_ID.match(value.strip())
    if match is None:
        return ""
    return match.group(0)[:MAX_CORRELATION_ID_LENGTH]


def get_correlation_id() -> str:
    return _CORRELATION_ID.get()


def bind_correlation_id(value: str) -> Token[str]:
    return _CORRELATION_ID.set(value)


def reset_correlation_id(token: Token[str]) -> None:
    _CORRELATION_ID.reset(token)


def configured_secrets() -> tuple[str, ...]:
    """Return the deployment's secret values, longest first for safe scrubbing."""

    from django.conf import settings

    candidates = [
        getattr(settings, name, "")
        for name in (
            "SECRET_KEY",
            "LINKDING_TOKEN",
            "AGENT_LINK_SECRET",
            "POSTMARK_INBOUND_SECRET",
        )
    ]
    databases = getattr(settings, "DATABASES", {}) or {}
    for database in databases.values():
        candidates.append(str(database.get("PASSWORD", "")))
    secrets = {
        candidate
        for candidate in candidates
        if isinstance(candidate, str)
        and len(candidate) >= _MIN_REDACTABLE_SECRET_LENGTH
    }
    return tuple(sorted(secrets, key=len, reverse=True))


def redact(text: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    return text


class CorrelationIdFilter(logging.Filter):
    """Attach the active correlation ID to every record that lacks one.

    Binding it on the record rather than at format time keeps the ID with the
    record for any handler and for tests that inspect records directly.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "correlation_id", ""):
            record.correlation_id = get_correlation_id()
        return True


class JsonLogFormatter(logging.Formatter):
    """Render one JSON object per record, never emitting configured secrets."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        correlation_id = getattr(record, "correlation_id", "") or get_correlation_id()
        if correlation_id:
            payload["correlation_id"] = correlation_id
        for key, value in record.__dict__.items():
            if key == "correlation_id" or key in _RESERVED_RECORD_FIELDS:
                continue
            if key in payload:
                continue
            if key.startswith("_") or _SENSITIVE_FIELD.search(key):
                continue
            payload[key] = value
        if record.exc_info:
            payload["traceback"] = self.formatException(record.exc_info)
        serialized = json.dumps(payload, default=str, sort_keys=True)
        return redact(serialized, configured_secrets())
