#!/usr/bin/env python3
"""Dependency-free capacity model and threshold checker for PostgreSQL backups."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

MIB = 1024**2
GIB = 1024**3
MEASURED_DUMP_BYTES = 15_173_740
MEASURED_PAIR_SIDECAR_BYTES = 1_387
RETAINED_POINTS_MIN = 56
RETAINED_POINTS_MAX = 60
BACKUPS_PER_DAY = 2
LOCAL_SNAPSHOT_PIN_DAYS = 14
LOCAL_SNAPSHOTS_PER_DAY = 24
DATA_QUOTA_BYTES = 20 * GIB
CONTROL_QUOTA_BYTES = 1 * GIB
THRESHOLDS = (60, 80, 95)


@dataclass(frozen=True)
class CapacityModel:
    """Logical source bytes plus conservative snapshot-retained changed bytes."""

    scale: int
    point_bytes: int
    live_min_bytes: int
    live_max_bytes: int
    snapshot_delta_max_bytes: int
    local_min_bytes: int
    local_max_bytes: int

    @property
    def data_quota_percent_min(self) -> float:
        return self.local_min_bytes * 100 / DATA_QUOTA_BYTES

    @property
    def data_quota_percent_max(self) -> float:
        return self.local_max_bytes * 100 / DATA_QUOTA_BYTES


def positive_integer(value: str) -> int:
    """Parse a canonical positive integer for bounded CLI planning inputs."""
    if (
        not value.isascii()
        or not value.isdigit()
        or value.startswith("0")
        or len(value) > 5
    ):
        raise argparse.ArgumentTypeError("must be a canonical positive integer")
    parsed = int(value)
    if parsed > 10_000:
        raise argparse.ArgumentTypeError("must not exceed 10000")
    return parsed


def nonnegative_integer(value: str) -> int:
    """Parse a canonical nonnegative integer for copy planning."""
    if (
        not value.isascii()
        or not value.isdigit()
        or (len(value) > 1 and value[0] == "0")
        or len(value) > 3
    ):
        raise argparse.ArgumentTypeError("must be a canonical nonnegative integer")
    parsed = int(value)
    if parsed > 100:
        raise argparse.ArgumentTypeError("must not exceed 100")
    return parsed


def conservative_snapshot_delta(point_bytes: int, deleted_points: int) -> int:
    """Count only deleted changed points still pinned by snapshots.

    Hourly snapshots share unchanged blocks; multiplying the live dataset by the
    number of snapshots would double-count unchanged content.
    """
    if point_bytes < 1 or deleted_points < 0:
        raise ValueError("snapshot capacity inputs must be positive")
    return point_bytes * deleted_points


def build_model(scale: int = 1) -> CapacityModel:
    """Build the fixed-policy model for current data or an integer growth scenario."""
    if isinstance(scale, bool) or not isinstance(scale, int) or scale < 1:
        raise ValueError("scale must be a positive integer")
    point_bytes = MEASURED_DUMP_BYTES * scale + MEASURED_PAIR_SIDECAR_BYTES
    live_min = point_bytes * RETAINED_POINTS_MIN
    live_max = point_bytes * RETAINED_POINTS_MAX
    deleted_points = BACKUPS_PER_DAY * LOCAL_SNAPSHOT_PIN_DAYS
    snapshot_delta = conservative_snapshot_delta(point_bytes, deleted_points)
    return CapacityModel(
        scale=scale,
        point_bytes=point_bytes,
        live_min_bytes=live_min,
        live_max_bytes=live_max,
        snapshot_delta_max_bytes=snapshot_delta,
        local_min_bytes=live_min + snapshot_delta,
        local_max_bytes=live_max + snapshot_delta,
    )


def threshold_status(percent: float) -> str:
    """Return the highest crossed data-dataset capacity threshold."""
    if percent >= 95:
        return "critical (95% TrueNAS quota critical threshold crossed)"
    if percent >= 80:
        return "warning (80% TrueNAS quota warning threshold crossed)"
    if percent >= 60:
        return "planning-warning (60% capacity review threshold crossed)"
    return "ok (below 60% capacity review threshold)"


def _mib(value: int) -> float:
    return value / MIB


def _gib(value: int) -> float:
    return value / GIB


def render(model: CapacityModel, replicated_copies: int = 0) -> str:
    """Render a human-readable report without presenting plans as observations."""
    deleted_points = BACKUPS_PER_DAY * LOCAL_SNAPSHOT_PIN_DAYS
    lines = [
        f"PostgreSQL backup capacity model ({model.scale}x dump scenario)",
        (
            "Measured basis: "
            f"dump={MEASURED_DUMP_BYTES:,} bytes; "
            f"pair sidecars={MEASURED_PAIR_SIDECAR_BYTES:,} bytes; "
            f"modeled point={model.point_bytes:,} bytes"
        ),
        (
            "Source live (56-60 retained points): "
            f"{_mib(model.live_min_bytes):.2f}-{_mib(model.live_max_bytes):.2f} MiB "
            f"({_gib(model.live_min_bytes):.2f}-{_gib(model.live_max_bytes):.2f} GiB)"
        ),
        (
            f"Conservative local snapshot delta (<= {deleted_points} deleted points "
            f"pinned for {LOCAL_SNAPSHOT_PIN_DAYS} days): "
            f"<={_mib(model.snapshot_delta_max_bytes):.2f} MiB "
            f"({_gib(model.snapshot_delta_max_bytes):.2f} GiB)"
        ),
        (
            "Total local source (live + snapshot delta): "
            f"{_gib(model.local_min_bytes):.2f}-{_gib(model.local_max_bytes):.2f} GiB"
        ),
        (
            f"Data quota: {_gib(DATA_QUOTA_BYTES):.0f} GiB; total local source uses "
            f"{model.data_quota_percent_min:.2f}-{model.data_quota_percent_max:.2f}%"
        ),
        (
            f"Threshold status (upper projection): "
            f"{threshold_status(model.data_quota_percent_max)}; "
            "thresholds=60% planning, 80% warning, 95% critical"
        ),
        (
            f"Control quota: {_gib(CONTROL_QUOTA_BYTES):.0f} GiB; backup artifacts do "
            "not use the control dataset, so control usage and percentage are unmeasured"
        ),
        (
            f"Snapshot accounting: {LOCAL_SNAPSHOTS_PER_DAY}/day snapshots share unchanged "
            "blocks; the model counts only the conservative deleted-point delta once"
        ),
    ]
    if replicated_copies:
        copies_min = model.local_min_bytes * replicated_copies
        copies_max = model.local_max_bytes * replicated_copies
        all_min = model.local_min_bytes + copies_min
        all_max = model.local_max_bytes + copies_max
        lines.extend(
            [
                (
                    f"Replicated-copy planning envelope ({replicated_copies} additional "
                    f"full local-footprint copies): {_gib(copies_min):.2f}-"
                    f"{_gib(copies_max):.2f} GiB"
                ),
                (
                    "Planning envelope across source plus copies: "
                    f"{_gib(all_min):.2f}-{_gib(all_max):.2f} GiB; "
                    "planning-only, not attested remote usage"
                ),
            ]
        )
    else:
        lines.append(
            "Replicated copies: omitted; no remote usage is attested by this model"
        )
    return "\n".join(lines)


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scale",
        type=positive_integer,
        default=1,
        help="integer multiplier for measured dump bytes (default: 1)",
    )
    parser.add_argument(
        "--replicated-copies",
        type=nonnegative_integer,
        default=0,
        help="additional full-footprint copies for planning only (default: 0)",
    )
    parser.add_argument(
        "--fail-at",
        type=int,
        choices=THRESHOLDS,
        default=80,
        metavar="{60,80,95}",
        help="exit nonzero when the upper local projection reaches this percent (default: 80)",
    )
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    options = parse_arguments(arguments)
    model = build_model(options.scale)
    print(render(model, options.replicated_copies))
    if model.data_quota_percent_max >= options.fail_at:
        print(
            f"capacity check failed: upper projection reached {options.fail_at}%",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
