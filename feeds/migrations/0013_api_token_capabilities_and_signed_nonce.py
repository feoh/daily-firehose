import django.utils.timezone
from django.db import migrations, models


def grant_legacy_capabilities(apps, schema_editor):
    """Map every preexisting token to the full scope it already had.

    Capabilities are new; the tokens are not. Narrowing one that an agent is
    already using would be an unannounced outage, so existing tokens keep the
    read and write access they have always had until they are reissued.
    """

    apps.get_model("feeds", "ApiToken").objects.update(capabilities=["read", "write"])


def clear_capabilities(apps, schema_editor):
    apps.get_model("feeds", "ApiToken").objects.update(capabilities=[])


class Migration(migrations.Migration):
    dependencies = [
        ("feeds", "0012_read_state_query_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="apitoken",
            name="capabilities",
            field=models.JSONField(default=list),
        ),
        migrations.RunPython(grant_legacy_capabilities, clear_capabilities),
        migrations.CreateModel(
            name="SignedActionNonce",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("nonce", models.CharField(max_length=64, unique=True)),
                ("purpose", models.CharField(max_length=64)),
                ("expires_at", models.DateTimeField()),
                ("used_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                "ordering": ["-used_at"],
                "indexes": [
                    models.Index(
                        fields=["expires_at"], name="feeds_signe_expires_87d9a7_idx"
                    )
                ],
            },
        ),
    ]
