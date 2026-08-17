"""Recoverable Linkding delivery: state machine, retry, and reconciliation."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO
from typing import Any
from unittest.mock import Mock, patch

import requests
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from ..models import LinkdingDelivery, SavedArticle
from ..services import (
    canonical_bookmark_url,
    deliver_pending_saved_articles,
    find_linkding_bookmark,
    save_article,
)
from .support.base import model_id
from .support.builders import build_article, build_feed, build_user
from .support.http_responses import (
    configure_linkding_lookup,
    configure_linkding_response,
    configure_request_timeout,
)

DELIVERY_SETTINGS: dict[str, Any] = {
    "LINKDING_URL": "https://linkding.example.com",
    "LINKDING_TOKEN": "delivery-test-token",
}


@override_settings(**DELIVERY_SETTINGS)
class LinkdingDeliveryStateMachineTests(TestCase):
    def setUp(self) -> None:
        self.user = build_user(username="delivery-owner")
        self.feed = build_feed()
        self.article = build_article(
            feed=self.feed, title="Delivered", url="https://example.com/delivered"
        )

    def save(self) -> SavedArticle:
        return save_article(
            user=self.user,
            article=self.article,
            base_url="https://linkding.example.com",
            token="delivery-test-token",
        )

    @patch("feeds.services.requests.post")
    def test_successful_save_records_a_delivered_bookmark(
        self, mock_post: Mock
    ) -> None:
        configure_linkding_response(mock_post, article=self.article, id=4321)

        saved = self.save()

        delivery = saved.delivery
        self.assertEqual(delivery.state, LinkdingDelivery.State.SUCCEEDED)
        self.assertEqual(delivery.attempts, 1)
        self.assertEqual(delivery.bookmark_id, "4321")
        self.assertIsNotNone(delivery.delivered_at)
        self.assertIsNone(delivery.next_attempt_at)
        self.assertEqual(delivery.error_class, "")
        self.assertEqual(delivery.error_message, "")

    @patch("feeds.services.requests.post")
    def test_transient_failure_leaves_the_bookmark_owed_with_backoff(
        self, mock_post: Mock
    ) -> None:
        configure_request_timeout(mock_post)

        saved = self.save()

        delivery = saved.delivery
        self.assertEqual(delivery.state, LinkdingDelivery.State.TRANSIENT_FAILED)
        self.assertEqual(delivery.error_class, LinkdingDelivery.ErrorClass.TIMEOUT)
        self.assertEqual(delivery.attempts, 1)
        scheduled = delivery.next_attempt_at
        assert scheduled is not None
        self.assertGreater(scheduled, timezone.now())
        self.assertIn(delivery.state, LinkdingDelivery.RETRYABLE_STATES)

    @patch("feeds.services.requests.post")
    def test_rejected_request_is_permanent_and_never_retried(
        self, mock_post: Mock
    ) -> None:
        configure_linkding_response(mock_post, article=self.article, status_code=400)

        saved = self.save()

        delivery = saved.delivery
        self.assertEqual(delivery.state, LinkdingDelivery.State.PERMANENT_FAILED)
        self.assertEqual(delivery.error_class, LinkdingDelivery.ErrorClass.CLIENT_ERROR)
        self.assertIsNone(delivery.next_attempt_at)
        self.assertEqual(
            deliver_pending_saved_articles(
                base_url="https://linkding.example.com", token="t"
            ).attempted,
            0,
        )

    @patch("feeds.services.requests.post")
    def test_local_save_and_its_owed_delivery_survive_remote_failure(
        self, mock_post: Mock
    ) -> None:
        configure_request_timeout(mock_post)

        saved = self.save()

        saved.refresh_from_db()
        self.assertFalse(saved.linkding_saved)
        self.assertIn("timed out", saved.linkding_error)
        self.assertTrue(
            SavedArticle.objects.filter(user=self.user, article=self.article).exists()
        )
        self.assertEqual(LinkdingDelivery.objects.count(), 1)


@override_settings(**DELIVERY_SETTINGS)
class LinkdingErrorClassificationTests(TestCase):
    def setUp(self) -> None:
        self.user = build_user(username="classifier")
        self.article = build_article(
            feed=build_feed(), url="https://example.com/classified"
        )

    def save(self, token: str = "delivery-test-token") -> LinkdingDelivery:
        return save_article(
            user=self.user,
            article=self.article,
            base_url="https://linkding.example.com",
            token=token,
        ).delivery

    @patch("feeds.services.requests.post")
    def test_each_failure_mode_records_its_class_and_retryability(
        self, mock_post: Mock
    ) -> None:
        classes = LinkdingDelivery.ErrorClass
        states = LinkdingDelivery.State
        cases: list[tuple[str, Any, str, str]] = [
            (
                "timeout",
                requests.Timeout("timed out"),
                classes.TIMEOUT,
                states.TRANSIENT_FAILED,
            ),
            (
                "connection",
                requests.ConnectionError("refused"),
                classes.CONNECTION,
                states.TRANSIENT_FAILED,
            ),
            ("rate limit", 429, classes.RATE_LIMITED, states.TRANSIENT_FAILED),
            ("unauthorized", 401, classes.AUTH, states.TRANSIENT_FAILED),
            ("forbidden", 403, classes.AUTH, states.TRANSIENT_FAILED),
            ("server error", 503, classes.SERVER_ERROR, states.TRANSIENT_FAILED),
            ("not found", 404, classes.CLIENT_ERROR, states.PERMANENT_FAILED),
        ]
        for label, failure, expected_class, expected_state in cases:
            with self.subTest(failure=label):
                if isinstance(failure, int):
                    configure_linkding_response(
                        mock_post, article=self.article, status_code=failure
                    )
                else:
                    configure_request_timeout(mock_post)
                    mock_post.side_effect = failure

                delivery = self.save()

                self.assertEqual(delivery.error_class, expected_class)
                self.assertEqual(delivery.state, expected_state)

    @patch("feeds.services.requests.post")
    def test_a_bookmark_for_another_url_is_a_permanent_mismatch(
        self, mock_post: Mock
    ) -> None:
        configure_linkding_response(
            mock_post, article=self.article, url="https://example.com/somewhere-else"
        )

        delivery = self.save()

        self.assertEqual(delivery.error_class, LinkdingDelivery.ErrorClass.URL_MISMATCH)
        self.assertEqual(delivery.state, LinkdingDelivery.State.PERMANENT_FAILED)

    @patch("feeds.services.requests.post")
    def test_missing_credentials_stay_retryable_so_configuring_them_recovers(
        self, mock_post: Mock
    ) -> None:
        delivery = self.save(token="")

        self.assertEqual(
            delivery.error_class, LinkdingDelivery.ErrorClass.NOT_CONFIGURED
        )
        self.assertEqual(delivery.state, LinkdingDelivery.State.TRANSIENT_FAILED)
        mock_post.assert_not_called()


class LinkdingUrlCanonicalizationTests(SimpleTestCase):
    def test_canonical_comparison_ignores_case_and_root_slash_only(self) -> None:
        self.assertEqual(
            canonical_bookmark_url("HTTPS://Example.COM/a"),
            canonical_bookmark_url("https://example.com/a"),
        )
        self.assertEqual(
            canonical_bookmark_url("https://example.com"),
            canonical_bookmark_url("https://example.com/"),
        )
        self.assertNotEqual(
            canonical_bookmark_url("https://example.com/a"),
            canonical_bookmark_url("https://example.com/b"),
        )
        self.assertNotEqual(
            canonical_bookmark_url("https://example.com/a?page=1"),
            canonical_bookmark_url("https://example.com/a?page=2"),
        )


@override_settings(**DELIVERY_SETTINGS)
class LinkdingDeliveryDrainTests(TestCase):
    def setUp(self) -> None:
        self.user = build_user(username="drain-owner")
        self.article = build_article(
            feed=build_feed(), title="Owed", url="https://example.com/owed"
        )

    def save(self) -> SavedArticle:
        return save_article(
            user=self.user,
            article=self.article,
            base_url="https://linkding.example.com",
            token="delivery-test-token",
        )

    def drain(self) -> Any:
        return deliver_pending_saved_articles(
            base_url="https://linkding.example.com", token="delivery-test-token"
        )

    def make_due(self, delivery: LinkdingDelivery) -> None:
        LinkdingDelivery.objects.filter(pk=delivery.pk).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1)
        )

    @patch("feeds.services.requests.get")
    @patch("feeds.services.requests.post")
    def test_a_transient_failure_is_delivered_by_a_later_drain(
        self, mock_post: Mock, mock_get: Mock
    ) -> None:
        configure_request_timeout(mock_post)
        saved = self.save()
        self.assertFalse(saved.linkding_saved)

        configure_linkding_lookup(mock_get, bookmark=None)
        configure_linkding_response(mock_post, article=self.article, id=99)
        self.make_due(saved.delivery)

        tally = self.drain()

        saved.refresh_from_db()
        delivery = saved.delivery
        self.assertEqual((tally.attempted, tally.succeeded), (1, 1))
        self.assertEqual(delivery.state, LinkdingDelivery.State.SUCCEEDED)
        self.assertEqual(delivery.attempts, 2)
        self.assertEqual(delivery.bookmark_id, "99")
        self.assertTrue(saved.linkding_saved)
        self.assertEqual(saved.linkding_error, "")

    @patch("feeds.services.requests.post")
    def test_a_delivery_that_is_not_due_yet_is_left_alone(
        self, mock_post: Mock
    ) -> None:
        configure_request_timeout(mock_post)
        self.save()
        mock_post.reset_mock()

        tally = self.drain()

        self.assertEqual(tally.attempted, 0)
        mock_post.assert_not_called()

    @patch("feeds.services.requests.get")
    @patch("feeds.services.requests.post")
    def test_retry_budget_is_bounded_then_recorded_as_permanent(
        self, mock_post: Mock, mock_get: Mock
    ) -> None:
        configure_request_timeout(mock_post)
        configure_linkding_lookup(mock_get, bookmark=None)
        saved = self.save()

        with override_settings(LINKDING_MAX_DELIVERY_ATTEMPTS=3):
            for _ in range(5):
                delivery = LinkdingDelivery.objects.get(saved_article=saved)
                if delivery.state not in LinkdingDelivery.RETRYABLE_STATES:
                    break
                self.make_due(delivery)
                self.drain()

        delivery = LinkdingDelivery.objects.get(saved_article=saved)
        self.assertEqual(delivery.state, LinkdingDelivery.State.PERMANENT_FAILED)
        self.assertEqual(delivery.attempts, 3)
        self.assertIsNone(delivery.next_attempt_at)

    @patch("feeds.services.requests.post")
    def test_claiming_a_due_delivery_stops_a_second_drainer_repeating_it(
        self, mock_post: Mock
    ) -> None:
        configure_request_timeout(mock_post)
        saved = self.save()
        self.make_due(saved.delivery)
        stale = LinkdingDelivery.objects.get(pk=saved.delivery.pk)

        self.drain()
        mock_post.reset_mock()

        # A racing drainer holding the pre-claim row must lose the compare-and-set.
        from ..services import _claim_delivery

        self.assertFalse(_claim_delivery(stale, now=timezone.now()))
        mock_post.assert_not_called()

    @patch("feeds.services.requests.post")
    def test_unsaving_cancels_an_owed_delivery(self, mock_post: Mock) -> None:
        configure_request_timeout(mock_post)
        saved = self.save()
        self.make_due(saved.delivery)

        SavedArticle.objects.filter(pk=saved.pk).delete()
        mock_post.reset_mock()

        tally = self.drain()

        self.assertEqual(LinkdingDelivery.objects.count(), 0)
        self.assertEqual(tally.attempted, 0)
        mock_post.assert_not_called()

    @patch("feeds.services.requests.post")
    def test_re_saving_restarts_the_budget_of_a_permanently_failed_delivery(
        self, mock_post: Mock
    ) -> None:
        configure_linkding_response(mock_post, article=self.article, status_code=400)
        saved = self.save()
        self.assertEqual(saved.delivery.state, LinkdingDelivery.State.PERMANENT_FAILED)

        configure_linkding_response(mock_post, article=self.article, id=7)
        resaved = self.save()

        self.assertEqual(model_id(resaved), model_id(saved))
        self.assertEqual(resaved.delivery.state, LinkdingDelivery.State.SUCCEEDED)
        self.assertEqual(resaved.delivery.attempts, 1)


@override_settings(**DELIVERY_SETTINGS)
class LinkdingReconciliationTests(TestCase):
    """The timeout-after-remote-create case, which used to duplicate bookmarks."""

    def setUp(self) -> None:
        self.user = build_user(username="reconciler")
        self.article = build_article(
            feed=build_feed(), title="Twice", url="https://example.com/twice"
        )

    def save(self) -> SavedArticle:
        return save_article(
            user=self.user,
            article=self.article,
            base_url="https://linkding.example.com",
            token="delivery-test-token",
        )

    @patch("feeds.services.requests.get")
    @patch("feeds.services.requests.post")
    def test_a_retry_adopts_the_bookmark_the_timed_out_attempt_created(
        self, mock_post: Mock, mock_get: Mock
    ) -> None:
        configure_request_timeout(mock_post)
        saved = self.save()

        # The remote create actually succeeded; only the response was lost.
        configure_linkding_lookup(
            mock_get, bookmark={"id": 555, "url": self.article.url}
        )
        mock_post.reset_mock()
        LinkdingDelivery.objects.filter(pk=saved.delivery.pk).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1)
        )

        deliver_pending_saved_articles(
            base_url="https://linkding.example.com", token="delivery-test-token"
        )

        delivery = LinkdingDelivery.objects.get(saved_article=saved)
        self.assertEqual(delivery.state, LinkdingDelivery.State.SUCCEEDED)
        self.assertEqual(delivery.bookmark_id, "555")
        mock_post.assert_not_called()

    @patch("feeds.services.requests.get")
    @patch("feeds.services.requests.post")
    def test_the_first_attempt_never_pays_for_a_lookup(
        self, mock_post: Mock, mock_get: Mock
    ) -> None:
        configure_linkding_response(mock_post, article=self.article)

        self.save()

        mock_get.assert_not_called()
        mock_post.assert_called_once()

    @patch("feeds.services.requests.get")
    def test_lookup_degrades_to_no_match_rather_than_failing(
        self, mock_get: Mock
    ) -> None:
        cases: list[tuple[str, Any]] = [
            (
                "endpoint missing",
                lambda: configure_linkding_lookup(
                    mock_get, bookmark=None, status_code=404
                ),
            ),
            ("transport error", lambda: configure_request_timeout(mock_get)),
            ("no bookmark", lambda: configure_linkding_lookup(mock_get, bookmark=None)),
            (
                "different url",
                lambda: configure_linkding_lookup(
                    mock_get, bookmark={"id": 1, "url": "https://example.com/other"}
                ),
            ),
        ]
        for label, configure in cases:
            with self.subTest(case=label):
                configure()
                self.assertIsNone(
                    find_linkding_bookmark(
                        base_url="https://linkding.example.com",
                        token="delivery-test-token",
                        url=self.article.url,
                    )
                )

    @patch("feeds.services.requests.get")
    def test_lookup_matches_a_bookmark_whose_url_differs_only_canonically(
        self, mock_get: Mock
    ) -> None:
        configure_linkding_lookup(
            mock_get, bookmark={"id": 8, "url": "HTTPS://Example.com/twice"}
        )

        found = find_linkding_bookmark(
            base_url="https://linkding.example.com",
            token="delivery-test-token",
            url=self.article.url,
        )

        assert found is not None
        self.assertEqual(found["id"], 8)


class LinkdingDeliveryInvariantTests(TestCase):
    def setUp(self) -> None:
        self.saved = SavedArticle.objects.create(
            user=build_user(username="invariant-owner"),
            article=build_article(feed=build_feed()),
            url="https://example.com/invariant",
            title="Invariant",
        )

    def test_database_rejects_incoherent_delivery_states(self) -> None:
        cases: list[tuple[str, dict[str, Any]]] = [
            (
                "succeeded without a delivery time",
                {"state": "succeeded", "delivered_at": None, "next_attempt_at": None},
            ),
            (
                "succeeded but still scheduled",
                {
                    "state": "succeeded",
                    "delivered_at": timezone.now(),
                    "next_attempt_at": timezone.now(),
                },
            ),
            (
                "owed with nothing scheduled",
                {"state": "queued", "next_attempt_at": None},
            ),
            (
                "failed without a classification",
                {
                    "state": "permanent_failed",
                    "next_attempt_at": None,
                    "error_class": "",
                },
            ),
        ]
        for label, fields in cases:
            with self.subTest(case=label):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    LinkdingDelivery.objects.create(
                        saved_article=self.saved,
                        url=self.saved.url,
                        **fields,
                    )


@override_settings(**DELIVERY_SETTINGS)
class DeliverSavedArticlesCommandTests(TestCase):
    def setUp(self) -> None:
        self.user = build_user(username="operator")
        self.article = build_article(
            feed=build_feed(), url="https://example.com/commanded"
        )

    @patch("feeds.services.requests.get")
    @patch("feeds.services.requests.post")
    def test_requeue_failed_lets_an_operator_retry_a_dropped_bookmark(
        self, mock_post: Mock, mock_get: Mock
    ) -> None:
        configure_linkding_response(mock_post, article=self.article, status_code=400)
        saved = save_article(
            user=self.user,
            article=self.article,
            base_url="https://linkding.example.com",
            token="delivery-test-token",
        )
        self.assertEqual(saved.delivery.state, LinkdingDelivery.State.PERMANENT_FAILED)

        configure_linkding_lookup(mock_get, bookmark=None)
        configure_linkding_response(mock_post, article=self.article, id=11)
        output = StringIO()

        call_command("deliver_saved_articles", "--requeue-failed", stdout=output)

        delivery = LinkdingDelivery.objects.get(saved_article=saved)
        self.assertEqual(delivery.state, LinkdingDelivery.State.SUCCEEDED)
        self.assertEqual(delivery.attempts, 1)
        self.assertIn("requeued 1", output.getvalue())
        self.assertIn("succeeded=1", output.getvalue())

    @patch("feeds.services.requests.post")
    def test_draining_nothing_reports_an_empty_tally(self, mock_post: Mock) -> None:
        output = StringIO()

        call_command("deliver_saved_articles", stdout=output)

        self.assertIn("attempted=0", output.getvalue())
        mock_post.assert_not_called()
