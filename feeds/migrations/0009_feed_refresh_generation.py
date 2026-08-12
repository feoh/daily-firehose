from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("feeds", "0008_enforce_bulk_marker_invariants"),
    ]

    operations = [
        migrations.AddField(
            model_name="feed",
            name="refresh_generation",
            field=models.PositiveBigIntegerField(default=0),
        ),
    ]
