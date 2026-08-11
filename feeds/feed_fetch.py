from __future__ import annotations

import ipaddress
import math
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final
from urllib.parse import urljoin, urlsplit

import requests
from django.conf import settings
from urllib3.exceptions import ReadTimeoutError as Urllib3ReadTimeoutError
from urllib3.exceptions import SSLError as Urllib3SSLError

_REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})
_STREAM_CHUNK_BYTES: Final = 64 * 1024
_ALLOWED_PORTS: Final = frozenset({80, 443})


class FeedFetchError(Exception):
    """A classified feed transport failure with a caller-safe message."""

    def __init__(self, *, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class FetchedFeedDocument:
    content: bytes
    final_url: str
    response_headers: Mapping[str, str]

    def __post_init__(self) -> None:
        normalized_headers = {
            key.lower(): value for key, value in self.response_headers.items()
        }
        object.__setattr__(
            self,
            "response_headers",
            MappingProxyType(normalized_headers),
        )


@dataclass(frozen=True)
class FeedFetchPolicy:
    connect_timeout_seconds: float
    read_timeout_seconds: float
    total_timeout_seconds: float
    max_bytes: int
    max_redirects: int

    @classmethod
    def from_settings(cls) -> FeedFetchPolicy:
        return cls(
            connect_timeout_seconds=settings.FEED_FETCH_CONNECT_TIMEOUT_SECONDS,
            read_timeout_seconds=settings.FEED_FETCH_READ_TIMEOUT_SECONDS,
            total_timeout_seconds=settings.FEED_FETCH_TOTAL_TIMEOUT_SECONDS,
            max_bytes=settings.FEED_FETCH_MAX_BYTES,
            max_redirects=settings.FEED_FETCH_MAX_REDIRECTS,
        )


def _error(code: str, message: str) -> FeedFetchError:
    return FeedFetchError(code=code, message=message)


def _valid_positive_number(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _validate_policy(policy: FeedFetchPolicy) -> None:
    if not all(
        _valid_positive_number(value)
        for value in (
            policy.connect_timeout_seconds,
            policy.read_timeout_seconds,
            policy.total_timeout_seconds,
        )
    ):
        raise _error("invalid_policy", "Feed fetch policy is invalid.")
    if (
        not isinstance(policy.max_bytes, int)
        or isinstance(policy.max_bytes, bool)
        or policy.max_bytes <= 0
        or not isinstance(policy.max_redirects, int)
        or isinstance(policy.max_redirects, bool)
        or policy.max_redirects < 0
    ):
        raise _error("invalid_policy", "Feed fetch policy is invalid.")


def _prepare_request(url: str, *, error_code: str) -> requests.PreparedRequest:
    try:
        prepared = requests.Request(
            method="GET",
            url=url,
            headers={
                "Accept-Encoding": "identity",
                "User-Agent": requests.utils.default_user_agent(),
            },
        ).prepare()
    except (requests.RequestException, UnicodeError, ValueError):
        message = (
            "Feed redirect destination is invalid."
            if error_code == "invalid_redirect"
            else "Feed URL is invalid."
        )
        raise _error(error_code, message) from None
    if prepared.url is None:
        raise _error(error_code, "Feed URL is invalid.")
    return prepared


def _validate_target(canonical_url: str) -> None:
    try:
        parsed = urlsplit(canonical_url)
        port = parsed.port
    except ValueError:
        raise _error("invalid_url", "Feed URL is invalid.") from None

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise _error(
            "invalid_url",
            "Feed URL must be an absolute HTTP(S) URL without credentials.",
        )

    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    if port not in _ALLOWED_PORTS:
        raise _error(
            "blocked_port",
            "Feed URL must use destination port 80 or 443.",
        )

    try:
        addresses = socket.getaddrinfo(
            parsed.hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError:
        raise _error("dns_failure", "Feed host could not be resolved.") from None
    if not addresses:
        raise _error("dns_failure", "Feed host could not be resolved.")

    for address in addresses:
        try:
            ip = ipaddress.ip_address(address[4][0])
        except ValueError:
            raise _error(
                "dns_failure", "Feed host returned an invalid address."
            ) from None
        if not ip.is_global or ip.is_multicast or getattr(ip, "is_site_local", False):
            raise _error(
                "blocked_target",
                "Feed URL resolves to a blocked network address.",
            )


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise _error("timeout", "Feed request exceeded its total time limit.")


def _content_length(response: requests.Response) -> int | None:
    value = response.headers.get("Content-Length")
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _validate_content_encoding(response: requests.Response) -> None:
    value = response.headers.get("Content-Encoding", "")
    encodings = [encoding.strip().lower() for encoding in value.split(",") if encoding]
    if any(encoding != "identity" for encoding in encodings):
        raise _error(
            "unsupported_encoding",
            "Feed response used an unsupported content encoding.",
        )


def _nested_exception_is(
    exc: BaseException, types: tuple[type[BaseException], ...]
) -> bool:
    pending: list[object] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, types):
            return True
        if isinstance(current, BaseException):
            pending.extend(current.args)
            if current.__cause__ is not None:
                pending.append(current.__cause__)
            if current.__context__ is not None:
                pending.append(current.__context__)
    return False


def _classify_stream_error(exc: requests.RequestException) -> FeedFetchError:
    if isinstance(exc, requests.exceptions.SSLError) or _nested_exception_is(
        exc, (Urllib3SSLError,)
    ):
        return _error("tls_failure", "Feed TLS connection failed.")
    if isinstance(exc, requests.Timeout) or _nested_exception_is(
        exc, (Urllib3ReadTimeoutError,)
    ):
        return _error("timeout", "Feed request timed out.")
    return _error("network_failure", "Feed could not be downloaded.")


def _read_bounded(
    response: requests.Response,
    *,
    max_bytes: int,
    deadline: float,
) -> bytes:
    _validate_content_encoding(response)
    content_length = _content_length(response)
    if content_length is not None and content_length > max_bytes:
        raise _error("response_too_large", "Feed response is too large.")

    chunks: list[bytes] = []
    received = 0
    try:
        _check_deadline(deadline)
        for chunk in response.iter_content(chunk_size=_STREAM_CHUNK_BYTES):
            _check_deadline(deadline)
            if not chunk:
                continue
            received += len(chunk)
            if received > max_bytes:
                raise _error("response_too_large", "Feed response is too large.")
            chunks.append(chunk)
    except FeedFetchError:
        raise
    except requests.RequestException as exc:
        raise _classify_stream_error(exc) from None

    content = b"".join(chunks)
    if not content:
        raise _error("invalid_content", "Feed response was empty.")
    return content


def fetch_feed_document(
    url: str,
    *,
    policy: FeedFetchPolicy | None = None,
) -> FetchedFeedDocument:
    """Download a feed through bounded, redirect-aware HTTP transport.

    The total deadline is checked before requests and between streamed chunks.
    It cannot interrupt a socket call before the read timeout and is not a hard
    wall-clock deadline against adversarial slow-drip responses.
    """

    active_policy = policy or FeedFetchPolicy.from_settings()
    _validate_policy(active_policy)
    deadline = time.monotonic() + active_policy.total_timeout_seconds

    prepared = _prepare_request(url, error_code="invalid_url")
    redirects_followed = 0
    session = requests.Session()
    session.trust_env = False
    try:
        while True:
            canonical_url = prepared.url
            if canonical_url is None:  # Defensive; _prepare_request already checks.
                raise _error("invalid_url", "Feed URL is invalid.")
            _check_deadline(deadline)
            _validate_target(canonical_url)
            _check_deadline(deadline)
            try:
                response = session.send(
                    prepared,
                    allow_redirects=False,
                    stream=True,
                    timeout=(
                        active_policy.connect_timeout_seconds,
                        active_policy.read_timeout_seconds,
                    ),
                )
            except requests.Timeout:
                raise _error("timeout", "Feed request timed out.") from None
            except requests.exceptions.SSLError:
                raise _error("tls_failure", "Feed TLS connection failed.") from None
            except requests.RequestException:
                raise _error(
                    "network_failure", "Feed could not be downloaded."
                ) from None

            try:
                if response.status_code in _REDIRECT_STATUSES:
                    if redirects_followed >= active_policy.max_redirects:
                        raise _error(
                            "redirect_limit", "Feed redirected too many times."
                        )
                    location = response.headers.get("Location")
                    if not location:
                        raise _error(
                            "invalid_redirect", "Feed redirect had no destination."
                        )
                    try:
                        redirect_url = urljoin(canonical_url, location)
                    except ValueError:
                        raise _error(
                            "invalid_redirect", "Feed redirect destination is invalid."
                        ) from None
                    prepared = _prepare_request(
                        redirect_url,
                        error_code="invalid_redirect",
                    )
                    redirects_followed += 1
                    continue
                if not 200 <= response.status_code < 300:
                    raise _error(
                        "http_failure",
                        "Feed server returned an unsuccessful response.",
                    )
                content = _read_bounded(
                    response,
                    max_bytes=active_policy.max_bytes,
                    deadline=deadline,
                )
                response_headers = {"content-location": canonical_url}
                content_type = response.headers.get("Content-Type")
                if content_type:
                    response_headers["content-type"] = content_type
                return FetchedFeedDocument(
                    content=content,
                    final_url=canonical_url,
                    response_headers=response_headers,
                )
            finally:
                response.close()
    finally:
        session.close()


def fetch_feed_bytes(
    url: str,
    *,
    policy: FeedFetchPolicy | None = None,
) -> bytes:
    """Compatibility wrapper returning only the bounded response body."""

    return fetch_feed_document(url, policy=policy).content
