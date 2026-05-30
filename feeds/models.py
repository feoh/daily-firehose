from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class Category(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.name


class Feed(models.Model):
    title = models.CharField(max_length=255)
    feed_url = models.URLField(unique=True)
    site_url = models.URLField(blank=True)
    description = models.TextField(blank=True)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, blank=True, null=True, related_name="feeds")
    is_active = models.BooleanField(default=True)
    last_fetched_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title", "feed_url"]

    def __str__(self) -> str:
        return self.title or self.feed_url


class Article(models.Model):
    feed = models.ForeignKey(Feed, on_delete=models.CASCADE, related_name="articles")
    title = models.CharField(max_length=500)
    url = models.URLField(max_length=1000)
    guid = models.CharField(max_length=1000)
    author = models.CharField(max_length=255, blank=True)
    summary = models.TextField(blank=True)
    published_at = models.DateTimeField(default=timezone.now)
    fetched_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at", "title"]
        constraints = [
            models.UniqueConstraint(fields=["feed", "guid"], name="unique_article_guid_per_feed"),
            models.UniqueConstraint(fields=["feed", "url"], name="unique_article_url_per_feed"),
        ]

    def __str__(self) -> str:
        return self.title


class SavedArticle(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_articles")
    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="saves")
    url = models.URLField(max_length=1000)
    title = models.CharField(max_length=500)
    feed = models.ForeignKey(Feed, on_delete=models.SET_NULL, blank=True, null=True)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, blank=True, null=True)
    linkding_saved = models.BooleanField(default=False)
    linkding_error = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    interest_score = models.FloatField(blank=True, null=True)
    saved_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-saved_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "article"], name="unique_saved_article")
        ]

    def __str__(self) -> str:
        return self.title


class ArticleReadState(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="read_states")
    is_read = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "article"], name="unique_article_read_state")
        ]

    def __str__(self) -> str:
        state = "read" if self.is_read else "unread"
        return f"{self.user} marked {self.article} {state}"


class ReadScope(models.TextChoices):
    DAY = "day", "Day"
    WEEK = "week", "Week"
    MONTH = "month", "Month"
    FEED = "feed", "Feed"


class BulkReadMarker(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    scope = models.CharField(max_length=10, choices=ReadScope.choices)
    feed = models.ForeignKey(Feed, on_delete=models.CASCADE, blank=True, null=True)
    period_start = models.DateField(blank=True, null=True)
    period_end = models.DateField(blank=True, null=True)
    marked_read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-marked_read_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "scope", "feed", "period_start", "period_end"],
                name="unique_bulk_read_marker",
            )
        ]

    def __str__(self) -> str:
        if self.scope == ReadScope.FEED and self.feed:
            return f"{self.user} marked {self.feed} read"
        return f"{self.user} marked {self.scope} {self.period_start}–{self.period_end} read"


class UserPreference(models.Model):
    class Theme(models.TextChoices):
        SYSTEM = "system", "Use system setting"
        LIGHT = "light", "Accessible light"
        DARK = "dark", "Accessible dark"
        CATPPUCCIN_MOCHA = "catppuccin-mocha", "Catppuccin Mocha"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="feed_preferences")
    theme = models.CharField(max_length=32, choices=Theme.choices, default=Theme.SYSTEM)
    compact = models.BooleanField(default=False)

    def __str__(self) -> str:
        return f"Preferences for {self.user}"
