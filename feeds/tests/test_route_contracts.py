"""Every route's authentication, method, CSRF, and isolation posture.

Driven from the URLconf rather than a hand-listed set, so a route added later
cannot quietly ship without declaring how it is protected: the inventory test
fails until the new name appears in ``ROUTES`` below.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.test import Client, TestCase
from django.urls import get_resolver, reverse

from feeds.models import ArticleReadState, Feed, SavedArticle, UserPreference

from .support.base import StaticFilesTestCase, model_id
from .support.builders import (
    build_article,
    build_feed,
    build_newsletter_issue,
    build_read_state,
    build_user,
)

# Auth posture of every route.
#   session  - browser session; anonymous is redirected to the login page
#   bearer   - API token; anonymous is refused with 401
#   signed   - single-use signed POST; an unsigned request is refused with 403
#   webhook  - inbound provider credentials; unauthenticated is refused
#   public   - deliberately reachable without credentials
#   admin    - Django admin, which owns its own redirect behavior
SESSION, BEARER, SIGNED, WEBHOOK, PUBLIC, ADMIN = (
    "session",
    "bearer",
    "signed",
    "webhook",
    "public",
    "admin",
)


@dataclass(frozen=True)
class Route:
    posture: str
    safe: tuple[str, ...] = ("GET",)
    unsafe: tuple[str, ...] = ()


ROUTES: dict[str, Route] = {
    # Browser reading surfaces.
    "today": Route(SESSION),
    "week": Route(SESSION),
    "month": Route(SESSION),
    "archived": Route(SESSION),
    "saved-links": Route(SESSION),
    "feed-detail": Route(SESSION),
    "opml-export": Route(SESSION),
    # Deliberately method-agnostic: API-COMPAT-INV-001 characterizes this legacy
    # endpoint as accepting any method behind session auth and CSRF, and an
    # external agent may rely on that. CSRF still guards the unsafe methods,
    # which the CSRF sweep below asserts.
    "digest-json": Route(
        SESSION, safe=("GET",), unsafe=("POST", "PUT", "PATCH", "DELETE")
    ),
    # Browser forms.
    "feeds": Route(SESSION, unsafe=("POST",)),
    "opml-import": Route(SESSION, unsafe=("POST",)),
    "preferences": Route(SESSION, unsafe=("POST",)),
    # Browser mutations.
    "mark-article": Route(SESSION, safe=(), unsafe=("POST",)),
    "save-article": Route(SESSION, safe=(), unsafe=("POST",)),
    "mark-feed-read": Route(SESSION, safe=(), unsafe=("POST",)),
    "mark-period-read": Route(SESSION, safe=(), unsafe=("POST",)),
    "refresh-feeds": Route(SESSION, safe=(), unsafe=("POST",)),
    # Bearer API.
    "api-morning-briefing": Route(BEARER),
    "api-articles": Route(BEARER),
    "api-article-read": Route(BEARER, safe=(), unsafe=("POST", "PATCH")),
    "api-article-saved": Route(BEARER, safe=(), unsafe=("POST", "PATCH", "DELETE")),
    "api-mark-period-read": Route(BEARER, safe=(), unsafe=("POST",)),
    "api-feeds": Route(BEARER, unsafe=("POST",)),
    "api-feed-detail": Route(BEARER, unsafe=("PATCH", "DELETE")),
    "api-feed-mark-read": Route(BEARER, safe=(), unsafe=("POST",)),
    "api-categories": Route(BEARER, unsafe=("POST",)),
    "api-preferences": Route(BEARER, unsafe=("PATCH",)),
    "api-refresh": Route(BEARER, safe=(), unsafe=("POST",)),
    # Signed single-use actions.
    "api-article-save-and-go": Route(SIGNED, safe=(), unsafe=("POST",)),
    "api-mark-period-read-and-go": Route(SIGNED, safe=(), unsafe=("POST",)),
    # Inbound webhooks.
    "postmark-inbound": Route(WEBHOOK, safe=(), unsafe=("POST",)),
    "postmark-inbound-basic": Route(WEBHOOK, safe=(), unsafe=("POST",)),
    # Deliberately public.
    "newsletter-detail": Route(PUBLIC),
    "health-live": Route(PUBLIC),
    "health-ready": Route(PUBLIC),
    "health-status": Route(BEARER),
    "login": Route(PUBLIC, unsafe=("POST",)),
    "logout": Route(PUBLIC, safe=(), unsafe=("POST",)),
}

ADMIN_PREFIXES = ("admin:",)
ALL_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _route_names() -> set[str]:
    names = set()
    for name in get_resolver().reverse_dict.keys():
        if not isinstance(name, str):
            continue
        if any(name.startswith(prefix) for prefix in ADMIN_PREFIXES):
            continue
        names.add(name)
    return names


class RouteInventoryTests(TestCase):
    """The guard that keeps this file honest as routes are added."""

    def test_every_route_declares_an_auth_posture(self) -> None:
        declared = set(ROUTES)
        actual = _route_names()

        self.assertEqual(
            actual - declared,
            set(),
            "a route exists with no declared auth posture; add it to ROUTES",
        )
        self.assertEqual(
            declared - actual,
            set(),
            "ROUTES names a route that no longer exists",
        )

    def test_every_posture_is_one_of_the_known_kinds(self) -> None:
        self.assertEqual(
            {route.posture for route in ROUTES.values()}
            - {
                SESSION,
                BEARER,
                SIGNED,
                WEBHOOK,
                PUBLIC,
                ADMIN,
            },
            set(),
        )

    def test_no_route_declares_a_method_twice(self) -> None:
        for name, route in ROUTES.items():
            with self.subTest(route=name):
                self.assertEqual(
                    set(route.safe) & set(route.unsafe),
                    set(),
                    "a method cannot be both safe and unsafe",
                )


class RouteContractTestCase(StaticFilesTestCase):
    """Shared fixtures giving every parameterized route a resolvable target."""

    def setUp(self) -> None:
        self.user = build_user(username="route-owner")
        self.feed = build_feed()
        self.article = build_article(feed=self.feed, title="Route article")
        self.issue = build_newsletter_issue(article=build_article(feed=self.feed))

    def url_for(self, name: str) -> str:
        targets: dict[str, dict[str, Any]] = {
            "feed-detail": {"feed_id": model_id(self.feed)},
            "mark-feed-read": {"feed_id": model_id(self.feed)},
            "mark-article": {"article_id": model_id(self.article)},
            "save-article": {"article_id": model_id(self.article)},
            "newsletter-detail": {"public_id": self.issue.public_id},
            "postmark-inbound": {"secret": "not-the-secret"},
            "api-article-read": {"article_id": model_id(self.article)},
            "api-article-saved": {"article_id": model_id(self.article)},
            "api-article-save-and-go": {"article_id": model_id(self.article)},
            "api-feed-detail": {"feed_id": model_id(self.feed)},
            "api-feed-mark-read": {"feed_id": model_id(self.feed)},
        }
        return reverse(name, kwargs=targets.get(name, {}))

    def request(self, method: str, url: str, *, client: Client | None = None):
        return getattr(client or self.client, method.lower())(url)


class AnonymousAccessTests(RouteContractTestCase):
    """No protected route serves or mutates anything without credentials."""

    def test_session_routes_redirect_anonymous_requests_to_login(self) -> None:
        for name, route in ROUTES.items():
            if route.posture != SESSION:
                continue
            for method in route.safe + route.unsafe:
                with self.subTest(route=name, method=method):
                    response = self.request(method, self.url_for(name))
                    self.assertEqual(response.status_code, 302)
                    self.assertIn("/accounts/login/", response["Location"])

    def test_bearer_routes_refuse_anonymous_requests_with_401(self) -> None:
        for name, route in ROUTES.items():
            if route.posture != BEARER:
                continue
            for method in route.safe + route.unsafe:
                with self.subTest(route=name, method=method):
                    self.assertEqual(
                        self.request(method, self.url_for(name)).status_code, 401
                    )

    def test_signed_routes_refuse_an_unsigned_request(self) -> None:
        for name, route in ROUTES.items():
            if route.posture != SIGNED:
                continue
            for method in route.unsafe:
                with self.subTest(route=name, method=method):
                    self.assertEqual(
                        self.request(method, self.url_for(name)).status_code, 403
                    )

    def test_webhook_routes_refuse_unauthenticated_deliveries(self) -> None:
        for name, route in ROUTES.items():
            if route.posture != WEBHOOK:
                continue
            for method in route.unsafe:
                with self.subTest(route=name, method=method):
                    self.assertIn(
                        self.request(method, self.url_for(name)).status_code,
                        {401, 403},
                    )

    def test_no_protected_route_writes_anything_for_an_anonymous_caller(self) -> None:
        before = (
            ArticleReadState.objects.count(),
            SavedArticle.objects.count(),
            Feed.objects.count(),
        )

        for name, route in ROUTES.items():
            if route.posture == PUBLIC:
                continue
            for method in route.unsafe:
                self.request(method, self.url_for(name))

        self.assertEqual(
            (
                ArticleReadState.objects.count(),
                SavedArticle.objects.count(),
                Feed.objects.count(),
            ),
            before,
        )


class MethodContractTests(RouteContractTestCase):
    """A route answers 405 to every method it does not implement."""

    def setUp(self) -> None:
        super().setUp()
        self.client.force_login(self.user)

    def test_undeclared_methods_are_refused(self) -> None:
        for name, route in ROUTES.items():
            if route.posture in {SIGNED, WEBHOOK, PUBLIC}:
                # These answer on their own authentication terms first, which
                # their own suites assert; method order is covered there.
                continue
            allowed = set(route.safe) | set(route.unsafe)
            if "GET" in allowed:
                allowed.add("HEAD")
            for method in ALL_METHODS:
                if method in allowed:
                    continue
                with self.subTest(route=name, method=method):
                    self.assertEqual(
                        self.request(method, self.url_for(name)).status_code,
                        405,
                        f"{name} should refuse {method}",
                    )

    def test_read_only_session_routes_refuse_unsafe_methods(self) -> None:
        """A reading surface used to answer 200 to POST, PUT, and DELETE."""

        read_only = [
            name
            for name, route in ROUTES.items()
            if route.posture == SESSION and not route.unsafe and route.safe
        ]
        self.assertNotEqual(read_only, [])

        for name in read_only:
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                with self.subTest(route=name, method=method):
                    self.assertEqual(
                        self.request(method, self.url_for(name)).status_code, 405
                    )

    def test_declared_safe_methods_are_served(self) -> None:
        for name, route in ROUTES.items():
            if route.posture != SESSION or "GET" not in route.safe:
                continue
            with self.subTest(route=name):
                self.assertEqual(
                    self.request("GET", self.url_for(name)).status_code, 200
                )


class CsrfEnforcementTests(RouteContractTestCase):
    """Every session mutation is CSRF-protected."""

    def setUp(self) -> None:
        super().setUp()
        self.csrf_client = Client(enforce_csrf_checks=True)
        self.csrf_client.force_login(self.user)

    def test_session_mutations_require_a_csrf_token(self) -> None:
        mutations = [
            name
            for name, route in ROUTES.items()
            if route.posture == SESSION and route.unsafe
        ]
        self.assertNotEqual(mutations, [])

        for name in mutations:
            for method in ROUTES[name].unsafe:
                with self.subTest(route=name, method=method):
                    response = self.request(
                        method, self.url_for(name), client=self.csrf_client
                    )
                    self.assertEqual(response.status_code, 403)

    def test_a_csrf_rejected_mutation_writes_nothing(self) -> None:
        before = ArticleReadState.objects.count()

        self.csrf_client.post(self.url_for("mark-article"), {"is_read": "true"})

        self.assertEqual(ArticleReadState.objects.count(), before)


class UserIsolationTests(RouteContractTestCase):
    """One reader's state and actions never reach another's."""

    def setUp(self) -> None:
        super().setUp()
        self.stranger = build_user(username="route-stranger")
        self.client.force_login(self.user)

    def test_a_mutation_writes_state_only_for_the_acting_reader(self) -> None:
        self.client.post(
            self.url_for("mark-article"),
            {"is_read": "true", "article_id": model_id(self.article)},
        )

        self.assertTrue(
            ArticleReadState.objects.filter(
                user=self.user, article=self.article, is_read=True
            ).exists()
        )
        self.assertFalse(ArticleReadState.objects.filter(user=self.stranger).exists())

    def test_another_readers_read_state_does_not_hide_an_article(self) -> None:
        build_read_state(user=self.stranger, article=self.article, is_read=True)

        response = self.client.get(self.url_for("today"))

        self.assertContains(response, self.article.title)

    def test_another_readers_save_does_not_appear_in_saved_links(self) -> None:
        SavedArticle.objects.create(
            user=self.stranger,
            article=self.article,
            url=self.article.url,
            title="Stranger's save",
        )

        response = self.client.get(self.url_for("saved-links"))

        self.assertNotContains(response, "Stranger's save")

    def test_marking_a_feed_read_does_not_touch_another_readers_state(self) -> None:
        self.client.post(self.url_for("mark-feed-read"))

        self.assertFalse(ArticleReadState.objects.filter(user=self.stranger).exists())

    def test_another_readers_archive_stays_private(self) -> None:
        other_article = build_article(feed=self.feed, title="Stranger read this")
        build_read_state(user=self.stranger, article=other_article, is_read=True)

        response = self.client.get(self.url_for("archived"))

        self.assertNotContains(response, other_article.title)

    def test_preferences_are_per_reader(self) -> None:
        self.client.post(self.url_for("preferences"), {"theme": "dark"})

        self.assertEqual(UserPreference.objects.get(user=self.user).theme, "dark")
        self.assertFalse(UserPreference.objects.filter(user=self.stranger).exists())
