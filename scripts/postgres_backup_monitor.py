#!/usr/bin/env python3
"""TrueNAS-native freshness supervision for receipt-backed PostgreSQL backups."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from scripts import postgres_backup_receiver as receiver
from scripts.postgres_backup_common import OperatorError, format_utc

CONTROL_DIRECTORY_PATH = receiver.CONTROL_DIRECTORY_PATH
CONTROL_ZFS_DATASET_SOURCE = "nas_general/homes/backups/daily-firehose-control"
# Test-only override. Production always uses CONTROL_DIRECTORY_PATH.
CONTROL_DIRECTORY: Path | None = None
STATE_NAME = ".postgres-backup-monitor-state.json"
STATE_SCHEMA_VERSION = 1
MAX_STATE_BYTES = 4096
REMINDER_INTERVAL = timedelta(hours=24)
MISSED_AFTER = timedelta(hours=14)
CRITICAL_AFTER = timedelta(hours=20)
CONTAINMENT_AFTER = timedelta(hours=24)
JOB_POLL_ATTEMPTS = 120
JOB_POLL_INTERVAL_SECONDS = 1
_SEVERITIES = ("ok", "missed", "critical", "containment")
_JOB_PENDING_STATES = {"WAITING", "RUNNING"}


@dataclass(frozen=True)
class CheckResult:
    severity: str
    receipt_age: timedelta | None
    valid_pairs: int


@dataclass(frozen=True)
class NotificationState:
    severity: str
    notified_at: datetime | None


Notifier = Callable[[str, str], None]


def classify_receipt_age(age: timedelta | None) -> str:
    """Apply the fixed receiver-freshness thresholds."""
    if age is None:
        return "containment"
    if age >= CONTAINMENT_AFTER:
        return "containment"
    if age >= CRITICAL_AFTER:
        return "critical"
    if age >= MISSED_AFTER:
        return "missed"
    return "ok"


def inspect_receipts(directory_fd: int, now: datetime) -> CheckResult:
    """Validate all receipt candidates and classify the newest valid receipt."""
    current = now.astimezone(UTC)
    received: list[datetime] = []
    for name in os.listdir(directory_fd):
        match = receiver._RECEIPT_NAME.fullmatch(name)
        if match is None:
            continue
        try:
            pair = receiver._validated_pair(directory_fd, match.group(1))
        except (OperatorError, OSError, UnicodeError):
            continue
        if pair.retention_at <= current:
            received.append(pair.retention_at)
    if not received:
        return CheckResult("containment", None, 0)
    age = current - max(received)
    return CheckResult(classify_receipt_age(age), age, len(received))


def _configured_control_directory() -> Path:
    return (
        CONTROL_DIRECTORY if CONTROL_DIRECTORY is not None else CONTROL_DIRECTORY_PATH
    )


def _open_control_directory() -> int:
    path = _configured_control_directory()
    try:
        resolved = path.resolve(strict=True)
        info = path.lstat()
    except OSError as error:
        raise OperatorError("backup control dataset is unavailable") from error
    if resolved != path or stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise OperatorError(
            "backup control dataset path must contain no symlink or alias"
        )
    receiver._require_active_zfs_dataset(path, CONTROL_ZFS_DATASET_SOURCE)
    expected_owner = info.st_uid if CONTROL_DIRECTORY is not None else 0
    if info.st_uid != expected_owner or stat.S_IMODE(info.st_mode) & 0o022:
        raise OperatorError(
            "backup control dataset must be root-owned and not writable"
        )
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(path, flags)
    except OSError as error:
        raise OperatorError("backup control dataset cannot be opened safely") from error


def _parse_state(value: bytes) -> NotificationState:
    try:
        decoded = json.loads(value.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise OperatorError("backup monitor notification state is invalid") from error
    if not isinstance(decoded, dict):
        raise OperatorError("backup monitor notification state is invalid")
    severity = decoded.get("severity")
    notified_at_raw = decoded.get("notified_at")
    if (
        decoded.get("schema_version") != STATE_SCHEMA_VERSION
        or severity not in _SEVERITIES
    ):
        raise OperatorError("backup monitor notification state is invalid")
    if notified_at_raw is None:
        notified_at = None
    elif isinstance(notified_at_raw, str) and notified_at_raw.endswith("Z"):
        try:
            notified_at = datetime.fromisoformat(notified_at_raw).astimezone(UTC)
        except ValueError as error:
            raise OperatorError(
                "backup monitor notification state is invalid"
            ) from error
    else:
        raise OperatorError("backup monitor notification state is invalid")
    return NotificationState(severity, notified_at)


def _read_state(directory_fd: int) -> NotificationState | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(STATE_NAME, flags, dir_fd=directory_fd)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(descriptor)
        expected_owner = info.st_uid if CONTROL_DIRECTORY is not None else 0
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != expected_owner
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size < 1
            or info.st_size > MAX_STATE_BYTES
        ):
            raise OperatorError("backup monitor notification state is unsafe")
        value = os.read(descriptor, MAX_STATE_BYTES + 1)
        if len(value) != info.st_size:
            raise OperatorError("backup monitor notification state is invalid")
        return _parse_state(value)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, value: bytes) -> None:
    offset = 0
    while offset < len(value):
        written = os.write(descriptor, value[offset:])
        if written < 1:
            raise OperatorError("backup monitor state write made no progress")
        offset += written


def _write_state(directory_fd: int, state: NotificationState) -> None:
    value = (
        json.dumps(
            {
                "notified_at": (
                    format_utc(state.notified_at)
                    if state.notified_at is not None
                    else None
                ),
                "schema_version": STATE_SCHEMA_VERSION,
                "severity": state.severity,
            },
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    temporary_name = f".{STATE_NAME}.{os.getpid()}.{secrets.token_hex(4)}.part"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary_name, flags, 0o600, dir_fd=directory_fd)
    try:
        _write_all(descriptor, value)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.rename(
            temporary_name,
            STATE_NAME,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    finally:
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def _notification_required(
    previous: NotificationState | None, severity: str, now: datetime
) -> bool:
    if previous is None:
        return severity != "ok"
    if previous.severity != severity:
        return True
    return severity != "ok" and (
        previous.notified_at is None
        or now.astimezone(UTC) - previous.notified_at >= REMINDER_INTERVAL
    )


def _notification_message(result: CheckResult, *, recovery: bool) -> tuple[str, str]:
    if recovery:
        return (
            "Daily Firehose PostgreSQL backup recovered",
            "The newest validated TrueNAS receiver receipt is fresh again (under 14 hours).",
        )
    if result.severity == "missed":
        return (
            "Daily Firehose PostgreSQL backup missed",
            "No newly completed validated receiver receipt has arrived for at least 14 hours. Investigate the scheduled backup.",
        )
    if result.severity == "critical":
        return (
            "CRITICAL: Daily Firehose PostgreSQL backup stale",
            "The newest validated receiver receipt is at least 20 hours old. Investigate backup creation and transport now.",
        )
    return (
        "CONTAINMENT: Daily Firehose PostgreSQL backup stale",
        "No valid receiver receipt exists or the newest is at least 24 hours old. Stop destructive changes, contain application writes, investigate or revoke suspect keys, and recover before normal writes resume.",
    )


def _wait_for_job(client: Any, job_id: int) -> None:
    for attempt in range(JOB_POLL_ATTEMPTS):
        job = client.call("core.get_jobs", [["id", "=", job_id]], {"get": True})
        if not isinstance(job, dict) or job.get("id") != job_id:
            raise OperatorError("TrueNAS mail job is missing or invalid")
        state = job.get("state")
        if state == "SUCCESS":
            return
        if state in {"FAILED", "ABORTED"}:
            raise OperatorError("TrueNAS mail delivery job failed")
        if state not in _JOB_PENDING_STATES:
            raise OperatorError("TrueNAS mail job returned an invalid status")
        if attempt + 1 < JOB_POLL_ATTEMPTS:
            time.sleep(JOB_POLL_INTERVAL_SECONDS)
    raise OperatorError("TrueNAS mail delivery job timed out")


def notify_truenas_administrators(subject: str, text: str) -> None:
    """Send bounded text to default TrueNAS administrators through middleware."""
    if len(subject) > 80 or len(text) > 512:
        raise OperatorError("backup monitor notification is unexpectedly large")
    try:
        from truenas_api_client import Client  # type: ignore[import-not-found]

        with Client() as client:
            result = client.call(
                "mail.send",
                {"subject": subject, "text": text, "queue": False},
            )
            if result is True:
                return
            if isinstance(result, int) and not isinstance(result, bool) and result > 0:
                _wait_for_job(client, result)
                return
            raise OperatorError("TrueNAS mail delivery returned an invalid result")
    except OperatorError:
        raise
    except Exception as error:
        raise OperatorError("TrueNAS administrator notification failed") from error


def check(
    *, now: datetime | None = None, notifier: Notifier = notify_truenas_administrators
) -> CheckResult:
    if os.geteuid() != 0:
        raise OperatorError("backup monitor must run locally as root on TrueNAS")
    current = (now or datetime.now(UTC)).astimezone(UTC)
    data_fd = receiver._open_data_directory()
    try:
        control_fd = _open_control_directory()
        try:
            with receiver._receiver_lock(data_fd), receiver._receiver_lock(control_fd):
                result = inspect_receipts(data_fd, current)
                previous = _read_state(control_fd)
                notification_required = _notification_required(
                    previous, result.severity, current
                )
                if notification_required:
                    subject, text = _notification_message(
                        result,
                        recovery=(
                            result.severity == "ok"
                            and previous is not None
                            and previous.severity != "ok"
                        ),
                    )
                    notifier(subject, text)
                    following = NotificationState(result.severity, current)
                elif previous is None:
                    following = NotificationState(result.severity, None)
                else:
                    following = previous
                if following != previous:
                    _write_state(control_fd, following)
                return result
        finally:
            os.close(control_fd)
    finally:
        os.close(data_fd)


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="run the fixed check")
    options = parser.parse_args(arguments)
    if not options.check:
        parser.error("--check is required")
    return options


def main(arguments: list[str] | None = None) -> int:
    parse_arguments(arguments)
    result = check()
    age = (
        "none"
        if result.receipt_age is None
        else f"{result.receipt_age.total_seconds() / 3600:.1f}h"
    )
    print(
        f"postgres backup monitor: severity={result.severity} "
        f"newest_receipt_age={age} valid_pairs={result.valid_pairs}"
    )
    return 0 if result.severity == "ok" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OperatorError, OSError, UnicodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from None
