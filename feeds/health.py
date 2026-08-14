"""Liveness, readiness, and operational status endpoints."""

from __future__ import annotations

import logging

from django.db import Error as DatabaseLayerError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache

from .api import api_view
from .jobs import job_health
from .models import Feed

logger = logging.getLogger("daily_firehose.health")


def _method_not_allowed() -> JsonResponse:
    response = JsonResponse(
        {"error": {"code": "method_not_allowed", "message": "Method not allowed."}},
        status=405,
    )
    response["Allow"] = "GET, HEAD"
    return response


@never_cache
def live(request: HttpRequest) -> HttpResponse:
    """Prove the WSGI worker handles HTTP without touching the database."""

    if request.method not in {"GET", "HEAD"}:
        return _method_not_allowed()
    return JsonResponse({"status": "live"})


def _database_ready() -> tuple[bool, bool]:
    try:
        # A real round trip, because a cached connection object reports itself
        # as connected long after the server has gone away.
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseLayerError:
        logger.exception("health_database_unavailable")
        return False, False
    try:
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        pending = bool(executor.migration_plan(targets))
    except DatabaseLayerError:
        logger.exception("health_migration_state_unavailable")
        return True, False
    if pending:
        logger.warning("health_migrations_pending")
    return True, not pending


@never_cache
def ready(request: HttpRequest) -> HttpResponse:
    """Prove HTTP handling, database access, and applied migrations."""

    if request.method not in {"GET", "HEAD"}:
        return _method_not_allowed()
    database_ok, migrations_ok = _database_ready()
    ready_now = database_ok and migrations_ok
    return JsonResponse(
        {
            "status": "ready" if ready_now else "not_ready",
            "checks": {"database": database_ok, "migrations": migrations_ok},
        },
        status=200 if ready_now else 503,
    )


@api_view({"GET"})
@never_cache
def status(request: HttpRequest, user) -> HttpResponse:
    """Report worker progress and aggregate ingestion health to an operator."""

    health = job_health()
    counts = Feed.objects.filter(is_active=True).aggregate(
        active=Count("id"),
        failing=Count("id", filter=Q(consecutive_failures__gt=0)),
        backing_off=Count("id", filter=Q(next_retry_at__isnull=False)),
    )
    return JsonResponse(
        {
            "worker": health.payload(),
            "feeds": {
                "active": counts["active"],
                "failing": counts["failing"],
                "backing_off": counts["backing_off"],
            },
        },
        status=200 if not health.stale else 503,
    )
