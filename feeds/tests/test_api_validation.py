from __future__ import annotations

import hmac
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import override_settings
from django.urls import reverse

from ..models import (
    ArticleReadState,
    BulkReadMarker,
    Category,
    Feed,
    SavedArticle,
    UserPreference,
)
from .support.base import StaticFilesTestCase, model_id
from .support.builders import build_api_token, build_article, build_feed, build_user


class ApiValidationTests(StaticFilesTestCase):
    def setUp(self) -> None:
        self.user = build_user(username="validation-reader")
        _, self.key = build_api_token(user=self.user)
        self.feed = build_feed(title="Existing feed")
        self.article = build_article(feed=self.feed, title="Existing article")

    def auth_headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.key}"}

    def assert_error(
        self, response, *, status: int, code: str, message: str | None = None
    ) -> dict:
        self.assertEqual(response.status_code, status)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], code)
        if message is not None:
            self.assertEqual(payload["error"]["message"], message)
        return payload["error"]

    def raw_json(self, method: str, route: str, body: str, *args: int):
        return self.client.generic(
            method,
            reverse(route, args=args),
            data=body,
            content_type="application/json",
            headers=self.auth_headers(),
        )

    def test_malformed_and_non_object_json_use_bad_request_envelope(self) -> None:
        url = reverse("api-article-read", args=[model_id(self.article)])
        for body, message in (
            ("{", "Request body must be valid UTF-8 JSON."),
            ("[]", "Request body must be a JSON object."),
            ("null", "Request body must be a JSON object."),
        ):
            with self.subTest(body=body):
                response = self.client.generic(
                    "POST",
                    url,
                    data=body,
                    content_type="application/json",
                    headers=self.auth_headers(),
                )
                self.assert_error(
                    response, status=400, code="bad_request", message=message
                )
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                response = self.client.generic(
                    "POST",
                    url,
                    data=f'{{"is_read": {constant}}}',
                    content_type="application/json",
                    headers=self.auth_headers(),
                )
                self.assert_error(response, status=400, code="bad_request")
        duplicate = self.client.generic(
            "POST",
            url,
            data='{"is_read": true, "is_read": false}',
            content_type="application/json",
            headers=self.auth_headers(),
        )
        self.assert_error(duplicate, status=400, code="bad_request")
        huge_integer = "9" * 5000
        response = self.client.generic(
            "POST",
            url,
            data=f'{{"is_read": {huge_integer}}}',
            content_type="application/json",
            headers=self.auth_headers(),
        )
        self.assert_error(response, status=400, code="bad_request")
        response = self.client.generic(
            "POST",
            url,
            data=b'{"is_read": "\xff"}',
            content_type="application/json",
            headers=self.auth_headers(),
        )
        self.assert_error(response, status=400, code="bad_request")
        self.assertFalse(ArticleReadState.objects.exists())

    def test_nonempty_json_requires_json_media_type_and_accepts_json_suffix(
        self,
    ) -> None:
        url = reverse("api-article-read", args=[model_id(self.article)])
        for content_type in ("text/plain", "application/x-www-form-urlencoded"):
            with self.subTest(content_type=content_type):
                response = self.client.generic(
                    "POST",
                    url,
                    data='{"is_read": true}',
                    content_type=content_type,
                    headers=self.auth_headers(),
                )
                self.assert_error(
                    response,
                    status=400,
                    code="bad_request",
                    message=(
                        "Content-Type must be application/json or application/*+json."
                    ),
                )
        self.assertFalse(ArticleReadState.objects.exists())
        response = self.client.generic(
            "POST",
            url,
            data='{"is_read": true}',
            content_type="application/vnd.daily-firehose+json",
            headers=self.auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ArticleReadState.objects.get().is_read)

    def test_method_and_auth_errors_use_stable_envelopes(self) -> None:
        response = self.client.put(
            reverse("api-preferences"),
            data={},
            content_type="application/json",
            headers=self.auth_headers(),
        )
        self.assert_error(response, status=405, code="method_not_allowed")
        response = self.client.get(reverse("api-preferences"))
        self.assert_error(response, status=401, code="unauthorized")
        self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")

    def test_each_bearer_endpoint_rejects_unsupported_methods_as_json(self) -> None:
        cases = (
            ("POST", "api-morning-briefing", ()),
            ("POST", "api-articles", ()),
            ("GET", "api-article-read", (model_id(self.article),)),
            ("GET", "api-article-saved", (model_id(self.article),)),
            ("GET", "api-mark-period-read", ()),
            ("PUT", "api-feeds", ()),
            ("POST", "api-feed-detail", (model_id(self.feed),)),
            ("GET", "api-feed-mark-read", (model_id(self.feed),)),
            ("PATCH", "api-categories", ()),
            ("POST", "api-preferences", ()),
            ("GET", "api-refresh", ()),
        )
        for method, route, args in cases:
            with self.subTest(route=route, method=method):
                response = self.client.generic(
                    method,
                    reverse(route, args=args),
                    headers=self.auth_headers(),
                )
                self.assert_error(response, status=405, code="method_not_allowed")

    def test_article_query_validates_period_dates_booleans_and_feed(self) -> None:
        cases = (
            ({"period": "year"}, 422, "validation_error"),
            ({"start": "2026-01-01"}, 400, "bad_request"),
            ({"start": "not-a-date", "end": "2026-01-02"}, 422, "validation_error"),
            ({"start": "20260101", "end": "2026-01-02"}, 422, "validation_error"),
            ({"start": "2026-01-03", "end": "2026-01-02"}, 422, "validation_error"),
            ({"include_read": "1"}, 400, "bad_request"),
            ({"include_saved": "TRUE"}, 400, "bad_request"),
            ({"feed_id": "word"}, 400, "bad_request"),
            ({"feed_id": "0"}, 422, "validation_error"),
            ({"feed_id": "9" * 400}, 422, "validation_error"),
            ({"feed_id": "999999"}, 404, "not_found"),
            ({"unknown": "x"}, 400, "bad_request"),
        )
        for query, status, code in cases:
            with self.subTest(query=query):
                response = self.client.get(
                    reverse("api-articles"), query, headers=self.auth_headers()
                )
                self.assert_error(response, status=status, code=code)
        repeated = self.client.get(
            f"{reverse('api-articles')}?include_read=true&include_read=false",
            headers=self.auth_headers(),
        )
        self.assert_error(repeated, status=400, code="bad_request")

    def test_read_state_rejects_wrong_null_and_unknown_values_without_mutation(
        self,
    ) -> None:
        for data in (
            {"is_read": None},
            {"is_read": 1},
            {"is_read": "true"},
            {"unexpected": True},
        ):
            with self.subTest(data=data):
                response = self.client.post(
                    reverse("api-article-read", args=[model_id(self.article)]),
                    data,
                    content_type="application/json",
                    headers=self.auth_headers(),
                )
                self.assert_error(response, status=400, code="bad_request")
                self.assertFalse(ArticleReadState.objects.exists())

    def test_all_resource_endpoints_return_json_not_found(self) -> None:
        cases = (
            ("post", "api-article-read", (999999,), {"is_read": True}),
            ("post", "api-article-saved", (999999,), {"is_saved": True}),
            ("get", "api-feed-detail", (999999,), None),
            ("post", "api-feed-mark-read", (999999,), None),
        )
        for method, route, args, data in cases:
            with self.subTest(route=route):
                kwargs: dict[str, object] = {"headers": self.auth_headers()}
                if data is not None:
                    kwargs.update(data=data, content_type="application/json")
                response = getattr(self.client, method)(
                    reverse(route, args=args), **kwargs
                )
                self.assert_error(response, status=404, code="not_found")
        response = self.client.post(
            reverse("api-article-read", args=[10**100]),
            {"is_read": True},
            content_type="application/json",
            headers=self.auth_headers(),
        )
        self.assert_error(response, status=422, code="validation_error")

    def test_saved_state_validates_alias_notes_and_score_before_writes(self) -> None:
        cases = (
            ({"is_saved": None}, 400),
            ({"is_saved": True, "saved": True}, 400),
            ({"is_saved": True, "notes": None}, 400),
            ({"is_saved": True, "interest_score": "5"}, 400),
            ({"is_saved": True, "interest_score": True}, 400),
            ({"is_saved": True, "interest_score": -0.1}, 422),
            ({"is_saved": True, "interest_score": 5.1}, 422),
            ({"is_saved": False, "notes": "invalid with unsave"}, 400),
            ({"unknown": True}, 400),
        )
        for data, status in cases:
            with self.subTest(data=data):
                response = self.client.post(
                    reverse("api-article-saved", args=[model_id(self.article)]),
                    data,
                    content_type="application/json",
                    headers=self.auth_headers(),
                )
                self.assert_error(
                    response,
                    status=status,
                    code="bad_request" if status == 400 else "validation_error",
                )
                self.assertFalse(SavedArticle.objects.exists())

        for numeric_value in ("9" * 400, "1e999"):
            with self.subTest(numeric_value=numeric_value[:20]):
                response = self.raw_json(
                    "POST",
                    "api-article-saved",
                    f'{{"is_saved": true, "interest_score": {numeric_value}}}',
                    model_id(self.article),
                )
                self.assert_error(response, status=422, code="validation_error")
                self.assertFalse(SavedArticle.objects.exists())

    def test_nullable_interest_score_and_documented_range_are_accepted(self) -> None:
        for score in (0, 2.5, 5, None):
            SavedArticle.objects.all().delete()
            with self.subTest(score=score):
                response = self.client.post(
                    reverse("api-article-saved", args=[model_id(self.article)]),
                    {"is_saved": True, "interest_score": score},
                    content_type="application/json",
                    headers=self.auth_headers(),
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    SavedArticle.objects.get().interest_score,
                    None if score is None else float(score),
                )

    def test_period_marking_validates_scope_and_complete_ordered_dates(self) -> None:
        cases: tuple[tuple[dict[str, object], int], ...] = (
            ({"scope": None}, 400),
            ({"scope": "feed"}, 422),
            ({"scope": "day", "period_start": "2026-01-01"}, 400),
            ({"period_start": 1, "period_end": "2026-01-02"}, 400),
            ({"period_start": "bad", "period_end": "2026-01-02"}, 422),
            ({"period_start": "2026-01-03", "period_end": "2026-01-02"}, 422),
            ({"unknown": "x"}, 400),
        )
        for data, status in cases:
            with self.subTest(data=data):
                response = self.client.post(
                    reverse("api-mark-period-read"),
                    data,
                    content_type="application/json",
                    headers=self.auth_headers(),
                )
                self.assert_error(
                    response,
                    status=status,
                    code="bad_request" if status == 400 else "validation_error",
                )
                self.assertFalse(BulkReadMarker.objects.exists())

    @patch("feeds.api.discover_feed_metadata")
    def test_feed_creation_validates_before_discovery_or_write(self, discover) -> None:
        cases: tuple[tuple[dict[str, object], int], ...] = (
            ({}, 400),
            ({"feed_url": None}, 400),
            ({"feed_url": "ftp://example.com/feed", "title": "x"}, 422),
            (
                {
                    "feed_url": "https://user:secret@example.com/feed",
                    "title": "x",
                },
                422,
            ),
            ({"feed_url": "https://example.com/feed", "title": 1}, 400),
            ({"feed_url": "https://example.com/feed", "title": "x" * 256}, 422),
            (
                {
                    "feed_url": "https://example.com/feed",
                    "site_url": "file:///tmp/x",
                    "title": "x",
                },
                422,
            ),
            (
                {
                    "feed_url": "https://example.com/feed",
                    "category_id": "1",
                    "title": "x",
                },
                400,
            ),
            (
                {
                    "feed_url": "https://example.com/feed",
                    "category_id": 10**100,
                    "title": "x",
                },
                422,
            ),
            (
                {
                    "feed_url": "https://example.com/feed",
                    "category_id": 999999,
                    "title": "x",
                },
                404,
            ),
            (
                {"feed_url": "https://example.com/feed", "is_active": 1, "title": "x"},
                400,
            ),
            (
                {"feed_url": "https://example.com/feed", "unknown": "x", "title": "x"},
                400,
            ),
        )
        baseline = Feed.objects.count()
        for data, status in cases:
            with self.subTest(data=data):
                response = self.client.post(
                    reverse("api-feeds"),
                    data,
                    content_type="application/json",
                    headers=self.auth_headers(),
                )
                self.assert_error(
                    response,
                    status=status,
                    code={
                        400: "bad_request",
                        404: "not_found",
                        422: "validation_error",
                    }[status],
                )
                self.assertEqual(Feed.objects.count(), baseline)
        discover.assert_not_called()

    def test_feed_patch_validation_and_conflicts_do_not_mutate(self) -> None:
        other = build_feed(feed_url="https://example.com/other.xml")
        baseline = (self.feed.title, self.feed.feed_url, self.feed.is_active)
        cases: tuple[tuple[dict[str, object], int, str], ...] = (
            ({"is_active": "false"}, 400, "bad_request"),
            ({"feed_url": "ftp://example.com/feed"}, 422, "validation_error"),
            (
                {"feed_url": "https://user:secret@example.com/feed"},
                422,
                "validation_error",
            ),
            ({"title": "x" * 256}, 422, "validation_error"),
            ({"feed_url": other.feed_url}, 409, "conflict"),
            ({"category_id": 999999}, 404, "not_found"),
            ({"unknown": True}, 400, "bad_request"),
        )
        for data, status, code in cases:
            with self.subTest(data=data):
                response = self.client.patch(
                    reverse("api-feed-detail", args=[model_id(self.feed)]),
                    data,
                    content_type="application/json",
                    headers=self.auth_headers(),
                )
                self.assert_error(response, status=status, code=code)
                self.feed.refresh_from_db()
                self.assertEqual(
                    (self.feed.title, self.feed.feed_url, self.feed.is_active), baseline
                )

    def test_category_validation_and_conflict_envelopes(self) -> None:
        baseline = Category.objects.count()
        cases: tuple[tuple[dict[str, object], int, str], ...] = (
            ({}, 400, "bad_request"),
            ({"name": None, "slug": "x"}, 400, "bad_request"),
            ({"name": "x", "slug": "not valid"}, 422, "validation_error"),
            ({"name": "x" * 121, "slug": "long"}, 422, "validation_error"),
            ({"name": "x", "slug": "x", "unknown": True}, 400, "bad_request"),
        )
        for data, status, code in cases:
            with self.subTest(data=data):
                response = self.client.post(
                    reverse("api-categories"),
                    data,
                    content_type="application/json",
                    headers=self.auth_headers(),
                )
                error = self.assert_error(response, status=status, code=code)
                if status == 422:
                    self.assertIn("fields", error)
                self.assertEqual(Category.objects.count(), baseline)

        Category.objects.create(name="Existing", slug="existing")
        response = self.client.post(
            reverse("api-categories"),
            {"name": "Different", "slug": "existing"},
            content_type="application/json",
            headers=self.auth_headers(),
        )
        self.assert_error(response, status=409, code="conflict")

    def test_preference_validation_does_not_partially_mutate(self) -> None:
        preferences = UserPreference.objects.create(user=self.user)
        cases = (
            ({"compact": None}, 400, "bad_request"),
            ({"focus_mode": 0}, 400, "bad_request"),
            ({"theme": None}, 400, "bad_request"),
            ({"theme": "unknown"}, 422, "validation_error"),
            ({"theme": "dark", "compact": "true"}, 400, "bad_request"),
            ({"unknown": True}, 400, "bad_request"),
        )
        for data, status, code in cases:
            with self.subTest(data=data):
                response = self.client.patch(
                    reverse("api-preferences"),
                    data,
                    content_type="application/json",
                    headers=self.auth_headers(),
                )
                self.assert_error(response, status=status, code=code)
                preferences.refresh_from_db()
                self.assertEqual(
                    (preferences.theme, preferences.compact, preferences.focus_mode),
                    ("system", False, False),
                )

    def test_invalid_preference_patch_does_not_create_default_row(self) -> None:
        response = self.client.patch(
            reverse("api-preferences"),
            {"compact": "false"},
            content_type="application/json",
            headers=self.auth_headers(),
        )
        self.assert_error(response, status=400, code="bad_request")
        self.assertFalse(UserPreference.objects.filter(user=self.user).exists())

    @patch("feeds.commands.refresh_active_feeds")
    def test_no_input_endpoints_reject_bodies_of_any_media_before_mutation(
        self, refresh
    ) -> None:
        for route, args in (
            ("api-refresh", ()),
            ("api-feed-mark-read", (model_id(self.feed),)),
        ):
            for body, content_type in (
                ("{}", "application/json"),
                ("x", "text/plain"),
            ):
                with self.subTest(route=route, content_type=content_type):
                    response = self.client.generic(
                        "POST",
                        reverse(route, args=args),
                        data=body,
                        content_type=content_type,
                        headers=self.auth_headers(),
                    )
                    self.assert_error(response, status=400, code="bad_request")
        for route, args in (
            ("api-morning-briefing", ()),
            ("api-articles", ()),
            ("api-feeds", ()),
            ("api-feed-detail", (model_id(self.feed),)),
            ("api-categories", ()),
            ("api-preferences", ()),
        ):
            with self.subTest(route=route, method="GET"):
                response = self.client.generic(
                    "GET",
                    reverse(route, args=args),
                    data="x",
                    content_type="text/plain",
                    headers=self.auth_headers(),
                )
                self.assert_error(response, status=400, code="bad_request")
        refresh.assert_not_called()
        self.assertFalse(BulkReadMarker.objects.exists())

        saved = SavedArticle.objects.create(
            user=self.user,
            article=self.article,
            url=self.article.url,
            title=self.article.title,
            feed=self.feed,
        )
        response = self.client.delete(
            reverse("api-article-saved", args=[model_id(self.article)]),
            {"unexpected": True},
            content_type="application/json",
            headers=self.auth_headers(),
        )
        self.assert_error(response, status=400, code="bad_request")
        self.assertTrue(SavedArticle.objects.filter(pk=saved.pk).exists())
        response = self.client.generic(
            "DELETE",
            reverse("api-article-saved", args=[model_id(self.article)]),
            data="x",
            content_type="text/plain",
            headers=self.auth_headers(),
        )
        self.assert_error(response, status=400, code="bad_request")
        self.assertTrue(SavedArticle.objects.filter(pk=saved.pk).exists())

        response = self.client.delete(
            reverse("api-feed-detail", args=[model_id(self.feed)]),
            {"unexpected": True},
            content_type="application/json",
            headers=self.auth_headers(),
        )
        self.assert_error(response, status=400, code="bad_request")
        self.feed.refresh_from_db()
        self.assertTrue(self.feed.is_active)

    @patch(
        "feeds.api.save_article",
        side_effect=IntegrityError("private concurrent uniqueness detail"),
    )
    def test_saved_article_concurrent_unique_race_maps_to_conflict(self, save) -> None:
        response = self.client.post(
            reverse("api-article-saved", args=[model_id(self.article)]),
            {"is_saved": True, "interest_score": 4},
            content_type="application/json",
            headers=self.auth_headers(),
        )
        self.assert_error(
            response,
            status=409,
            code="conflict",
            message="The requested write conflicts with existing data.",
        )
        self.assertNotContains(
            response, "private concurrent uniqueness detail", status_code=409
        )
        self.assertFalse(SavedArticle.objects.exists())
        save.assert_called_once()

    def test_duplicate_bulk_marker_state_maps_to_conflict_and_rolls_back(self) -> None:
        message = "Stored read-marker state conflicts with this request."
        with patch(
            "feeds.api.BulkReadMarker.objects.update_or_create",
            side_effect=BulkReadMarker.MultipleObjectsReturned,
        ):
            response = self.client.post(
                reverse("api-mark-period-read"),
                {"scope": "day"},
                content_type="application/json",
                headers=self.auth_headers(),
            )
        self.assert_error(response, status=409, code="conflict", message=message)
        self.assertFalse(ArticleReadState.objects.exists())

        with patch(
            "feeds.api.BulkReadMarker.objects.update_or_create",
            side_effect=BulkReadMarker.MultipleObjectsReturned,
        ):
            response = self.client.post(
                reverse("api-feed-mark-read", args=[model_id(self.feed)]),
                headers=self.auth_headers(),
            )
        self.assert_error(response, status=409, code="conflict", message=message)
        self.assertFalse(ArticleReadState.objects.exists())

    def test_get_and_no_input_endpoints_reject_unknown_queries(self) -> None:
        routes = (
            ("api-morning-briefing", ()),
            ("api-feeds", ()),
            ("api-feed-detail", (model_id(self.feed),)),
            ("api-categories", ()),
            ("api-preferences", ()),
        )
        for route, args in routes:
            with self.subTest(route=route):
                response = self.client.get(
                    reverse(route, args=args),
                    {"unknown": "x"},
                    headers=self.auth_headers(),
                )
                self.assert_error(response, status=400, code="bad_request")

        for route, args in (
            ("api-refresh", ()),
            ("api-feed-mark-read", (model_id(self.feed),)),
        ):
            response = self.client.post(
                f"{reverse(route, args=args)}?unknown=x",
                headers=self.auth_headers(),
            )
            self.assert_error(response, status=400, code="bad_request")
        self.assertFalse(BulkReadMarker.objects.exists())

    @override_settings(POSTMARK_INBOUND_SECRET="inbound-secret")
    @patch("feeds.api.import_postmark_newsletter")
    def test_postmark_rejects_malformed_and_non_object_json(
        self, import_newsletter
    ) -> None:
        url = reverse("postmark-inbound", args=["inbound-secret"])
        method_response = self.client.get(url)
        self.assert_error(method_response, status=405, code="method_not_allowed")
        for body in ("{", "[]"):
            response = self.client.generic(
                "POST", url, data=body, content_type="application/json"
            )
            self.assert_error(response, status=400, code="bad_request")
        for query in ("unknown=x", "unknown=x&unknown=y"):
            response = self.client.generic(
                "POST",
                f"{url}?{query}",
                data="{}",
                content_type="application/json",
            )
            self.assert_error(response, status=400, code="bad_request")
        response = self.client.generic(
            "POST", url, data="{}", content_type="text/plain"
        )
        self.assert_error(response, status=400, code="bad_request")
        invalid_secret = reverse("postmark-inbound", args=["wrong-secret"])
        response = self.client.generic(
            "POST",
            f"{invalid_secret}?unknown=x",
            data="{",
            content_type="text/plain",
        )
        self.assert_error(response, status=403, code="forbidden")
        import_newsletter.assert_not_called()

    @override_settings(POSTMARK_INBOUND_SECRET="inbound-secret")
    def test_postmark_maps_validation_and_integrity_errors_without_details(
        self,
    ) -> None:
        url = reverse("postmark-inbound", args=["inbound-secret"])
        with patch(
            "feeds.api.import_postmark_newsletter",
            side_effect=ValidationError({"payload": ["Rejected payload."]}),
        ):
            response = self.client.post(url, {}, content_type="application/json")
        error = self.assert_error(
            response,
            status=422,
            code="validation_error",
            message="Request fields failed validation.",
        )
        self.assertEqual(error["fields"], {"payload": ["Rejected payload."]})

        with patch(
            "feeds.api.import_postmark_newsletter",
            side_effect=IntegrityError("private database detail"),
        ):
            response = self.client.post(url, {}, content_type="application/json")
        self.assert_error(
            response,
            status=409,
            code="conflict",
            message="Inbound newsletter conflicts with existing data.",
        )
        self.assertNotContains(response, "private database detail", status_code=409)

    @override_settings(
        AGENT_LINK_SECRET="test-secret", AGENT_LINK_USERNAME="validation-reader"
    )
    def test_signed_action_methods_and_missing_resource_use_json_errors(self) -> None:
        article_id = 999999
        signature = hmac.new(
            b"test-secret", f"save-and-go:{article_id}".encode(), "sha256"
        ).hexdigest()
        response = self.client.get(
            reverse("api-article-save-and-go", args=[article_id]), {"sig": signature}
        )
        self.assert_error(
            response,
            status=404,
            code="not_found",
            message="Article not found.",
        )
        response = self.client.post(
            reverse("api-article-save-and-go", args=[model_id(self.article)])
        )
        self.assert_error(response, status=405, code="method_not_allowed")
        response = self.client.post(reverse("api-mark-period-read-and-go"))
        self.assert_error(response, status=405, code="method_not_allowed")

        response = self.client.generic(
            "GET",
            f"{reverse('api-article-save-and-go', args=[model_id(self.article)])}?sig=invalid&unknown=x",
            data="x",
            content_type="text/plain",
        )
        self.assert_error(response, status=403, code="forbidden")
        response = self.client.get(
            reverse("api-mark-period-read-and-go"),
            {"scope": "century", "sig": "invalid", "unknown": "x"},
        )
        self.assert_error(response, status=403, code="forbidden")

        excessive_query = "&".join("unknown=x" for _ in range(1_100))
        response = self.client.get(
            f"{reverse('api-article-save-and-go', args=[model_id(self.article)])}?{excessive_query}"
        )
        self.assert_error(response, status=403, code="forbidden")

        invalid_scope_signature = hmac.new(
            b"test-secret", b"mark-period-read:century", "sha256"
        ).hexdigest()
        response = self.client.get(
            reverse("api-mark-period-read-and-go"),
            {"scope": "century", "sig": invalid_scope_signature},
        )
        error = self.assert_error(response, status=422, code="validation_error")
        self.assertIn("scope", error["fields"])

        save_signature = hmac.new(
            b"test-secret",
            f"save-and-go:{model_id(self.article)}".encode(),
            "sha256",
        ).hexdigest()
        response = self.client.get(
            reverse("api-article-save-and-go", args=[model_id(self.article)]),
            {"sig": save_signature, "unknown": "x"},
        )
        self.assert_error(response, status=400, code="bad_request")
        response = self.client.get(
            f"{reverse('api-article-save-and-go', args=[model_id(self.article)])}?sig={save_signature}&{excessive_query}"
        )
        self.assert_error(response, status=400, code="bad_request")
        response = self.client.generic(
            "GET",
            f"{reverse('api-article-save-and-go', args=[model_id(self.article)])}?sig={save_signature}",
            data="x",
            content_type="text/plain",
        )
        self.assert_error(response, status=400, code="bad_request")
        self.assertFalse(SavedArticle.objects.exists())
        with override_settings(AGENT_LINK_USERNAME="missing-user"):
            response = self.client.get(
                reverse("api-article-save-and-go", args=[model_id(self.article)]),
                {"sig": save_signature},
            )
        self.assert_error(response, status=503, code="not_configured")
