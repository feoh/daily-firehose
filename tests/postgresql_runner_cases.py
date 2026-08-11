"""Isolation regressions for the disposable PostgreSQL integration runner."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any, final

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BASE_POSTGRES_VARIABLES = {
    "POSTGRES_DB",
    "POSTGRES_HOST",
    "POSTGRES_PASSWORD",
    "POSTGRES_PORT",
    "POSTGRES_USER",
}
HOSTILE_AMBIENT_ENV = {
    "DJANGO_ENV": "production",
    "DJANGO_DEBUG": "truthy",
    "DATABASE_URL": "sqlite:////tmp/must-not-be-used.sqlite3",
    "POSTGRES_DB": "production-database",
    "POSTGRES_USER": "production-user",
    "POSTGRES_PASSWORD": "production-password",
    "POSTGRES_HOST": "production.example.invalid",
    "POSTGRES_PORT": "not-a-port",
}


@final
class PostgreSQLRunnerIsolationCases(unittest.TestCase):
    def _environment(self, **overrides: str) -> dict[str, str]:
        environment = dict(os.environ)
        environment.update(HOSTILE_AMBIENT_ENV)
        environment.update(overrides)
        return environment

    def _write_executable(self, directory: Path, name: str, body: str) -> None:
        path = directory / name
        path.write_text(
            "#!/usr/bin/env python3\n" + textwrap.dedent(body), encoding="utf-8"
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def test_opt_in_is_rejected_before_hostile_base_settings_import(self) -> None:
        environment = self._environment(
            DJANGO_SETTINGS_MODULE="daily_firehose.postgresql_test_settings"
        )
        environment.pop("DAILY_FIREHOSE_POSTGRES_TEST", None)
        result = subprocess.run(
            [sys.executable, "-c", "import django; django.setup()"],
            cwd=REPOSITORY_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        output = result.stdout + result.stderr

        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("test-only", output)
        self.assertNotIn("Invalid production configuration", output)
        self.assertNotIn(HOSTILE_AMBIENT_ENV["POSTGRES_PASSWORD"], output)

    def test_runner_clears_hostile_ambient_database_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_directory = root / "bin"
            bin_directory.mkdir()
            captured_environment = root / "uv-environment.json"
            self._write_executable(
                bin_directory,
                "docker",
                """
                import sys

                if "port" in sys.argv:
                    print("127.0.0.1:55439")
                """,
            )
            self._write_executable(
                bin_directory,
                "uv",
                """
                import json
                import os
                from pathlib import Path

                names = {
                    "DAILY_FIREHOSE_POSTGRES_TEST",
                    "DATABASE_URL",
                    "DJANGO_DEBUG",
                    "DJANGO_ENV",
                    "DJANGO_SETTINGS_MODULE",
                    "POSTGRES_DB",
                    "POSTGRES_HOST",
                    "POSTGRES_PASSWORD",
                    "POSTGRES_PORT",
                    "POSTGRES_USER",
                    "POSTGRES_TEST_DB",
                    "POSTGRES_TEST_HOST",
                    "POSTGRES_TEST_PASSWORD",
                    "POSTGRES_TEST_PORT",
                    "POSTGRES_TEST_USER",
                }
                captured = {name: os.environ[name] for name in names if name in os.environ}
                Path(os.environ["CAPTURED_ENVIRONMENT"]).write_text(json.dumps(captured))
                """,
            )
            environment = self._environment(
                CAPTURED_ENVIRONMENT=str(captured_environment),
                PATH=f"{bin_directory}{os.pathsep}{os.environ['PATH']}",
            )
            result = subprocess.run(
                ["bash", "scripts/test_postgresql.sh", "--verbosity", "2"],
                cwd=REPOSITORY_ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            captured: dict[str, Any] = json.loads(captured_environment.read_text())

        self.assertFalse(
            {"DJANGO_ENV", "DJANGO_DEBUG", "DATABASE_URL"} & captured.keys()
        )
        self.assertFalse(BASE_POSTGRES_VARIABLES & captured.keys())
        self.assertEqual(captured["DAILY_FIREHOSE_POSTGRES_TEST"], "1")
        self.assertEqual(
            captured["DJANGO_SETTINGS_MODULE"],
            "daily_firehose.postgresql_test_settings",
        )
        self.assertEqual(captured["POSTGRES_TEST_HOST"], "127.0.0.1")
        self.assertEqual(captured["POSTGRES_TEST_PORT"], "55439")
        self.assertEqual(captured["POSTGRES_TEST_DB"], "daily_firehose_test_lane")


if __name__ == "__main__":
    unittest.main()
