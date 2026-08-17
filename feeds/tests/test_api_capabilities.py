"""Token capabilities and non-replayable signed actions.

A bearer token used to be all-powerful and a signed action used to be a
permanent GET that anything fetching the URL could execute, repeatedly. These
tests hold the replacements to their security claims: capability, expiry,
single use, and binding to exactly one purpose and target.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ..models import ApiToken, ArticleReadState, BulkReadMarker, SavedArticle
from .support.base import StaticFilesTestCase, model_id
from .support.builders import (
    build_api_token,
    build_article,
    build_feed,
    build_user,
    signed_action_query,
    signed_action_url,
)

AGENT_SETTINGS = {
    "AGENT_LINK_SECRET": "test-secret",
    "AGENT_LINK_USERNAME": "signing-reader",
    "LINKDING_TOKEN": "",
}


class TokenCapabilityTests(StaticFilesTestCase):
    def setUp(self) -> None:
        self.user = build_user(username="capability-reader")
        self.feed = build_feed()
        self.article = build_article(feed=self.feed, title="Capability article")
        _, self.read_key = build_api_token(
            user=self.user, name="briefing", capabilities=[ApiToken.Capability.READ]
        )
        _, self.write_key = build_api_token(
            user=self.user, name="writer", capabilities=[ApiToken.Capability.WRITE]
        )

    def read_state_path(self) -> str:
        return reverse("api-article-read", args=[model_id(self.article)])

    def test_read_only_token_reads_but_cannot_change_anything(self) -> None:
        headers = {"authorization": f"Bearer {self.read_key}"}

        listing = self.client.get(reverse("api-articles"), headers=headers)
        self.assertEqual(listing.status_code, 200)

        for path, body in (
            (self.read_state_path(), {"is_read": True}),
            (reverse("api-mark-period-read"), {"scope": "day"}),
            (reverse("api-feeds"), {"feed_url": "https://new.example/feed.xml"}),
        ):
            with self.subTest(path=path):
                response = self.client.post(
                    path, body, content_type="application/json", headers=headers
                )

                self.assertEqual(response.status_code, 403)
                self.assertEqual(
                    response.json()["error"]["code"], "insufficient_capability"
                )
        self.assertFalse(ArticleReadState.objects.exists())
        self.assertFalse(BulkReadMarker.objects.exists())

    def test_write_only_token_changes_state_but_cannot_read(self) -> None:
        headers = {"authorization": f"Bearer {self.write_key}"}

        mutation = self.client.post(
            self.read_state_path(),
            {"is_read": True},
            content_type="application/json",
            headers=headers,
        )
        self.assertEqual(mutation.status_code, 200)

        listing = self.client.get(reverse("api-articles"), headers=headers)

        self.assertEqual(listing.status_code, 403)
        self.assertEqual(listing.json()["error"]["code"], "insufficient_capability")

    def test_capability_is_checked_after_authentication_and_after_method(self) -> None:
        # Precedence is deliberate: an unauthenticated caller must not be able to
        # tell a capability apart from a bad token, and an unsupported method is
        # answered before either.
        unsupported = self.client.delete(
            reverse("api-mark-period-read"),
            headers={"authorization": f"Bearer {self.read_key}"},
        )
        self.assertEqual(unsupported.status_code, 405)

        anonymous = self.client.post(
            self.read_state_path(), {"is_read": True}, content_type="application/json"
        )
        self.assertEqual(anonymous.status_code, 401)

        forbidden = self.client.post(
            self.read_state_path(),
            {"is_read": True},
            content_type="application/json",
            headers={"authorization": f"Bearer {self.read_key}"},
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_a_token_cannot_be_created_without_a_known_capability(self) -> None:
        # An explicitly empty list must fail rather than fall back to the legacy
        # default, which would widen a token its creator meant to narrow.
        for capabilities in ([], ["admin"], ["read", "read"]):
            with (
                self.subTest(capabilities=capabilities),
                self.assertRaises(ValidationError),
            ):
                ApiToken.create_token(
                    user=self.user,
                    name=f"bad-{capabilities}",
                    capabilities=capabilities,
                )
        self.assertEqual(ApiToken.objects.filter(name__startswith="bad-").count(), 0)


class LegacyTokenCapabilityMigrationTests(TransactionTestCase):
    """Tokens that predate capabilities keep working."""

    @staticmethod
    def _restore_latest_migrations() -> None:
        """Return the shared test database to the newest migration.

        This test rewinds real schema, which every later test in the run then
        inherits. Targeting the leaf node rather than a pinned name means adding
        a migration cannot silently strand the suite one migration behind.
        """

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes("feeds"))

    def test_migration_grants_preexisting_tokens_the_scope_they_already_had(
        self,
    ) -> None:
        self.addCleanup(self._restore_latest_migrations)
        executor = MigrationExecutor(connection)
        executor.migrate([("feeds", "0012_read_state_query_indexes")])
        executor.loader.build_graph()
        historical = executor.loader.project_state(
            [("feeds", "0012_read_state_query_indexes")]
        ).apps
        user = build_user(username="legacy-token-owner")
        HistoricalToken = historical.get_model("feeds", "ApiToken")
        HistoricalToken.objects.create(
            user_id=model_id(user),
            name="legacy",
            key_hash="a" * 64,
            prefix="legacy",
        )

        executor = MigrationExecutor(connection)
        executor.migrate([("feeds", "0013_api_token_capabilities_and_signed_nonce")])

        token = ApiToken.objects.get(name="legacy")
        self.assertEqual(token.capabilities, ["read", "write"])
        self.assertTrue(token.allows(ApiToken.Capability.READ))
        self.assertTrue(token.allows(ApiToken.Capability.WRITE))


@override_settings(**AGENT_SETTINGS)
class SignedActionSecurityTests(StaticFilesTestCase):
    def setUp(self) -> None:
        self.user = build_user(username="signing-reader")
        self.feed = build_feed()
        self.article = build_article(feed=self.feed, title="Signed article")
        self.article_id = model_id(self.article)

    def save_url(self, **overrides: Any) -> str:
        query = signed_action_query(
            purpose="save-and-go", target=str(self.article_id), **overrides
        )
        return signed_action_url(
            reverse("api-article-save-and-go", args=[self.article_id]), **query
        )

    def period_url(self, **overrides: Any) -> str:
        query = signed_action_query(
            purpose="mark-period-read", target="day", **overrides
        )
        return signed_action_url(
            reverse("api-mark-period-read-and-go"), scope="day", **query
        )

    def test_a_signed_action_cannot_be_replayed(self) -> None:
        url = self.save_url()

        first = self.client.post(url)
        replay = self.client.post(url)

        self.assertEqual(first.status_code, 302)
        self.assertEqual(replay.status_code, 409)
        self.assertEqual(replay.json()["error"]["code"], "conflict")
        self.assertEqual(SavedArticle.objects.count(), 1)

    def test_a_replayed_period_action_does_not_advance_the_marker_again(self) -> None:
        url = self.period_url()

        self.assertEqual(self.client.post(url).status_code, 302)
        marked_at = BulkReadMarker.objects.get().marked_read_at

        replay = self.client.post(url)

        self.assertEqual(replay.status_code, 409)
        self.assertEqual(BulkReadMarker.objects.get().marked_read_at, marked_at)

    def test_an_expired_signed_action_is_refused(self) -> None:
        expired = int((timezone.now() - timedelta(seconds=1)).timestamp())

        response = self.client.post(self.save_url(expires=expired))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "expired")
        self.assertFalse(SavedArticle.objects.exists())

    def test_an_action_outliving_the_permitted_lifetime_is_refused(self) -> None:
        # Without this bound a caller could sign a request valid for years,
        # which is the permanent-link defect wearing an expiry field.
        far_future = int(
            (timezone.now() + timedelta(days=365)).timestamp()
        )

        response = self.client.post(self.save_url(expires=far_future))

        self.assertEqual(response.status_code, 403)
        self.assertFalse(SavedArticle.objects.exists())

    def test_tampering_with_any_signed_field_is_refused(self) -> None:
        other = build_article(feed=self.feed, title="Other article", guid="other")
        signed = signed_action_query(
            purpose="save-and-go", target=str(self.article_id)
        )
        cases = {
            "target": signed_action_url(
                reverse("api-article-save-and-go", args=[model_id(other)]), **signed
            ),
            "expires": self.save_url() + "&ignored=1",
            "nonce": signed_action_url(
                reverse("api-article-save-and-go", args=[self.article_id]),
                **{**signed, "nonce": "substituted-nonce-value"},
            ),
            "signature": signed_action_url(
                reverse("api-article-save-and-go", args=[self.article_id]),
                **{**signed, "sig": "0" * 64},
            ),
            "purpose": signed_action_url(
                reverse("api-mark-period-read-and-go"), scope="day", **signed
            ),
        }

        for field, url in cases.items():
            with self.subTest(field=field):
                response = self.client.post(url)

                self.assertIn(response.status_code, {400, 403})
        self.assertFalse(SavedArticle.objects.exists())
        self.assertFalse(BulkReadMarker.objects.exists())

    def test_a_signed_action_carries_no_session_so_csrf_does_not_apply(self) -> None:
        # The test client does not enforce CSRF by default, so without this the
        # move from GET to POST would pass here and 403 in production.
        client = Client(enforce_csrf_checks=True)

        response = client.post(self.save_url())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(SavedArticle.objects.count(), 1)

    def test_fetching_a_signed_action_cannot_change_state(self) -> None:
        # The defect this replaces: a scanner, prefetcher, or mail client that
        # merely fetched the URL performed the mutation.
        for url in (self.save_url(), self.period_url()):
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 405)
        self.assertFalse(SavedArticle.objects.exists())
        self.assertFalse(BulkReadMarker.objects.exists())
