from django.urls import path

from . import views

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
    path("articles/<int:article_id>/save/", views.save_article_view, name="save-article"),
    path("mark-period-read/", views.mark_period_read, name="mark-period-read"),
    path("api/digest/today.json", views.digest_json, name="digest-json"),
]
