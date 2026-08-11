# PostgreSQL backup and isolated restore runbook

## Status and approved architecture

This repository implements a **direct push** from the canonical application checkout
`daily-firehose:/home/ubuntu/daily-firehose` to TrueNAS SCALE 25.10.5 at
`192.168.1.2`. No production host or NAS change is performed by this repository work.
The timer remains disabled until every manual gate below passes.

The application host creates an unencrypted PostgreSQL custom-format, compression-9
dump with an exact local Docker Compose command, validates it with the exact local
`db` container `pg_restore --list` command, then pushes the dump and metadata to
`daily-firehose-backup@192.168.1.2`. The restricted SSH protocol contains only:

```text
health
put <backup-id> dump <decimal-size> <lowercase-sha256>
put <backup-id> metadata <decimal-size> <lowercase-sha256>
read <backup-id> dump
read <backup-id> metadata
```

There is **no SSH retention or delete operation**. A compromised application key can
write new bounded objects and read exact valid pairs, but cannot invoke deletion.
Retention is a local TrueNAS maintenance command outside SSH authorization.

Metadata records `storage.transport=ssh_push` and
`storage.offsite=not_verified_by_this_script` independently. It includes source,
`recovery_point_at`, exact size, and SHA-256. The receiver creates an additional durable
receipt sidecar using its own clock and durable monotonic receipt sequence only after
dump and metadata validation. Retention age and tier buckets use receiver receipt time,
not client recovery time. Within each UTC-day, ISO-week, or UTC-month tier bucket,
maintenance preserves the **earliest received** valid pair. A later upload from a
subsequently compromised application key therefore cannot preempt or cause deletion of
an already received genuine bucket point. The newest valid receipt is also always kept,
and every point at age seven days or less is kept. Compromise can still fill the quota,
block future uploads, and occupy otherwise empty future buckets; alert and revoke the key
immediately rather than treating retention as compromise recovery.

A read-only observation dated **2026-08-11** found approximately **33.2 TiB free** on
the NAS. That is not a current capacity guarantee. The measured dump is **14.47 MiB**;
56–60 retained points project to **0.79–0.85 GiB, under 0.9 GiB**, plus sidecars. At
10× current data, budget roughly **8–10 GiB**. The dedicated dataset nevertheless has
an exact **20 GiB quota**, while each accepted dump is bounded to **1 GiB**.

## Security and durability invariants

The client fixes the username and address and always supplies `BatchMode=yes`,
`IdentitiesOnly=yes`, `StrictHostKeyChecking=yes`, a pinned `UserKnownHostsFile`, and
one dedicated identity. The authorized key uses `restrict,command=...`. The forced
receiver parses `SSH_ORIGINAL_COMMAND` without shell execution and rejects paths,
unknown kinds, extra arguments, noncanonical sizes, oversized input, and all retention
or deletion words.

The local source accepts only three Compose operations against
`/home/ubuntu/daily-firehose/docker-compose.yml`: `config --services`, the fixed
container `pg_dump`, and container `pg_restore --list`. Database values stay inside the
container. The dump lives in a mode-0600 anonymous `TemporaryFile`; no named local dump
is created.

The receiver data path is exactly:

```text
/mnt/nas_general/homes/backups/daily-firehose
```

Its persistent, root-controlled code path is exactly:

```text
/mnt/nas_general/homes/backups/daily-firehose-control
```

Backup data never uses the TrueNAS boot filesystem. The receiver requires the effective
`st_dev` entry in `/proc/self/mountinfo` to be an **exact mountpoint** match for the data
path, filesystem type `zfs`, and source dataset
`nas_general/homes/backups/daily-firehose`. An ordinary directory on the parent dataset,
a parent-only mount, or a masking overmount is rejected. Verify the installed result:

```bash
findmnt --mountpoint /mnt/nas_general/homes/backups/daily-firehose \
  --noheadings --output SOURCE,FSTYPE,TARGET
# exact expected fields:
# nas_general/homes/backups/daily-firehose zfs /mnt/nas_general/homes/backups/daily-firehose
```

The launcher clears `PYTHON*`, uses `python3 -I`, and inserts only the fixed control
dataset module path. The data dataset must be mode 0700 and owned by
`daily-firehose-backup`; the control dataset/code remains root-owned and non-writable by
that account, with ZFS `exec=on` only for control.

Every put, read, and local maintenance run takes the same advisory lock. Receiver file
operations are directory-relative and use no-follow flags. Uploads enforce exact bytes
plus EOF and SHA-256, fsync a unique mode-0600 part, publish by no-replace hard link,
fsync the directory, and remove temporary names. Metadata publishes only after matching
the stored dump. A receiver-created receipt publishes last. Reads and maintenance
revalidate receipt, metadata, sizes, and hashes.

Restore evidence also publishes atomically without replacement. Each disposable Docker
container, network, and volume carries the same run-specific label. Evidence records the
exact label, exact resource names, per-resource cleanup status, bounded check results,
and no temporary passwords or error text.

## TrueNAS middleware installation — owner-controlled, not yet run

The payloads below match the TrueNAS SCALE 25.10.5 middleware schemas used for this
approved baseline. Use the UI/API/middleware, never `useradd`, `usermod`, or direct
account-database editing.

### 1. Remove the dated empty path, then create datasets

A read-only observation dated **2026-08-11** found
`/mnt/nas_general/homes/backups/daily-firehose` was an existing empty ordinary
directory, not a mounted dataset. Dataset creation at that exact name requires removing
that empty directory first. This is a separate owner-controlled prerequisite: reconfirm
it is the exact path, a real directory, empty, and not a mount; then use only `rmdir`
(which refuses nonempty directories). Do not use recursive removal.

```bash
test "$(find /mnt/nas_general/homes/backups/daily-firehose -mindepth 1 -maxdepth 1 -print -quit)" = ""
findmnt --mountpoint /mnt/nas_general/homes/backups/daily-firehose && exit 1 || true
rmdir -- /mnt/nas_general/homes/backups/daily-firehose
```

Create the data dataset with an exact 20 GiB quota, inherited compression, execution
disabled, and 80/95 percent quota alerts:

```bash
midclt call pool.dataset.create '{"name":"nas_general/homes/backups/daily-firehose","type":"FILESYSTEM","compression":"INHERIT","exec":"OFF","quota":21474836480,"quota_warning":80,"quota_critical":95}'
```

Create the persistent control dataset separately. It must permit execution because it
contains the forced launcher, but remains root-controlled:

```bash
midclt call pool.dataset.create '{"name":"nas_general/homes/backups/daily-firehose-control","type":"FILESYSTEM","compression":"INHERIT","exec":"ON","quota":1073741824}'
midclt call filesystem.setperm '{"path":"/mnt/nas_general/homes/backups/daily-firehose-control","uid":0,"gid":0,"mode":"755","options":{"stripacl":true,"recursive":false,"traverse":false}}'
```

Before relying on the quota, configure a TrueNAS **Alert Service** under **System
Settings → Alert Services**, send a test alert, and verify the dataset's quota warning
and critical thresholds are visible. Treat a quota warning, rejected upload, or backup
failure as actionable. The 20 GiB quota is a physical byte bound, not a file-count,
availability, or anti-flood guarantee. Small-object floods can exhaust metadata or the
quota; alert and revoke a suspected key promptly.

### 2. Create the exact restricted local user

Prepare the full restricted public-key line from
`deploy/truenas/authorized_keys.example`. The forced command must remain:

```text
/mnt/nas_general/homes/backups/daily-firehose-control/daily-firehose-backup-receiver
```

Set `RESTRICTED_PUBLIC_KEY_LINE` to that full reviewed line and call the 25.10.5
`user.create` schema. `/usr/bin/bash` is selected only so sshd can execute its forced
command; password SSH, SMB, sudo, and unrestricted commands remain unavailable.

```bash
RESTRICTED_PUBLIC_KEY_LINE='restrict,command="/mnt/nas_general/homes/backups/daily-firehose-control/daily-firehose-backup-receiver" ssh-ed25519 <DEDICATED_PUBLIC_KEY> daily-firehose-backup'
USER_PAYLOAD=$(jq -nc --arg key "$RESTRICTED_PUBLIC_KEY_LINE" '{"username":"daily-firehose-backup","full_name":"Daily Firehose restricted backup transport","group_create":true,"home":"/mnt/nas_general/homes/backups/daily-firehose","home_mode":"700","shell":"/usr/bin/bash","password_disabled":true,"smb":false,"ssh_password_enabled":false,"sshpubkey":$key}')
midclt call user.create "$USER_PAYLOAD"
```

Resolve the created numeric UID and primary group's numeric GID through middleware,
then apply exact ownership/mode through `filesystem.setperm`:

```bash
USER_JSON=$(midclt call user.query '[["username","=","daily-firehose-backup"]]' '{"get":true}')
BACKUP_UID=$(printf '%s' "$USER_JSON" | jq -r '.uid')
GROUP_DB_ID=$(printf '%s' "$USER_JSON" | jq -r '.group.id')
BACKUP_GID=$(midclt call group.query "[[\"id\",\"=\",${GROUP_DB_ID}]]" '{"get":true}' | jq -r '.gid')
SET_PERM_PAYLOAD=$(jq -nc --argjson uid "$BACKUP_UID" --argjson gid "$BACKUP_GID" '{"path":"/mnt/nas_general/homes/backups/daily-firehose","uid":$uid,"gid":$gid,"mode":"700","options":{"stripacl":true,"recursive":false,"traverse":false}}')
midclt call filesystem.setperm "$SET_PERM_PAYLOAD"
```

Verify `user.query`, `group.query`, `filesystem.stat`, and the UI show password disabled,
SMB false, no administrative groups, the exact forced key line, exact UID/GID ownership,
and mode 0700. Do not grant the account general shell credentials.

### 3. Install root-owned code on the persistent control dataset

From a reviewed checkout staged on TrueNAS:

```bash
CONTROL=/mnt/nas_general/homes/backups/daily-firehose-control
install -d -m 0755 -o root -g root "$CONTROL/scripts"
install -m 0644 -o root -g root scripts/__init__.py \
  scripts/postgres_backup_common.py scripts/postgres_backup_receiver.py \
  "$CONTROL/scripts/"
install -m 0755 -o root -g root deploy/truenas/daily-firehose-backup-receiver \
  "$CONTROL/daily-firehose-backup-receiver"
```

Verify the control dataset has `exec=on`; the data dataset has `exec=off`; every code
file is root-owned and not group/other writable; and invoking the forced launcher as
the backup user with `SSH_ORIGINAL_COMMAND=health` succeeds.

### 4. Schedule local-only maintenance

Maintenance is not an SSH command. It accepts only `--maintenance-retention`, refuses
when any SSH session variable is present, and runs under the same receiver-wide lock.
It applies fixed 7d-all/30d-daily/90d-weekly/365d-monthly retention using durable
receiver receipt time and sequence. It always preserves the newest valid pair and the
immutable earliest-received valid point in every applicable UTC-day, ISO-week, or
UTC-month bucket. Later receipts in an already occupied bucket cannot displace that
point. It also removes strictly named part files and incomplete unreceipted dump pairs
only after **48 hours**. Invalid received pairs are preserved for administrator review.

Create this TrueNAS Cron Job through **System Settings → Advanced → Cron Jobs**, as
`daily-firehose-backup`, or use the equivalent 25.10.5 payload:

```bash
midclt call cronjob.create '{"user":"daily-firehose-backup","command":"/mnt/nas_general/homes/backups/daily-firehose-control/daily-firehose-backup-receiver --maintenance-retention","description":"Daily Firehose local backup retention and orphan cleanup","schedule":{"minute":"17","hour":"1","dom":"*","month":"*","dow":"*"},"enabled":true,"stdout":true,"stderr":true}'
```

Root may invoke the same exact local command for recovery maintenance. Never add the
maintenance argument to `authorized_keys` or expose it through another application key.

### 5. Pin host key and stage service credentials

Obtain the SSH host key/fingerprint from a trusted TrueNAS UI or console. Do not trust
`ssh-keyscan` alone. Install the verified known-host line and dedicated private key on
`daily-firehose`:

```bash
sudo install -d -m 0700 -o root -g root /etc/daily-firehose-backup/ssh
sudo install -m 0600 -o root -g root ./id_ed25519 /etc/daily-firehose-backup/ssh/id_ed25519
sudo install -m 0644 -o root -g root ./known_hosts /etc/daily-firehose-backup/ssh/known_hosts
```

The service exposes only systemd `LoadCredential` copies to `ubuntu`; direct paths stay
root-only.

The SSH private key is an authorization credential, not dump encryption material. Do
not preserve the current private key as disaster-recovery material. Recovery instead
depends on owner/administrator access to the TrueNAS UI or console.

On the **recovery host**, generate a fresh dedicated Ed25519 authorization key and
obtain and independently verify the current NAS host key. These commands do not run on
TrueNAS:

```bash
sudo install -d -m 0700 -o root -g root /etc/daily-firehose-recovery/ssh
sudo ssh-keygen -t ed25519 -N '' \
  -f /etc/daily-firehose-recovery/ssh/id_ed25519 \
  -C daily-firehose-recovery
sudo install -m 0644 -o root -g root ./verified-known_hosts \
  /etc/daily-firehose-recovery/ssh/known_hosts
sudo cat /etc/daily-firehose-recovery/ssh/id_ed25519.pub
```

Transfer **only that public-key line** through an authenticated administrator channel.
In a separate **TrueNAS UI/console session**, prepend the fixed restriction and replace
the account's authorized public key through the UI or the NAS-local middleware calls:

```bash
RECOVERY_PUBLIC_KEY='<public key copied from the recovery host>'
USER_JSON=$(midclt call user.query '[["username","=","daily-firehose-backup"]]' '{"get":true}')
USER_ID=$(printf '%s' "$USER_JSON" | jq -r '.id')
RECOVERY_KEY_LINE="restrict,command=\"/mnt/nas_general/homes/backups/daily-firehose-control/daily-firehose-backup-receiver\" ${RECOVERY_PUBLIC_KEY}"
UPDATE_PAYLOAD=$(jq -nc --arg key "$RECOVERY_KEY_LINE" '{"sshpubkey":$key}')
midclt call user.update "$USER_ID" "$UPDATE_PAYLOAD"
```

Back on the recovery host, use a transient `systemd-run` credential handoff with
`/etc/daily-firehose-recovery/ssh/id_ed25519` and its `known_hosts`. First run the fixed
`ssh_command("health")` payload, then run
`python3 -m scripts.postgres_restore_verify --backup-id <exact-id> ...` through the same
handoff. The restore performs exact metadata and dump reads before verification.

This drill revokes the former application key. Before any timer activation, generate a
separate fresh application key on `daily-firehose`, install its restricted public half
through the TrueNAS administrator channel, replace the two root-owned service credential
files, and rerun the manual oneshot backup successfully. Complete and record both the
administrator rekey/health/read/restore drill **and** this application-key restoration
oneshot. TrueNAS UI/console administrator access—not preservation of an old private
credential—is the independent recovery authority.

## Install units, run manual backup, and keep timer disabled

Install both units before manual acceptance, but do not enable the timer:

```bash
cd /home/ubuntu/daily-firehose
sudo install -m 0644 deploy/systemd/daily-firehose-postgresql-backup.service \
  /etc/systemd/system/daily-firehose-postgresql-backup.service
sudo install -m 0644 deploy/systemd/daily-firehose-postgresql-backup.timer \
  /etc/systemd/system/daily-firehose-postgresql-backup.timer
sudo systemctl daemon-reload
sudo systemd-analyze verify \
  /etc/systemd/system/daily-firehose-postgresql-backup.service \
  /etc/systemd/system/daily-firehose-postgresql-backup.timer
systemctl is-enabled daily-firehose-postgresql-backup.timer  # expected: disabled
sudo systemctl start daily-firehose-postgresql-backup.service
systemctl show daily-firehose-postgresql-backup.service \
  --property=Result --property=ExecMainStatus
sudo journalctl --unit=daily-firehose-postgresql-backup.service \
  --since '10 minutes ago' --no-pager
test "$(systemctl show daily-firehose-postgresql-backup.service --property=Result --value)" = success
test "$(systemctl show daily-firehose-postgresql-backup.service --property=ExecMainStatus --value)" = 0
```

A completed oneshot is inactive, so `systemctl status` exit state is not acceptance.
Require `Result=success`, `ExecMainStatus=0`, and a reviewed bounded journal. Using
`systemctl start` is required for the manual backup because it exercises the actual
`LoadCredential` handoff. Do not substitute `sudo -u ubuntu` with inaccessible root
credential paths. Independently inspect the exact `.dump`, `.json`, and `.receipt.json`
pair and any separate off-site system.

## Manual isolated restore through a transient credential unit

Build the current web image and create an ubuntu-owned evidence directory. Run the
restore through `systemd-run` so `LoadCredential` performs the same root-to-ubuntu
handoff without exposing root-only source paths:

```bash
cd /home/ubuntu/daily-firehose
docker compose build web
sudo install -d -m 0700 -o ubuntu -g ubuntu \
  /home/ubuntu/.local/state/daily-firehose/restore-evidence
sudo systemd-run --wait --collect --pipe \
  --unit=daily-firehose-restore-verify \
  --uid=ubuntu --gid=ubuntu \
  --working-directory=/home/ubuntu/daily-firehose \
  -p LoadCredential=ssh-private-key:/etc/daily-firehose-backup/ssh/id_ed25519 \
  -p LoadCredential=ssh-known-hosts:/etc/daily-firehose-backup/ssh/known_hosts \
  -p 'Environment=BACKUP_SSH_IDENTITY_FILE=%d/ssh-private-key' \
  -p 'Environment=BACKUP_SSH_KNOWN_HOSTS_FILE=%d/ssh-known-hosts' \
  /usr/bin/python3 -m scripts.postgres_restore_verify \
  --backup-id 20260101T000000Z-0123abcd \
  --evidence-dir /home/ubuntu/.local/state/daily-firehose/restore-evidence
```

Replace the example ID with the exact receipt-backed pair. The verifier streams it to
an anonymous file, validates complete metadata/receipt-backed remote reads and SHA-256,
restores to a disposable PostgreSQL 17 container, checks schema/migrations/application
reads, and cleans only the exact run-labeled resources recorded in evidence.

Before timer activation, configure and test all three independent supervision gates:

1. every scheduled service failure produces an actionable alert;
2. newest verified receiver receipt age reaching **20 hours** produces an age alert; and
3. receipt age reaching **24 hours** triggers the documented containment action: stop
   destructive changes, contain application writes, investigate/revoke suspect keys,
   and recover before normal writes resume.

Only after those alerts/containment, the manual service backup, exact ZFS mount check,
independent dataset/off-site inspection, timed restore, quota alert test,
administrator rekey/health/read/restore drill, fresh application-key installation,
successful post-rekey oneshot backup, and owner approval may the timer be enabled.
Production RPO/RTO remain unknown until observed. A successful SSH upload or timer is
not off-site or restore evidence.

## Repository validation

```bash
uv run python -m unittest tests.postgres_backup_script_cases
uv run python manage.py test feeds
uv run python scripts/check_test_traceability.py
```

The optional receiver integration test is guarded and uses only a temporary directory;
it never contacts TrueNAS.
