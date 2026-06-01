from importlib import import_module

from django.urls import path

from . import views

api = import_module("feeds.api")

urlpatterns = [
    path("", views.today, name="today"),
    path("week/", views.week, name="week"),
    path("month/", views.month, name="month"),
    path("feeds/", views.feed_list, name="feeds"),
    path("feeds/<int:feed_id>/", views.feed_detail, name="feed-detail"),
    path("feeds/<int:feed_id>/mark-read/", views.mark_feed_read, name="mark-feed-read"),
    path("opml/import/", views.opml_import, name="opml-import"),
    path("opml/export/", views.opml_export, name="opml-export"),
    path("preferences/", views.preferences, name="preferences"),
    path("refresh/", views.refresh_feeds, name="refresh-feeds"),
    path("articles/<int:article_id>/mark/", views.mark_article, name="mark-article"),
    path(
        "articles/<int:article_id>/save/", views.save_article_view, name="save-article"
    ),
    path("mark-period-read/", views.mark_period_read, name="mark-period-read"),
    path("api/digest/today.json", views.digest_json, name="digest-json"),
    path("api/v1/briefing/morning/", api.morning_briefing, name="api-morning-briefing"),
    path("api/v1/articles/", api.article_list, name="api-articles"),
    path(
        "api/v1/articles/<int:article_id>/read/",
        api.article_read_state,
        name="api-article-read",
    ),
    path(
        "api/v1/articles/<int:article_id>/saved/",
        api.article_saved_state,
        name="api-article-saved",
    ),
    path(
        "api/v1/articles/<int:article_id>/save-and-go/",
        api.article_save_and_go,
        name="api-article-save-and-go",
    ),
    path(
        "api/v1/mark-period-read/",
        api.mark_period_read_api,
        name="api-mark-period-read",
    ),
    path("api/v1/feeds/", api.feed_collection, name="api-feeds"),
    path("api/v1/feeds/<int:feed_id>/", api.feed_detail_api, name="api-feed-detail"),
    path(
        "api/v1/feeds/<int:feed_id>/mark-read/",
        api.mark_feed_read_api,
        name="api-feed-mark-read",
    ),
    path("api/v1/categories/", api.category_collection, name="api-categories"),
    path("api/v1/preferences/", api.preferences_api, name="api-preferences"),
    path("api/v1/refresh/", api.refresh_feeds_api, name="api-refresh"),
]
