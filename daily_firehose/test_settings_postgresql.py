"""Opt-in settings for the required PostgreSQL integration test lane.

These settings are deliberately separate from production configuration. The
runner supplies an ephemeral PostgreSQL 17 container and sets the opt-in flag;
normal development continues to use SQLite when no database is configured.
"""

from __future__ import annotations

import os

from django.core.exceptions import ImproperlyConfigured

from .settings import *

if os.environ.get("DAILY_FIREHOSE_POSTGRES_TEST") != "1":
    raise ImproperlyConfigured(
        "daily_firehose.test_settings_postgresql is test-only; "
        "set DAILY_FIREHOSE_POSTGRES_TEST=1 explicitly."
    )

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_TEST_DB", "daily_firehose_test_lane"),
        "USER": os.environ.get("POSTGRES_TEST_USER", "daily_firehose_test_lane"),
        "PASSWORD": os.environ.get(
            "POSTGRES_TEST_PASSWORD", "daily_firehose_test_lane_password"
        ),
        "HOST": os.environ.get("POSTGRES_TEST_HOST", "127.0.0.1"),
        "PORT": os.environ.get("POSTGRES_TEST_PORT", "55432"),
        # Each concurrency worker owns and closes its connection. Persistent
        # connections would make connection ownership less obvious in this lane.
        "CONN_MAX_AGE": 0,
    }
}
