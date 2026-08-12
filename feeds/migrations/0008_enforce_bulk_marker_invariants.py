from django.conf import settings
from django.db import migrations, models


def audit_bulk_read_markers(apps, schema_editor):
    BulkReadMarker = apps.get_model("feeds", "BulkReadMarker")
    invalid_ids: list[int] = []
    logical_keys: dict[tuple[object, ...], int] = {}
    duplicate_ids: list[int] = []

    database_alias = schema_editor.connection.alias
    for marker in (
        BulkReadMarker.objects.using(database_alias).order_by("pk").iterator()
    ):
        if marker.scope == "feed":
            valid = (
                marker.feed_id is not None
                and marker.period_start is None
                and marker.period_end is None
            )
            key = (marker.user_id, marker.scope, marker.feed_id)
        elif marker.scope in {"day", "week", "month"}:
            valid = (
                marker.feed_id is None
                and marker.period_start is not None
                and marker.period_end is not None
                and marker.period_start <= marker.period_end
            )
            key = (
                marker.user_id,
                marker.scope,
                marker.period_start,
                marker.period_end,
            )
        else:
            valid = False
            key = (marker.pk,)

        if not valid:
            invalid_ids.append(marker.pk)
        elif key in logical_keys:
            duplicate_ids.extend((logical_keys[key], marker.pk))
        else:
            logical_keys[key] = marker.pk

    if invalid_ids or duplicate_ids:
        invalid_display = ", ".join(map(str, invalid_ids[:20])) or "none"
        duplicate_display = (
            ", ".join(map(str, sorted(set(duplicate_ids))[:20])) or "none"
        )
        raise RuntimeError(
            "BulkReadMarker constraint migration cannot continue: existing user "
            f"state violates the new invariants (invalid row IDs: {invalid_display}; "
            f"duplicate row IDs: {duplicate_display}). Repair these rows explicitly "
            "and rerun the migration; no marker rows were modified."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("feeds", "0007_feed_consecutive_failures_feed_last_attempt_at_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(audit_bulk_read_markers, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="bulkreadmarker",
            name="unique_bulk_read_marker",
        ),
        migrations.AddConstraint(
            model_name="bulkreadmarker",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("feed__isnull", False),
                        ("period_end__isnull", True),
                        ("period_start__isnull", True),
                        ("scope", "feed"),
                    ),
                    models.Q(
                        ("feed__isnull", True),
                        ("period_end__isnull", False),
                        ("period_start__isnull", False),
                        ("period_start__lte", models.F("period_end")),
                        ("scope__in", ("day", "week", "month")),
                    ),
                    _connector="OR",
                ),
                name="bulk_marker_valid_scope_shape",
            ),
        ),
        migrations.AddConstraint(
            model_name="bulkreadmarker",
            constraint=models.UniqueConstraint(
                condition=models.Q(("scope", "feed")),
                fields=("user", "scope", "feed"),
                name="unique_bulk_feed_marker",
            ),
        ),
        migrations.AddConstraint(
            model_name="bulkreadmarker",
            constraint=models.UniqueConstraint(
                condition=models.Q(("scope__in", ("day", "week", "month"))),
                fields=("user", "scope", "period_start", "period_end"),
                name="unique_bulk_period_marker",
            ),
        ),
    ]
