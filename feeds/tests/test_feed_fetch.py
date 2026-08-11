from __future__ import annotations

import socket
from dataclasses import replace
from io import BytesIO
from typing import Any, cast
from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, TestCase
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict
from urllib3.exceptions import ReadTimeoutError as Urllib3ReadTimeoutError
from urllib3.exceptions import SSLError as Urllib3SSLError

from ..feed_fetch import (
    FeedFetchError,
    FeedFetchPolicy,
    FetchedFeedDocument,
    fetch_feed_bytes,
    fetch_feed_document,
)
from ..models import Article
from ..services import discover_feed_metadata, refresh_feed
from .support.builders import build_feed

PUBLIC_IPV4 = "93.184.216.34"
PUBLIC_IPV6 = "2001:4860:4860::8888"


def _address(ip: str) -> tuple[Any, ...]:
    if ":" in ip:
        return (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 443, 0, 0))
    return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))


class RedirectToLoopbackAdapter(BaseAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.sent_urls: list[str] = []
        self.closed = False

    def send(
        self,
        request: requests.PreparedRequest,
        stream: bool = False,
        timeout: Any = None,
        verify: bool | str = True,
        cert: Any = None,
        proxies: Any = None,
    ) -> requests.Response:
        self.sent_urls.append(str(request.url))
        response = requests.Response()
        response.status_code = 302
        response.headers["Location"] = "http://127.0.0.1/private"
        response.url = str(request.url)
        response.request = request
        response.raw = BytesIO()
        return response

    def close(self) -> None:
        self.closed = True


def _document(
    content: bytes = b"<rss />",
    *,
    final_url: str = "https://feeds.example/feed.xml",
    content_type: str = "application/rss+xml; charset=utf-8",
) -> FetchedFeedDocument:
    return FetchedFeedDocument(
        content=content,
        final_url=final_url,
        response_headers={
            "content-location": final_url,
            "content-type": content_type,
        },
    )


def _response(
    body: bytes = b"<rss />",
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    response = Mock(spec=requests.Response)
    response.status_code = status
    response.headers = CaseInsensitiveDict(headers or {})
    response.iter_content.return_value = [body]
    return cast(requests.Response, response)


class FeedFetchGatewayTests(SimpleTestCase):
    policy = FeedFetchPolicy(
        connect_timeout_seconds=1.5,
        read_timeout_seconds=4.0,
        total_timeout_seconds=12.0,
        max_bytes=32,
        max_redirects=2,
    )

    @patch("feeds.feed_fetch.requests.Session.send")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_success_sends_canonical_request_with_bounded_transport(
        self, mock_getaddrinfo, mock_send
    ) -> None:
        mock_getaddrinfo.return_value = [_address(PUBLIC_IPV4)]
        mock_send.return_value = _response(
            b"<rss>safe</rss>",
            headers={
                "Content-Type": "application/rss+xml; charset=utf-8",
                "X-Upstream-Secret": "not-forwarded",
            },
        )

        document = fetch_feed_document("https://feeds.example", policy=self.policy)

        self.assertEqual(document.content, b"<rss>safe</rss>")
        self.assertEqual(document.final_url, "https://feeds.example/")
        self.assertEqual(
            document.response_headers,
            {
                "content-location": "https://feeds.example/",
                "content-type": "application/rss+xml; charset=utf-8",
            },
        )
        with self.assertRaises(TypeError):
            cast(Any, document.response_headers)["content-type"] = "text/plain"
        prepared = mock_send.call_args.args[0]
        self.assertEqual(prepared.url, "https://feeds.example/")
        self.assertEqual(prepared.headers["Accept-Encoding"], "identity")
        self.assertEqual(
            mock_send.call_args.kwargs,
            {
                "allow_redirects": False,
                "stream": True,
                "timeout": (1.5, 4.0),
            },
        )

    @patch("feeds.feed_fetch.requests.Session.send")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_idna_hostname_is_identical_for_dns_validation_and_send(
        self, mock_getaddrinfo, mock_send
    ) -> None:
        mock_getaddrinfo.return_value = [_address(PUBLIC_IPV4)]
        mock_send.return_value = _response()

        fetch_feed_bytes("https://éxample.com/feed", policy=self.policy)

        self.assertEqual(mock_getaddrinfo.call_args.args[0], "xn--xample-9ua.com")
        prepared = mock_send.call_args.args[0]
        self.assertEqual(prepared.url, "https://xn--xample-9ua.com/feed")

    @patch("feeds.feed_fetch.requests.Session.send")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_rejects_invalid_scheme_credentials_and_port(
        self, mock_getaddrinfo, mock_send
    ) -> None:
        cases = {
            "file:///etc/passwd": "invalid_url",
            "//feeds.example/rss": "invalid_url",
            "https://user:secret@feeds.example/rss": "invalid_url",
            "https://feeds.example:8443/rss": "blocked_port",
        }
        for url, code in cases.items():
            with self.subTest(url=url), self.assertRaises(FeedFetchError) as caught:
                fetch_feed_bytes(url, policy=self.policy)
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("secret", str(caught.exception))

        mock_send.assert_not_called()
        self.assertEqual(mock_getaddrinfo.call_count, 0)

    @patch("feeds.feed_fetch.requests.Session.send")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_rejects_every_non_global_address(
        self, mock_getaddrinfo, mock_send
    ) -> None:
        blocked = (
            "127.0.0.1",
            "10.0.0.1",
            "169.254.1.1",
            "224.0.0.1",
            "240.0.0.1",
            "0.0.0.0",
            "100.64.0.1",
            "100.100.100.100",
            "::1",
            "fd00::1",
            "fe80::1",
            "ff02::1",
            "fec0::1",
            "100::1",
            "::ffff:127.0.0.1",
            "::",
        )
        for ip in blocked:
            with self.subTest(ip=ip):
                mock_getaddrinfo.return_value = [_address(ip)]
                with self.assertRaises(FeedFetchError) as caught:
                    fetch_feed_bytes("https://feeds.example/rss", policy=self.policy)
                self.assertEqual(caught.exception.code, "blocked_target")

        mock_send.assert_not_called()

    @patch("feeds.feed_fetch.requests.Session.send")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_rejects_mixed_public_and_blocked_dns_answers(
        self, mock_getaddrinfo, mock_send
    ) -> None:
        mock_getaddrinfo.return_value = [
            _address(PUBLIC_IPV4),
            _address("100.100.100.100"),
        ]

        with self.assertRaises(FeedFetchError) as caught:
            fetch_feed_bytes("https://feeds.example/rss", policy=self.policy)

        self.assertEqual(caught.exception.code, "blocked_target")
        mock_send.assert_not_called()

    @patch("feeds.feed_fetch.requests.Session.send")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_allows_global_ipv4_mapped_ipv6_address(
        self, mock_getaddrinfo, mock_send
    ) -> None:
        mock_getaddrinfo.return_value = [_address(f"::ffff:{PUBLIC_IPV4}")]
        mock_send.return_value = _response()

        content = fetch_feed_bytes("https://feeds.example/rss", policy=self.policy)

        self.assertEqual(content, b"<rss />")

    def test_rejects_non_finite_and_invalid_policy_values(self) -> None:
        invalid_policies = (
            replace(self.policy, connect_timeout_seconds=float("nan")),
            replace(self.policy, read_timeout_seconds=float("inf")),
            replace(self.policy, total_timeout_seconds=0),
            replace(self.policy, max_bytes=0),
            replace(self.policy, max_bytes=cast(Any, 1.5)),
            replace(self.policy, max_redirects=-1),
            replace(self.policy, max_redirects=cast(Any, True)),
        )
        for policy in invalid_policies:
            with (
                self.subTest(policy=policy),
                self.assertRaises(FeedFetchError) as caught,
            ):
                fetch_feed_bytes("https://feeds.example/rss", policy=policy)
            self.assertEqual(caught.exception.code, "invalid_policy")

    @patch("feeds.feed_fetch.requests.Session.send")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_classifies_request_timeout(self, mock_getaddrinfo, mock_send) -> None:
        mock_getaddrinfo.return_value = [_address(PUBLIC_IPV4)]
        mock_send.side_effect = requests.Timeout("sensitive upstream detail")

        with self.assertRaises(FeedFetchError) as caught:
            fetch_feed_bytes("https://feeds.example/rss", policy=self.policy)

        self.assertEqual(caught.exception.code, "timeout")
        self.assertEqual(str(caught.exception), "Feed request timed out.")
        self.assertIsNone(caught.exception.__cause__)

    @patch("feeds.feed_fetch.requests.Session.send")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_classifies_requests_stream_read_timeout(
        self, mock_getaddrinfo, mock_send
    ) -> None:
        mock_getaddrinfo.return_value = [_address(PUBLIC_IPV4)]
        response = _response()
        cast(Any, response).iter_content.side_effect = requests.ConnectionError(
            Urllib3ReadTimeoutError(cast(Any, Mock()), None, "read timed out")
        )
        mock_send.return_value = response

        with self.assertRaises(FeedFetchError) as caught:
            fetch_feed_bytes("https://feeds.example/rss", policy=self.policy)

        self.assertEqual(caught.exception.code, "timeout")

    @patch("feeds.feed_fetch.requests.Session.send")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_classifies_stream_tls_failure(self, mock_getaddrinfo, mock_send) -> None:
        mock_getaddrinfo.return_value = [_address(PUBLIC_IPV4)]
        response = _response()
        cast(Any, response).iter_content.side_effect = requests.ConnectionError(
            Urllib3SSLError("TLS record failed")
        )
        mock_send.return_value = response

        with self.assertRaises(FeedFetchError) as caught:
            fetch_feed_bytes("https://feeds.example/rss", policy=self.policy)

        self.assertEqual(caught.exception.code, "tls_failure")

    @patch("feeds.feed_fetch.requests.Session.send")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_classifies_generic_stream_connection_failure(
        self, mock_getaddrinfo, mock_send
    ) -> None:
        mock_getaddrinfo.return_value = [_address(PUBLIC_IPV4)]
        response = _response()
        cast(Any, response).iter_content.side_effect = requests.ConnectionError(
            "connection reset"
        )
        mock_send.return_value = response

        with self.assertRaises(FeedFetchError) as caught:
            fetch_feed_bytes("https://feeds.example/rss", policy=self.policy)

        self.assertEqual(caught.exception.code, "network_failure")

    @patch("feeds.feed_fetch.requests.Session.send")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_classifies_initial_tls_failure(self, mock_getaddrinfo, mock_send) -> None:
        mock_getaddrinfo.return_value = [_address(PUBLIC_IPV4)]
        mock_send.side_effect = requests.exceptions.SSLError("certificate details")

        with self.assertRaises(FeedFetchError) as caught:
            fetch_feed_bytes("https://feeds.example/rss", policy=self.policy)

        self.assertEqual(caught.exception.code, "tls_failure")
        self.assertEqual(str(caught.exception), "Feed TLS connection failed.")

    @patch("feeds.feed_fetch.requests.Session.send")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_classifies_dns_failure(self, mock_getaddrinfo, mock_send) -> None:
        mock_getaddrinfo.side_effect = socket.gaierror("resolver details")

        with self.assertRaises(FeedFetchError) as caught:
            fetch_feed_bytes("https://feeds.example/rss", policy=self.policy)

        self.assertEqual(caught.exception.code, "dns_failure")
        self.assertEqual(str(caught.exception), "Feed host could not be resolved.")
        mock_send.assert_not_called()

    @patch("feeds.feed_fetch.requests.Session.send")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_rejects_oversized_content_length(
        self, mock_getaddrinfo, mock_send
    ) -> None:
        mock_getaddrinfo.return_value = [_address(PUBLIC_IPV4)]
        mock_send.return_value = _response(
            b"small",
            headers={"Content-Length": "33"},
        )

        with self.assertRaises(FeedFetchError) as caught:
            fetch_feed_bytes("https://feeds.example/rss", policy=self.policy)

        self.assertEqual(caught.exception.code, "response_too_large")

    @patch("feeds.feed_fetch.requests.Session.send")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_rejects_body_that_exceeds_limit(self, mock_getaddrinfo, mock_send) -> None:
        mock_getaddrinfo.return_value = [_address(PUBLIC_IPV4)]
        mock_send.return_value = _response(b"x" * 33)

        with self.assertRaises(FeedFetchError) as caught:
            fetch_feed_bytes("https://feeds.example/rss", policy=self.policy)

        self.assertEqual(caught.exception.code, "response_too_large")

    @patch("feeds.feed_fetch.requests.Session.send")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_rejects_non_identity_content_encoding(
        self, mock_getaddrinfo, mock_send
    ) -> None:
        mock_getaddrinfo.return_value = [_address(PUBLIC_IPV4)]
        mock_send.return_value = _response(headers={"Content-Encoding": "gzip"})

        with self.assertRaises(FeedFetchError) as caught:
            fetch_feed_bytes("https://feeds.example/rss", policy=self.policy)

        self.assertEqual(caught.exception.code, "unsupported_encoding")

    @patch("feeds.feed_fetch.requests.Session.send")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_revalidates_and_blocks_redirect_to_private_target(
        self, mock_getaddrinfo, mock_send
    ) -> None:
        mock_getaddrinfo.side_effect = [
            [_address(PUBLIC_IPV4)],
            [_address("169.254.169.254")],
        ]
        mock_send.return_value = _response(
            status=302,
            headers={"Location": "http://169.254.169.254/latest/meta-data"},
        )

        with self.assertRaises(FeedFetchError) as caught:
            fetch_feed_bytes("https://feeds.example/rss", policy=self.policy)

        self.assertEqual(caught.exception.code, "blocked_target")
        mock_send.assert_called_once()

    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_real_session_does_not_follow_redirect_before_private_validation(
        self, mock_getaddrinfo
    ) -> None:
        mock_getaddrinfo.side_effect = [
            [_address(PUBLIC_IPV4)],
            [_address("127.0.0.1")],
        ]
        session = requests.Session()
        adapter = RedirectToLoopbackAdapter()
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        with (
            patch("feeds.feed_fetch.requests.Session", return_value=session),
            self.assertRaises(FeedFetchError) as caught,
        ):
            fetch_feed_bytes("https://feeds.example/start", policy=self.policy)

        self.assertEqual(caught.exception.code, "blocked_target")
        self.assertEqual(adapter.sent_urls, ["https://feeds.example/start"])
        self.assertTrue(adapter.closed)

    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_real_session_honors_zero_redirect_limit(self, mock_getaddrinfo) -> None:
        mock_getaddrinfo.return_value = [_address(PUBLIC_IPV4)]
        session = requests.Session()
        adapter = RedirectToLoopbackAdapter()
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        policy = replace(self.policy, max_redirects=0)

        with (
            patch("feeds.feed_fetch.requests.Session", return_value=session),
            self.assertRaises(FeedFetchError) as caught,
        ):
            fetch_feed_bytes("https://feeds.example/start", policy=policy)

        self.assertEqual(caught.exception.code, "redirect_limit")
        self.assertEqual(adapter.sent_urls, ["https://feeds.example/start"])
        self.assertTrue(adapter.closed)

    @patch("feeds.feed_fetch.requests.Session.send")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_follows_relative_redirect_and_closes_each_response(
        self, mock_getaddrinfo, mock_send
    ) -> None:
        mock_getaddrinfo.return_value = [_address(PUBLIC_IPV4)]
        redirect = _response(status=302, headers={"Location": "../final.xml"})
        final = _response(b"final feed")
        mock_send.side_effect = [redirect, final]

        document = fetch_feed_document(
            "https://feeds.example/path/start.xml", policy=self.policy
        )

        self.assertEqual(document.content, b"final feed")
        self.assertEqual(document.final_url, "https://feeds.example/final.xml")
        self.assertEqual(
            document.response_headers["content-location"],
            "https://feeds.example/final.xml",
        )
        self.assertEqual(
            [call.args[0].url for call in mock_send.call_args_list],
            [
                "https://feeds.example/path/start.xml",
                "https://feeds.example/final.xml",
            ],
        )
        cast(Any, redirect).close.assert_called_once_with()
        cast(Any, final).close.assert_called_once_with()

    @patch("feeds.feed_fetch.requests.Session.send")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_enforces_redirect_limit(self, mock_getaddrinfo, mock_send) -> None:
        mock_getaddrinfo.return_value = [_address(PUBLIC_IPV4)]
        mock_send.side_effect = [
            _response(status=302, headers={"Location": "/second"}),
            _response(status=302, headers={"Location": "/third"}),
        ]
        policy = replace(self.policy, max_redirects=1)

        with self.assertRaises(FeedFetchError) as caught:
            fetch_feed_bytes("https://feeds.example/first", policy=policy)

        self.assertEqual(caught.exception.code, "redirect_limit")
        self.assertEqual(mock_send.call_count, 2)

    @patch("feeds.feed_fetch.requests.Session.send")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_rejects_missing_and_invalid_redirect_location(
        self, mock_getaddrinfo, mock_send
    ) -> None:
        mock_getaddrinfo.return_value = [_address(PUBLIC_IPV4)]
        for response in (
            _response(status=302),
            _response(status=302, headers={"Location": "http://[invalid"}),
        ):
            mock_send.reset_mock()
            mock_send.return_value = response
            with (
                self.subTest(headers=response.headers),
                self.assertRaises(FeedFetchError) as caught,
            ):
                fetch_feed_bytes("https://feeds.example/rss", policy=self.policy)
            self.assertEqual(caught.exception.code, "invalid_redirect")
            cast(Any, response).close.assert_called_once_with()

    @patch("feeds.feed_fetch.requests.Session.send")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_rejects_http_failure_and_closes_response(
        self, mock_getaddrinfo, mock_send
    ) -> None:
        mock_getaddrinfo.return_value = [_address(PUBLIC_IPV4)]
        response = _response(status=503)
        mock_send.return_value = response

        with self.assertRaises(FeedFetchError) as caught:
            fetch_feed_bytes("https://feeds.example/rss", policy=self.policy)

        self.assertEqual(caught.exception.code, "http_failure")
        self.assertNotIn("feeds.example", str(caught.exception))
        cast(Any, response).close.assert_called_once_with()

    @patch("feeds.feed_fetch.requests.Session.send")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_rejects_empty_body(self, mock_getaddrinfo, mock_send) -> None:
        mock_getaddrinfo.return_value = [_address(PUBLIC_IPV4)]
        mock_send.return_value = _response(b"")

        with self.assertRaises(FeedFetchError) as caught:
            fetch_feed_bytes("https://feeds.example/rss", policy=self.policy)

        self.assertEqual(caught.exception.code, "invalid_content")

    @patch("feeds.feed_fetch.requests.Session")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_session_disables_environment_and_closes_on_success(
        self, mock_getaddrinfo, mock_session_factory
    ) -> None:
        mock_getaddrinfo.return_value = [_address(PUBLIC_IPV4)]
        session = mock_session_factory.return_value
        response = _response()
        session.send.return_value = response

        fetch_feed_bytes("https://feeds.example/rss", policy=self.policy)

        self.assertFalse(session.trust_env)
        cast(Any, response).close.assert_called_once_with()
        session.close.assert_called_once_with()

    @patch("feeds.feed_fetch.requests.Session")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_session_closes_on_request_failure(
        self, mock_getaddrinfo, mock_session_factory
    ) -> None:
        mock_getaddrinfo.return_value = [_address(PUBLIC_IPV4)]
        session = mock_session_factory.return_value
        session.send.side_effect = requests.ConnectionError("reset")

        with self.assertRaises(FeedFetchError) as caught:
            fetch_feed_bytes("https://feeds.example/rss", policy=self.policy)

        self.assertEqual(caught.exception.code, "network_failure")
        session.close.assert_called_once_with()

    @patch("feeds.feed_fetch.time.monotonic", side_effect=[0, 0, 0, 0, 61])
    @patch("feeds.feed_fetch.requests.Session.send")
    @patch("feeds.feed_fetch.socket.getaddrinfo")
    def test_total_deadline_is_checked_between_chunks(
        self, mock_getaddrinfo, mock_send, mock_monotonic
    ) -> None:
        mock_getaddrinfo.return_value = [_address(PUBLIC_IPV4)]
        mock_send.return_value = _response(b"late")

        with self.assertRaises(FeedFetchError) as caught:
            fetch_feed_bytes("https://feeds.example/rss", policy=self.policy)

        self.assertEqual(caught.exception.code, "timeout")


class FeedFetchServiceIntegrationTests(TestCase):
    @patch("feeds.services.feedparser.parse")
    @patch("feeds.services.fetch_feed_document")
    def test_refresh_passes_bytes_and_lowercase_http_metadata_to_parser(
        self, mock_fetch, mock_parse
    ) -> None:
        feed = build_feed(feed_url="https://feeds.example/rss")
        document = _document(content=b"downloaded feed")
        mock_fetch.return_value = document
        mock_parse.return_value = {"feed": {"title": "Fetched"}, "entries": []}

        refresh_feed(feed)

        mock_fetch.assert_called_once_with(feed.feed_url)
        mock_parse.assert_called_once_with(
            b"downloaded feed",
            response_headers=document.response_headers,
        )
        self.assertEqual(
            set(mock_parse.call_args.kwargs["response_headers"]),
            {"content-location", "content-type"},
        )

    @patch("feeds.services.feedparser.parse")
    @patch("feeds.services.fetch_feed_document")
    def test_metadata_discovery_passes_http_metadata_to_parser(
        self, mock_fetch, mock_parse
    ) -> None:
        document = _document(content=b"downloaded metadata feed")
        mock_fetch.return_value = document
        mock_parse.return_value = {"feed": {"title": "Fetched"}, "entries": []}

        metadata = discover_feed_metadata("https://feeds.example/rss")

        self.assertEqual(metadata["title"], "Fetched")
        mock_fetch.assert_called_once_with("https://feeds.example/rss")
        mock_parse.assert_called_once_with(
            b"downloaded metadata feed",
            response_headers=document.response_headers,
        )

    @patch("feeds.services.fetch_feed_document")
    def test_final_url_resolves_relative_feed_and_article_links(
        self, mock_fetch
    ) -> None:
        feed = build_feed(feed_url="https://feeds.example/original.xml")
        mock_fetch.return_value = _document(
            content=(
                b"<rss version='2.0'><channel><title>Relative feed</title>"
                b"<link>../site</link><item><title>Relative article</title>"
                b"<guid isPermaLink='false'>relative-item</guid>"
                b"<link>article.html</link>"
                b"</item></channel></rss>"
            ),
            final_url="https://cdn.example/path/feed.xml",
        )

        refresh_feed(feed)

        feed.refresh_from_db()
        article = Article.objects.get(feed=feed, guid="relative-item")
        self.assertEqual(feed.site_url, "https://cdn.example/site")
        self.assertEqual(article.url, "https://cdn.example/path/article.html")

    @patch("feeds.services.fetch_feed_document")
    def test_http_content_type_charset_is_used_for_metadata(self, mock_fetch) -> None:
        mock_fetch.return_value = _document(
            content=(
                b"<rss version='2.0'><channel><title>Caf\xe9 feed</title>"
                b"<link>https://example.com/</link></channel></rss>"
            ),
            content_type="application/rss+xml; charset=iso-8859-1",
        )

        metadata = discover_feed_metadata("https://feeds.example/rss")

        self.assertEqual(metadata["title"], "Caf\u00e9 feed")
