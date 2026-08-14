import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml
from django.test import Client, SimpleTestCase, override_settings

BASE_DIR = Path(__file__).resolve().parents[2]
POSTGRES_VARIABLES = {
    "POSTGRES_DB",
    "POSTGRES_HOST",
    "POSTGRES_PASSWORD",
    "POSTGRES_PORT",
    "POSTGRES_USER",
}
CONFIGURATION_VARIABLES = {
    "DATABASE_URL",
    "DJANGO_ALLOWED_HOSTS",
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    "DJANGO_DEBUG",
    "DJANGO_ENV",
    "DJANGO_SECRET_KEY",
    "DJANGO_SETTINGS_MODULE",
    *POSTGRES_VARIABLES,
}
VALID_SECRET = "Prod-Test_Secret!With-Many-Unique-Characters_0123456789_abcdef"
VALID_DATABASE_PASSWORD = "Prod@pass/with%literal:reserved"
BAD_DATABASE_PASSWORD = "Bad-Database-Secret-Do-Not-Leak"
VALID_PRODUCTION_ENV = {
    "DJANGO_ENV": "production",
    "DJANGO_DEBUG": "false",
    "DJANGO_SECRET_KEY": VALID_SECRET,
    "DJANGO_ALLOWED_HOSTS": "reader.example.com",
    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://reader.example.com",
    "POSTGRES_DB": "daily_firehose",
    "POSTGRES_USER": "daily_firehose",
    "POSTGRES_PASSWORD": VALID_DATABASE_PASSWORD,
    "POSTGRES_HOST": "db",
    "POSTGRES_PORT": "5432",
}


class ProductionSettingsTests(SimpleTestCase):
    maxDiff = None

    def _run(
        self,
        command: list[str],
        *,
        overrides: dict[str, str | None] | None = None,
        production: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in CONFIGURATION_VARIABLES
        }
        if production:
            environment.update(VALID_PRODUCTION_ENV)
        environment["DJANGO_SETTINGS_MODULE"] = "daily_firehose.settings"
        for key, value in (overrides or {}).items():
            if value is None:
                environment.pop(key, None)
            else:
                environment[key] = value
        return subprocess.run(
            [sys.executable, *command],
            cwd=BASE_DIR,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

    def assert_fails_safely(
        self,
        overrides: dict[str, str | None],
        expected_setting: str,
    ) -> None:
        result = self._run(["-c", "import django; django.setup()"], overrides=overrides)
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn(expected_setting, output)
        for secret in (
            VALID_SECRET,
            VALID_DATABASE_PASSWORD,
            BAD_DATABASE_PASSWORD,
            overrides.get("DJANGO_SECRET_KEY"),
            overrides.get("POSTGRES_PASSWORD"),
        ):
            if secret:
                self.assertNotIn(secret, output)

    def test_bad_production_configuration_fails_closed_without_secret_values(
        self,
    ) -> None:
        cases: list[tuple[str, dict[str, str | None], str]] = [
            ("invalid environment", {"DJANGO_ENV": "staging"}, "DJANGO_ENV"),
            ("debug missing", {"DJANGO_DEBUG": None}, "DJANGO_DEBUG"),
            ("debug enabled", {"DJANGO_DEBUG": "true"}, "DJANGO_DEBUG"),
            ("debug invalid", {"DJANGO_DEBUG": "truthy"}, "DJANGO_DEBUG"),
            ("secret missing", {"DJANGO_SECRET_KEY": None}, "DJANGO_SECRET_KEY"),
            ("secret default", {"DJANGO_SECRET_KEY": "change-me"}, "DJANGO_SECRET_KEY"),
            (
                "secret weak",
                {"DJANGO_SECRET_KEY": "TOP-SECRET-weak"},
                "DJANGO_SECRET_KEY",
            ),
            ("hosts missing", {"DJANGO_ALLOWED_HOSTS": None}, "DJANGO_ALLOWED_HOSTS"),
            ("hosts wildcard", {"DJANGO_ALLOWED_HOSTS": "*"}, "DJANGO_ALLOWED_HOSTS"),
            (
                "hosts public IPv4",
                {"DJANGO_ALLOWED_HOSTS": "8.8.8.8"},
                "DJANGO_ALLOWED_HOSTS",
            ),
            (
                "hosts private IPv4",
                {"DJANGO_ALLOWED_HOSTS": "10.0.0.1"},
                "DJANGO_ALLOWED_HOSTS",
            ),
            (
                "hosts numeric IPv4 spelling",
                {"DJANGO_ALLOWED_HOSTS": "001.002.003.004"},
                "DJANGO_ALLOWED_HOSTS",
            ),
            (
                "hosts IPv6",
                {"DJANGO_ALLOWED_HOSTS": "[2001:4860:4860::8888]"},
                "DJANGO_ALLOWED_HOSTS",
            ),
            (
                "hosts consecutive dots",
                {"DJANGO_ALLOWED_HOSTS": "bad..example.com"},
                "DJANGO_ALLOWED_HOSTS",
            ),
            (
                "hosts leading hyphen",
                {"DJANGO_ALLOWED_HOSTS": "-bad.example.com"},
                "DJANGO_ALLOWED_HOSTS",
            ),
            (
                "hosts trailing hyphen",
                {"DJANGO_ALLOWED_HOSTS": "bad-.example.com"},
                "DJANGO_ALLOWED_HOSTS",
            ),
            (
                "hosts overlong label",
                {"DJANGO_ALLOWED_HOSTS": f"{'a' * 64}.example.com"},
                "DJANGO_ALLOWED_HOSTS",
            ),
            (
                "hosts underscore",
                {"DJANGO_ALLOWED_HOSTS": "bad_name.example.com"},
                "DJANGO_ALLOWED_HOSTS",
            ),
            (
                "hosts single label",
                {"DJANGO_ALLOWED_HOSTS": "internal"},
                "DJANGO_ALLOWED_HOSTS",
            ),
            (
                "csrf missing",
                {"DJANGO_CSRF_TRUSTED_ORIGINS": None},
                "DJANGO_CSRF_TRUSTED_ORIGINS",
            ),
            (
                "csrf not https",
                {"DJANGO_CSRF_TRUSTED_ORIGINS": "http://reader.example.com"},
                "DJANGO_CSRF_TRUSTED_ORIGINS",
            ),
            (
                "csrf host not allowed",
                {"DJANGO_CSRF_TRUSTED_ORIGINS": "https://other.example.com"},
                "DJANGO_CSRF_TRUSTED_ORIGINS",
            ),
            (
                "csrf empty port",
                {"DJANGO_CSRF_TRUSTED_ORIGINS": "https://reader.example.com:"},
                "DJANGO_CSRF_TRUSTED_ORIGINS",
            ),
            (
                "csrf explicit port",
                {"DJANGO_CSRF_TRUSTED_ORIGINS": "https://reader.example.com:443"},
                "DJANGO_CSRF_TRUSTED_ORIGINS",
            ),
            (
                "csrf path",
                {"DJANGO_CSRF_TRUSTED_ORIGINS": "https://reader.example.com/path"},
                "DJANGO_CSRF_TRUSTED_ORIGINS",
            ),
            (
                "database all fields missing",
                dict.fromkeys(POSTGRES_VARIABLES),
                "POSTGRES_DB",
            ),
            (
                "database missing password",
                {"POSTGRES_PASSWORD": None},
                "POSTGRES_PASSWORD",
            ),
            ("database missing host", {"POSTGRES_HOST": None}, "POSTGRES_HOST"),
            ("database invalid port", {"POSTGRES_PORT": "not-a-port"}, "POSTGRES_PORT"),
            (
                "database development password",
                {"POSTGRES_PASSWORD": "daily_firehose_dev_password"},
                "POSTGRES_PASSWORD",
            ),
            (
                "database URL sqlite",
                {"DATABASE_URL": "sqlite:///prod.sqlite3"},
                "DATABASE_URL",
            ),
            (
                "database URL development password",
                {
                    "DATABASE_URL": (
                        "postgresql://daily_firehose:daily_firehose_dev_password"
                        "@db:5432/daily_firehose"
                    )
                },
                "DATABASE_URL",
            ),
            (
                "database URL incomplete",
                {"DATABASE_URL": "postgresql://db/daily_firehose"},
                "DATABASE_URL",
            ),
            (
                "database URL malformed",
                {
                    "DATABASE_URL": (
                        f"postgresql://daily_firehose:{BAD_DATABASE_PASSWORD}"
                        "@db:notaport/daily_firehose"
                    )
                },
                "DATABASE_URL",
            ),
        ]

        for name, overrides, expected_setting in cases:
            with self.subTest(name=name):
                self.assert_fails_safely(overrides, expected_setting)

    def test_valid_production_settings_preserve_discrete_reserved_characters(
        self,
    ) -> None:
        code = f"""
from django.conf import settings
assert settings.DJANGO_ENV == "production"
assert settings.DEBUG is False
assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql"
assert settings.DATABASES["default"]["PASSWORD"] == {VALID_DATABASE_PASSWORD!r}
assert settings.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")
assert settings.SECURE_SSL_REDIRECT is True
assert settings.SESSION_COOKIE_SECURE is True
assert settings.CSRF_COOKIE_SECURE is True
assert settings.SESSION_COOKIE_HTTPONLY is True
assert settings.SECURE_CONTENT_TYPE_NOSNIFF is True
assert settings.SECURE_CROSS_ORIGIN_OPENER_POLICY == "same-origin"
assert settings.SECURE_REFERRER_POLICY == "same-origin"
assert settings.X_FRAME_OPTIONS == "DENY"
assert settings.SECURE_HSTS_SECONDS == 0
assert settings.SILENCED_SYSTEM_CHECKS == ["security.W004"]
"""
        result = self._run(["-c", code])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_valid_production_database_url_remains_supported(self) -> None:
        encoded_password = quote(VALID_DATABASE_PASSWORD, safe="")
        code = f"""
from django.conf import settings
assert settings.DATABASES["default"]["PASSWORD"] == {VALID_DATABASE_PASSWORD!r}
"""
        result = self._run(
            ["-c", code],
            overrides={
                "DATABASE_URL": (
                    f"postgresql://daily_firehose:{encoded_password}"
                    "@db:5432/daily_firehose"
                ),
                **dict.fromkeys(POSTGRES_VARIABLES),
            },
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_development_defaults_remain_zero_configuration(self) -> None:
        code = """
from django.conf import settings
assert settings.DJANGO_ENV == "development"
assert settings.DEBUG is True
assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
assert settings.ALLOWED_HOSTS == [
    "localhost",
    "127.0.0.1",
    "daily-firehose.reedfish-regulus.ts.net",
]
assert settings.SECURE_SSL_REDIRECT is False
assert settings.SESSION_COOKIE_SECURE is False
assert settings.CSRF_COOKIE_SECURE is False
"""
        result = self._run(["-c", code], production=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_development_discrete_postgres_preserves_reserved_characters(self) -> None:
        code = f"""
from django.conf import settings
assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql"
assert settings.DATABASES["default"]["PASSWORD"] == {VALID_DATABASE_PASSWORD!r}
"""
        result = self._run(
            ["-c", code],
            production=False,
            overrides={
                "DJANGO_ENV": "development",
                "POSTGRES_DB": "daily_firehose",
                "POSTGRES_USER": "daily_firehose",
                "POSTGRES_PASSWORD": VALID_DATABASE_PASSWORD,
                "POSTGRES_HOST": "db",
                "POSTGRES_PORT": "5432",
            },
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_production_deploy_check_has_no_unsilenced_warnings(self) -> None:
        result = self._run(
            ["manage.py", "check", "--deploy", "--fail-level", "WARNING"]
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("System check identified no issues", result.stdout)


@unittest.skipUnless(shutil.which("docker"), "Docker CLI is not installed.")
class ComposeConfigurationTests(SimpleTestCase):
    def test_canonical_compose_is_fail_closed_and_orders_the_worker(self) -> None:
        compose = (BASE_DIR / "docker-compose.yml").read_text()

        self.assertEqual(compose.count("DJANGO_ENV: ${DJANGO_ENV:-production}"), 1)
        self.assertEqual(compose.count("environment: *app-environment"), 3)
        self.assertNotIn("DATABASE_URL:", compose)
        self.assertEqual(compose.count("POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-}"), 2)
        self.assertIn("condition: service_healthy", compose)
        self.assertIn("condition: service_completed_successfully", compose)
        self.assertNotIn("socket.create_connection", compose)
        self.assertNotIn("manage.py migrate &&", compose)

    def test_probes_and_restart_policies_are_explicit(self) -> None:
        compose = yaml.safe_load((BASE_DIR / "docker-compose.yml").read_text())

        services = compose["services"]
        self.assertEqual(services["migrate"]["restart"], "no")
        for name in ("db", "web", "refresh-feeds"):
            self.assertEqual(services[name]["restart"], "unless-stopped")
        self.assertEqual(
            services["web"]["healthcheck"]["test"],
            ["CMD", "python", "deploy/healthcheck.py", "/health/ready"],
        )
        self.assertEqual(
            services["refresh-feeds"]["healthcheck"]["test"],
            ["CMD", "python", "manage.py", "check_refresh_worker"],
        )
        for name in ("web", "refresh-feeds"):
            self.assertTrue(services[name]["init"])
            self.assertIn("stop_grace_period", services[name])

    @classmethod
    def _compose_config(cls, env_file: Path) -> dict[str, Any]:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "config",
                "--format",
                "json",
            ],
            cwd=BASE_DIR,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_missing_env_defaults_application_services_to_production(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env") as env_file:
            config = self._compose_config(Path(env_file.name))

        services = config["services"]
        self.assertEqual(services["db"]["environment"]["POSTGRES_DB"], "")
        self.assertEqual(services["db"]["environment"]["POSTGRES_USER"], "")
        self.assertEqual(services["db"]["environment"]["POSTGRES_PASSWORD"], "")
        for service_name in ("web", "refresh-feeds", "migrate"):
            environment = services[service_name]["environment"]
            self.assertEqual(environment["DJANGO_ENV"], "production")
            self.assertEqual(environment["DJANGO_LOG_FORMAT"], "json")
            self.assertNotIn("DATABASE_URL", environment)
        self.assertEqual(
            services["refresh-feeds"]["depends_on"]["web"]["condition"],
            "service_healthy",
        )
        self.assertIn("healthcheck", services["web"])

    def test_serving_and_refreshing_wait_for_completed_migrations(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env") as env_file:
            config = self._compose_config(Path(env_file.name))

        services = config["services"]
        for service_name in ("web", "refresh-feeds"):
            self.assertEqual(
                services[service_name]["depends_on"]["migrate"]["condition"],
                "service_completed_successfully",
            )
        self.assertEqual(
            services["migrate"]["command"],
            ["python", "manage.py", "migrate", "--noinput"],
        )
        self.assertNotIn("healthcheck", services["migrate"])
        self.assertEqual(
            services["web"]["command"],
            [
                "gunicorn",
                "daily_firehose.wsgi:application",
                "--bind",
                "0.0.0.0:8000",
            ],
        )

    def test_development_env_and_reserved_password_survive_compose(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env") as env_file:
            env_file.write(
                "DJANGO_ENV=development\n"
                "POSTGRES_DB=daily_firehose\n"
                "POSTGRES_USER=daily_firehose\n"
                f"POSTGRES_PASSWORD={VALID_DATABASE_PASSWORD}\n"
                "POSTGRES_HOST=db\n"
                "POSTGRES_PORT=5432\n"
            )
            env_file.flush()
            config = self._compose_config(Path(env_file.name))

        for service_name in ("web", "refresh-feeds"):
            environment = config["services"][service_name]["environment"]
            self.assertEqual(environment["DJANGO_ENV"], "development")
            self.assertEqual(environment["POSTGRES_PASSWORD"], VALID_DATABASE_PASSWORD)


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    CSRF_COOKIE_SECURE=True,
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    SECURE_SSL_REDIRECT=True,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
)
class ProxySchemeBehaviorTests(SimpleTestCase):
    def test_direct_http_redirects_to_https(self) -> None:
        response = Client().get("/")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "https://testserver/")

    def test_forwarded_https_is_trusted_without_a_redirect_loop(self) -> None:
        response = Client().get("/", HTTP_X_FORWARDED_PROTO="https")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/accounts/login/?next=/")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Cross-Origin-Opener-Policy"], "same-origin")
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertEqual(response["X-Frame-Options"], "DENY")

    def test_csrf_cookie_is_secure_on_forwarded_https(self) -> None:
        response = Client().get("/accounts/login/", HTTP_X_FORWARDED_PROTO="https")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.cookies["csrftoken"]["secure"])
