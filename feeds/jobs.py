"""Background-job ownership, heartbeats, and staleness reporting."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import JobRun

REFRESH_JOB = "refresh_feeds"


class JobAlreadyRunning(Exception):
    """Another live worker owns this job."""


@dataclass(frozen=True)
class JobHealth:
    name: str
    status: str
    correlation_id: str
    started_at: datetime | None
    heartbeat_at: datetime | None
    finished_at: datetime | None
    last_success_at: datetime | None
    consecutive_failures: int
    heartbeat_age_seconds: float | None
    success_age_seconds: float | None
    stale: bool

    def payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status,
            "correlation_id": self.correlation_id,
            "started_at": _isoformat(self.started_at),
            "heartbeat_at": _isoformat(self.heartbeat_at),
            "finished_at": _isoformat(self.finished_at),
            "last_success_at": _isoformat(self.last_success_at),
            "consecutive_failures": self.consecutive_failures,
            "heartbeat_age_seconds": self.heartbeat_age_seconds,
            "success_age_seconds": self.success_age_seconds,
            "stale": self.stale,
        }


def _isoformat(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def worker_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"[:128]


def lease_seconds() -> float:
    return float(settings.JOB_LEASE_SECONDS)


def max_heartbeat_age_seconds() -> float:
    return float(settings.JOB_MAX_HEARTBEAT_AGE_SECONDS)


def max_success_age_seconds() -> float:
    return float(settings.JOB_MAX_SUCCESS_AGE_SECONDS)


def acquire_job(name: str, *, correlation_id: str, owner: str | None = None) -> JobRun:
    """Claim exclusive ownership of ``name``, reclaiming an expired lease.

    Raises ``JobAlreadyRunning`` when a live owner holds the job.
    """

    now = timezone.now()
    expired_before = now - timedelta(seconds=lease_seconds())
    try:
        with transaction.atomic():
            for run in JobRun.objects.select_for_update().filter(
                name=name, status=JobRun.Status.RUNNING
            ):
                if run.heartbeat_at > expired_before:
                    raise JobAlreadyRunning(name)
                run.status = JobRun.Status.INTERRUPTED
                run.finished_at = now
                run.error_code = "lease_expired"
                run.error_message = "The owning worker stopped sending heartbeats."
                run.save(
                    update_fields=[
                        "status",
                        "finished_at",
                        "error_code",
                        "error_message",
                    ]
                )
            return JobRun.objects.create(
                name=name,
                correlation_id=correlation_id,
                owner=owner or worker_owner(),
                status=JobRun.Status.RUNNING,
                started_at=now,
                heartbeat_at=now,
            )
    except IntegrityError as exc:
        raise JobAlreadyRunning(name) from exc


def heartbeat(run: JobRun) -> None:
    run.heartbeat_at = timezone.now()
    JobRun.objects.filter(pk=run.pk, status=JobRun.Status.RUNNING).update(
        heartbeat_at=run.heartbeat_at
    )


def finish_job(
    run: JobRun,
    *,
    status: str,
    checked: int = 0,
    attempted: int = 0,
    succeeded: int = 0,
    failed: int = 0,
    skipped: int = 0,
    superseded: int = 0,
    error_code: str = "",
    error_message: str = "",
) -> None:
    run.status = status
    run.finished_at = timezone.now()
    run.heartbeat_at = run.finished_at
    run.checked = checked
    run.attempted = attempted
    run.succeeded = succeeded
    run.failed = failed
    run.skipped = skipped
    run.superseded = superseded
    run.error_code = error_code
    run.error_message = error_message
    run.save(
        update_fields=[
            "status",
            "finished_at",
            "heartbeat_at",
            "checked",
            "attempted",
            "succeeded",
            "failed",
            "skipped",
            "superseded",
            "error_code",
            "error_message",
        ]
    )


def _consecutive_failures(name: str) -> int:
    """Count the newest unbroken run of failed cycles.

    Any other terminal outcome ends the streak: a clean stop is not evidence of
    continuing failure.
    """

    failures = 0
    for status in (
        JobRun.objects.filter(name=name)
        .exclude(status=JobRun.Status.RUNNING)
        .order_by("-started_at", "-id")
        .values_list("status", flat=True)[:50]
    ):
        if status != JobRun.Status.FAILED:
            break
        failures += 1
    return failures


def job_health(name: str = REFRESH_JOB) -> JobHealth:
    now = timezone.now()
    latest = JobRun.objects.filter(name=name).order_by("-started_at", "-id").first()
    last_success_at = (
        JobRun.objects.filter(name=name, status=JobRun.Status.SUCCEEDED)
        .order_by("-finished_at")
        .values_list("finished_at", flat=True)
        .first()
    )
    if latest is None:
        return JobHealth(
            name=name,
            status="never_run",
            correlation_id="",
            started_at=None,
            heartbeat_at=None,
            finished_at=None,
            last_success_at=None,
            consecutive_failures=0,
            heartbeat_age_seconds=None,
            success_age_seconds=None,
            stale=True,
        )
    heartbeat_age = max(0.0, (now - latest.heartbeat_at).total_seconds())
    success_age = (
        None
        if last_success_at is None
        else max(0.0, (now - last_success_at).total_seconds())
    )
    if latest.status == JobRun.Status.RUNNING:
        stale = heartbeat_age > max_heartbeat_age_seconds()
    else:
        stale = success_age is None or success_age > max_success_age_seconds()
    return JobHealth(
        name=name,
        status=latest.status,
        correlation_id=latest.correlation_id,
        started_at=latest.started_at,
        heartbeat_at=latest.heartbeat_at,
        finished_at=latest.finished_at,
        last_success_at=last_success_at,
        consecutive_failures=_consecutive_failures(name),
        heartbeat_age_seconds=round(heartbeat_age, 3),
        success_age_seconds=None if success_age is None else round(success_age, 3),
        stale=stale,
    )
