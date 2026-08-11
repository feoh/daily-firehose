from __future__ import annotations

from typing import Any, cast

from django.test import TestCase, override_settings

from feeds.models import Article, Feed, SavedArticle

from .builders import (
    FrozenClock,
    build_article,
    build_feed,
    build_read_state,
    build_saved_article,
    build_user,
    frozen_time,
)

TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def model_id(model: Any) -> int:
    return cast(int, model.id)


@override_settings(STORAGES=TEST_STORAGES)
class StaticFilesTestCase(TestCase):
    """Request-test base using non-manifest static storage."""


class DigestTestCase(StaticFilesTestCase):
    user: Any
    feed: Feed
    unread_article: Article
    read_article: Article
    saved_article: Article
    saved_record: SavedArticle
    clock: FrozenClock

    def setUp(self) -> None:
        self.clock = self.enterContext(frozen_time())
        self.user = build_user()
        self.feed = build_feed()
        self.unread_article = build_article(
            feed=self.feed,
            title="Unread article",
            url="https://example.com/unread",
            guid="unread",
            published_at=self.clock.now,
        )
        self.read_article = build_article(
            feed=self.feed,
            title="Read article",
            url="https://example.com/read",
            guid="read",
            published_at=self.clock.now,
        )
        self.saved_article = build_article(
            feed=self.feed,
            title="Saved article",
            url="https://example.com/saved",
            guid="saved",
            published_at=self.clock.now,
        )
        build_read_state(user=self.user, article=self.read_article)
        self.saved_record = build_saved_article(
            user=self.user,
            article=self.saved_article,
            linkding_saved=True,
        )
        self.client.force_login(self.user)
