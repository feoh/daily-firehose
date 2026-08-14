from django.db import migrations, models


def derive_sanitized_html(apps, schema_editor):
    """Backfill derived markup for issues stored before ingest-time sanitizing.

    The live sanitizer is imported deliberately: the derived column must hold
    exactly what the current policy produces, not a historical copy of it.
    """

    from feeds.services import sanitize_newsletter_html

    NewsletterIssue = apps.get_model("feeds", "NewsletterIssue")
    issues = NewsletterIssue.objects.exclude(html_body="").only("id", "html_body")
    updated = []
    for issue in issues.iterator(chunk_size=100):
        issue.sanitized_html = sanitize_newsletter_html(issue.html_body)
        updated.append(issue)
        if len(updated) >= 100:
            NewsletterIssue.objects.bulk_update(updated, ["sanitized_html"])
            updated.clear()
    if updated:
        NewsletterIssue.objects.bulk_update(updated, ["sanitized_html"])


def clear_sanitized_html(apps, schema_editor):
    apps.get_model("feeds", "NewsletterIssue").objects.update(sanitized_html="")


class Migration(migrations.Migration):
    dependencies = [
        ("feeds", "0010_jobrun"),
    ]

    operations = [
        migrations.AddField(
            model_name="newsletterissue",
            name="sanitized_html",
            field=models.TextField(blank=True),
        ),
        migrations.RunPython(derive_sanitized_html, clear_sanitized_html),
    ]
