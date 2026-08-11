from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from ..models import Article, SavedArticle
from ..services import LINKDING_TOREAD_TAG, save_to_linkding
from .support.base import (
    DigestTestCase,
    model_id,
)
from .support.http_responses import (
    configure_linkding_response,
)


class ArticleActionTests(DigestTestCase):
    @patch("feeds.services.save_to_linkding")
    def test_ajax_save_returns_inline_message_payload(
        self, mock_save_to_linkding
    ) -> None:
        response = self.client.post(
            reverse("save-article", args=[model_id(self.unread_article)]),
            headers={"x-requested-with": "XMLHttpRequest"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "message": "Saved “Unread article” to Linkding and Daily Firehose.",
                "level": "success",
                "remove": True,
                "article": {
                    "id": model_id(self.unread_article),
                    "title": self.unread_article.title,
                    "url": self.unread_article.url,
                },
            },
        )
        mock_save_to_linkding.assert_called_once()

    def test_save_forms_include_matching_article_verification_fields(self) -> None:
        other_article = Article.objects.create(
            feed=self.feed,
            title="Another unread article",
            url="https://example.com/another-unread",
            guid="another-unread",
            published_at=timezone.now() + timedelta(minutes=1),
        )

        response = self.client.get(reverse("today"))
        content = response.content.decode()

        for article in [self.unread_article, other_article]:
            article_id = model_id(article)
            heading_index = content.index(f'id="article-{article_id}"')
            article_start = content.rindex("<article", 0, heading_index)
            article_end = content.index("</article>", heading_index)
            card_html = content[article_start:article_end]

            self.assertIn(f'data-article-id="{article_id}"', card_html)
            self.assertIn(f'data-article-url="{article.url}"', card_html)
            self.assertIn(
                f'action="{reverse("save-article", args=[article_id])}"', card_html
            )
            self.assertIn(f'name="article_id" value="{article_id}"', card_html)
            self.assertIn(f'name="article_url" value="{article.url}"', card_html)

    @override_settings(LINKDING_TOKEN="x")
    @patch("feeds.services.requests.post")
    def test_save_article_view_sends_requested_card_article_to_linkding(
        self, mock_post
    ) -> None:
        other_article = Article.objects.create(
            feed=self.feed,
            title="Another unread article",
            url="https://example.com/another-unread",
            guid="another-unread",
            published_at=timezone.now() + timedelta(minutes=1),
        )
        other_article_id = model_id(other_article)
        configure_linkding_response(mock_post, article=other_article)

        response = self.client.post(
            reverse("save-article", args=[other_article_id]),
            {
                "article_id": str(other_article_id),
                "article_url": other_article.url,
            },
            headers={"x-requested-with": "XMLHttpRequest"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            SavedArticle.objects.filter(
                user=self.user, article=self.unread_article
            ).exists()
        )
        saved = SavedArticle.objects.get(user=self.user, article=other_article)
        self.assertEqual(saved.url, other_article.url)
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["url"], other_article.url)
        self.assertEqual(payload["title"], other_article.title)

    @patch("feeds.services.save_to_linkding")
    def test_save_article_rejects_mismatched_posted_article_id(
        self, mock_save_to_linkding
    ) -> None:
        other_article = Article.objects.create(
            feed=self.feed,
            title="Another unread article",
            url="https://example.com/another-unread",
            guid="another-unread",
            published_at=timezone.now() + timedelta(minutes=1),
        )

        response = self.client.post(
            reverse("save-article", args=[model_id(self.unread_article)]),
            {"article_id": str(model_id(other_article))},
            headers={"x-requested-with": "XMLHttpRequest"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {
                "message": "Article verification failed. Please refresh and try again.",
                "level": "error",
                "remove": False,
            },
        )
        self.assertFalse(
            SavedArticle.objects.filter(
                user=self.user, article=self.unread_article
            ).exists()
        )
        self.assertFalse(
            SavedArticle.objects.filter(user=self.user, article=other_article).exists()
        )
        mock_save_to_linkding.assert_not_called()

    @patch("feeds.services.save_to_linkding")
    def test_save_article_rejects_mismatched_posted_article_url(
        self, mock_save_to_linkding
    ) -> None:
        response = self.client.post(
            reverse("save-article", args=[model_id(self.unread_article)]),
            {
                "article_id": str(model_id(self.unread_article)),
                "article_url": "https://example.com/a-different-article",
            },
            headers={"x-requested-with": "XMLHttpRequest"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            SavedArticle.objects.filter(
                user=self.user, article=self.unread_article
            ).exists()
        )
        mock_save_to_linkding.assert_not_called()

    @override_settings(LINKDING_TOKEN="x")
    @patch("feeds.services.requests.post")
    def test_linkding_response_mismatch_keeps_card_visible(self, mock_post) -> None:
        configure_linkding_response(
            mock_post,
            article=self.unread_article,
            url="https://example.com/a-different-article",
            title="A different article",
        )

        response = self.client.post(
            reverse("save-article", args=[model_id(self.unread_article)]),
            {
                "article_id": str(model_id(self.unread_article)),
                "article_url": self.unread_article.url,
            },
            headers={"x-requested-with": "XMLHttpRequest"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["level"], "warning")
        self.assertFalse(response.json()["remove"])
        saved = SavedArticle.objects.get(user=self.user, article=self.unread_article)
        self.assertFalse(saved.linkding_saved)
        self.assertIn("different bookmark URL", saved.linkding_error)

    def test_article_action_script_blocks_repeat_and_duplicate_saves(self) -> None:
        script = (
            Path(__file__).resolve().parents[2] / "static/js/article-actions.js"
        ).read_text()

        self.assertIn('event.repeat && ["s", "m"].includes(event.key)', script)
        self.assertIn('form.dataset.actionPending === "true"', script)

    @patch("feeds.services.requests.post")
    def test_linkding_save_uses_article_url_and_toread_tag(self, mock_post) -> None:
        configure_linkding_response(mock_post, article=self.unread_article)

        save_to_linkding(
            base_url="https://linkding.example.com",
            token="x",
            article=self.unread_article,
        )

        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["url"], "https://example.com/unread")
        self.assertEqual(payload["tag_names"], [LINKDING_TOREAD_TAG])

    @patch("feeds.services.requests.post")
    def test_linkding_save_omits_comments_only_summary(self, mock_post) -> None:
        configure_linkding_response(mock_post, article=self.unread_article)
        self.unread_article.summary = (
            '<p><a href="https://lobste.rs/s/vkoa7r/story">Comments</a></p>'
        )

        save_to_linkding(
            base_url="https://linkding.example.com",
            token="x",
            article=self.unread_article,
        )

        self.assertEqual(mock_post.call_args.kwargs["json"]["description"], "")

    @patch("feeds.services.requests.post")
    def test_linkding_save_rejects_a_different_returned_url(self, mock_post) -> None:
        configure_linkding_response(
            mock_post,
            article=self.unread_article,
            url="https://example.com/a-different-article",
            title="A different article",
        )

        with self.assertRaisesMessage(
            ValueError, "Linkding returned a different bookmark URL"
        ):
            save_to_linkding(
                base_url="https://linkding.example.com",
                token="x",
                article=self.unread_article,
            )
