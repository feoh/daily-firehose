from django.contrib import admin

from .models import (
    ApiToken,
    Article,
    ArticleReadState,
    BulkReadMarker,
    Category,
    Feed,
    NewsletterIssue,
    SavedArticle,
    UserPreference,
)


@admin.register(ApiToken)
class ApiTokenAdmin(admin.ModelAdmin):
    list_display = ["name", "user", "prefix", "is_active", "created_at", "last_used_at"]
    list_filter = ["is_active", "created_at", "last_used_at"]
    search_fields = ["name", "user__username", "prefix"]
    readonly_fields = ["key_hash", "prefix", "created_at", "last_used_at"]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "created_at"]
    prepopulated_fields = {"slug": ["name"]}
    search_fields = ["name"]


@admin.register(Feed)
class FeedAdmin(admin.ModelAdmin):
    list_display = ["title", "category", "feed_url", "is_active", "last_fetched_at"]
    list_filter = ["is_active", "category"]
    search_fields = ["title", "feed_url", "site_url"]


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ["title", "feed", "published_at", "fetched_at"]
    list_filter = ["feed", "published_at"]
    search_fields = ["title", "url", "guid", "summary"]
    date_hierarchy = "published_at"


@admin.register(NewsletterIssue)
class NewsletterIssueAdmin(admin.ModelAdmin):
    list_display = ["subject", "from_email", "to_email", "received_at", "created_at"]
    list_filter = ["received_at", "created_at"]
    search_fields = ["subject", "message_id", "from_email", "to_email", "text_body"]
    readonly_fields = ["public_id", "created_at", "updated_at"]
    date_hierarchy = "received_at"


@admin.register(SavedArticle)
class SavedArticleAdmin(admin.ModelAdmin):
    list_display = ["title", "user", "feed", "category", "linkding_saved", "saved_at"]
    list_filter = ["linkding_saved", "category", "feed", "saved_at"]
    search_fields = ["title", "url", "user__username", "feed__title", "category__name"]
    date_hierarchy = "saved_at"


@admin.register(ArticleReadState)
class ArticleReadStateAdmin(admin.ModelAdmin):
    list_display = ["user", "article", "is_read", "updated_at"]
    list_filter = ["is_read", "updated_at"]
    search_fields = ["user__username", "article__title"]


@admin.register(BulkReadMarker)
class BulkReadMarkerAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "scope",
        "feed",
        "period_start",
        "period_end",
        "marked_read_at",
    ]
    list_filter = ["scope", "marked_read_at"]
    search_fields = ["user__username", "feed__title"]


@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):
    list_display = ["user", "theme", "compact"]
    list_filter = ["theme", "compact"]
    search_fields = ["user__username"]
