from django.db import migrations, models


LEGACY_DARK_THEMES = (
    "catppuccin-mocha",
    "tokyo-night",
    "dracula",
    "gruvbox-dark",
    "one-dark",
    "nord",
    "solarized-dark",
    "rose-pine",
    "kanagawa",
)


def consolidate_legacy_themes(apps, schema_editor):
    user_preference = apps.get_model("feeds", "UserPreference")
    user_preference.objects.filter(theme__in=LEGACY_DARK_THEMES).update(theme="dark")


class Migration(migrations.Migration):
    dependencies = [("feeds", "0014_linkding_delivery_state_machine")]

    operations = [
        migrations.RunPython(consolidate_legacy_themes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="userpreference",
            name="theme",
            field=models.CharField(
                choices=[
                    ("system", "Follow device"),
                    ("light", "Miura Paper"),
                    ("dark", "Miura Night"),
                ],
                default="system",
                max_length=32,
            ),
        ),
    ]
