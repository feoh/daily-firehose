# Incident: critical backup-stale page during a TrueNAS reinstall

**Date investigated:** 2026-08-19
**Affected surface:** scheduled PostgreSQL backup push to TrueNAS; receipt-age monitor
**Status:** root cause confirmed; no data loss; bounded retry added to the backup unit

## Alert as received

```text
Subject: TrueNAS deepthought.feoh.org: CRITICAL: Daily Firehose PostgreSQL backup stale

The newest validated receiver receipt is at least 20 hours old. Investigate backup
creation and transport now.
```

The alert was a **true positive** about receipt age and a **correct** application of the
documented thresholds. The underlying cause was benign owner-initiated NAS maintenance,
not a backup, transport, or integrity failure.

## Root cause

The NAS was reinstalled to TrueNAS SCALE 25.10.6 across the `00:00 UTC` backup slot. The
`00:00:03` activation found the receiver unreachable, failed at its first SSH `health`
probe, and — because the unit had no retry — abandoned the cycle. The next activation was
not due for 12 hours, so the newest valid receipt aged past the 14-hour missed threshold
and then the 20-hour critical threshold before any further attempt could occur.

`zpool history boot-pool` carries the fresh-install signature rather than an in-place
update: a newly created `boot-pool/ROOT`, a `@pristine` snapshot of the new boot
environment, `boot-pool/.system` datasets created from scratch, and only one boot
environment present afterwards.

| Time (UTC) | Event |
| --- | --- |
| 08-18 12:00:09 | Last successful backup, `20260818T120005Z-a830e826`, receipt sequence 16 |
| 08-19 00:00:03 | Scheduled activation starts |
| 08-19 00:00:18 | Fails after 15s at the SSH `health` probe; journal records only `command failed safely: ssh` |
| 08-19 00:28:36 | `boot-pool/ROOT/25.10.6` and its `@pristine` snapshot created |
| 08-19 00:29:39 | First boot of the new environment |
| 08-19 00:30:06 | `boot-pool/.system` datasets created |
| 08-19 00:48:46–00:49:05 | Boot environment toggled writable and back; configuration restored |
| 08-19 00:49:32 | Final boot-pool import; receiver available again |
| 08-19 02:37 | Monitor sends `missed` (14-hour threshold) |
| 08-19 08:37 | Monitor sends `CRITICAL` (20-hour threshold) |

The receiver was reachable again at `00:49:32`, roughly **50 minutes** after the failed
attempt and comfortably inside the `REL-OBJ-010` `+2h` completion objective. A single
retry would have met the objective and produced no page at all.

## Verified absence of data loss

- All **16** stored pairs revalidated through the receiver's own `_validated_pair`,
  including full SHA-256 re-hashing of every dump. Result: `valid_pairs=16
  invalid_pairs=0`.
- Receipt sequence unbroken, 1 through 16.
- Data pool healthy; no orphan `.part` files; dataset at 256M of its 20 GiB quota.
- The pinned SSH host key still matched `known_hosts`, so the configuration restore
  preserved `/etc/ssh` host keys and no host-key rotation was needed.
- The restricted `daily-firehose-backup` account, its forced-command `authorized_keys`,
  both datasets and quotas, and **both** middleware cron jobs survived the reinstall.
- A production `health` probe from the application host using the real credentials
  returned `daily-firehose-backup receiver ok`.

## Monitor behaved correctly

Notification state was intact at `severity=critical, notified_at=2026-08-19T08:37:01Z`.
Exactly two mails were sent, one per severity transition. The 24-hour reminder interval
correctly suppressed the hourly checks in between, so this was not an alert storm.

## Contributing factors

1. **No retry on the scheduled unit.** `Type=oneshot` with no `Restart=` meant one
   transient failure cost a full 12-hour cycle. `Persistent=true` on the timer does not
   help; it only replays activations missed while the *application host* was off.
2. **The failure was undiagnosable from its own logs.** The first SSH call ran under
   `capture_output=True`, which pipes stderr purely to discard it. The journal recorded
   `command failed safely: ssh` and nothing else — no exit status, no ssh diagnostic.
   Root-causing this required NAS-side forensics, and the NAS journal no longer covered
   the failure window because the reinstall reset it.
3. **Receiver maintenance is not coordinated with the backup schedule.** The NAS is a
   single receiver whose own maintenance can land on a backup slot.

## Fixes applied

- `deploy/systemd/daily-firehose-postgresql-backup.service` now retries with
  `Restart=on-failure`, `RestartSec=10min`, a hard `StartLimitBurst=6` cap,
  `StartLimitIntervalSec=4h`, and a per-attempt `TimeoutStartSec=30min`. This absorbs a
  receiver outage of up to about an hour inside the `+2h` objective while remaining far
  below the 12-hour cycle, so the next scheduled activation is never refused.
- The SSH `health` probe now discards only stdout and leaves stderr on the inherited
  descriptor, so ssh's own diagnostic reaches the journal.
- `run()` now reports the failed command's exit status, which distinguishes an ssh
  transport failure (`255`) from a receiver rejection (`1`).

Verified empirically before adoption: `systemd-analyze verify` accepts the unit;
`Restart=on-failure` does retry a `Type=oneshot` unit and stops at the burst cap with
`Result=start-limit-hit`; and an exhausted unit starts normally again once
`StartLimitIntervalSec` elapses, with no `systemctl reset-failed`.

## Deliberately unchanged

The 14/20/24-hour thresholds are `REL-OBJ-010` RPO policy, not tuning knobs, and the
monitor applied them correctly. Making them looser to silence this page would have
weakened the guarantee that detected the outage.

## Residual risk

Receiver maintenance is still uncoordinated with the `00:00`/`12:00 UTC` schedule. Retry
now absorbs outages up to about an hour; a longer NAS outage overlapping a slot will
still page, which is the intended behavior. Announced NAS maintenance longer than an hour
should expect a `missed` and possibly a `critical` page, or be scheduled away from the
backup slots.
