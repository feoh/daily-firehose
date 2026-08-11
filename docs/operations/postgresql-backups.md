# PostgreSQL backup and isolated restore runbook

## Status and owner-approved architecture

Repository tooling is ready for a **loki pull**, but no production backup exists yet
and activation remains blocked. The approved data path is:

1. canonical source `daily-firehose:/home/ubuntu/daily-firehose`;
2. a constrained systemd service on the current `loki` host running as `feoh` because
   the existing CIFS mount is force-owned by uid/gid 1000 with mode 0755;
3. encrypted artifact and metadata written directly to loki's active CIFS mount at
   `/nas/homes/backups/daily-firehose`; and
4. the NAS's independently managed off-site backup.

This avoids reverse SSH and puts no NAS credentials, encryptor, scheduler, or new SSH
credential on the application host. A read-only 2026-08-11 observation found a 42 MB
production database. An actual streamed custom-format
`pg_dump --compress=9` measured **14.47 MiB**. That observation did not read `.env`,
mutate production, or establish a usable backup.

**Encryption is the remaining stop gate.** `age` is absent and no encryption key
authority is approved. The exact next decision is a dedicated age recipient and the
custody/recovery location for its private identity. The intended profile keeps only the
public recipient on loki and keeps the private identity in an owner-approved off-host
restore authority. Do not install packages, generate a key, substitute plaintext, GPG,
or symmetric credentials, or activate the timer until that decision is recorded and a
manual encrypted backup and isolated restore both pass.

Production RPO/RTO are unknown. The twice-daily target and quarterly drill in
`REL-OBJ-010` remain objectives, not achieved claims.

Local mutation/fake-adapter cases are deliberately excluded from Django discovery so
its baseline remains 17 modules/225 tests/12 expected failures. Invoke them explicitly:

```bash
uv run python -m unittest tests.postgres_backup_script_cases
```

## Safety invariants

- The source host and path are exact allowlisted values. The script accepts no arbitrary
  remote command. It invokes OpenSSH with `BatchMode=yes`; every remote token is a
  repository-owned Compose operation against the canonical path.
- `pg_dump` and `pg_restore --list` execute inside the remote `db` service. Database
  environment values stay inside the container; scripts never read, source, echo, or
  copy production `.env` and never put a password in arguments.
- Plaintext exists only in a mode-0600 anonymous/already-unlinked local temporary-file
  descriptor on loki. The same descriptor is fsynced, rewound for remote
  `pg_restore --list`, rewound for encryption, and closed in `finally`; SIGKILL cannot strand a
  named plaintext dump. The encrypted `.part` is removed on ordinary failure.
- The destination must resolve under `/nas/homes/backups/daily-firehose`, and
  `/nas/homes` must be an active CIFS/SMB3 mount according to kernel mount state.
  Symlink escape and a local directory masking a missing NAS are rejected.
- Only encrypted dump bytes (plus non-secret JSON metadata) reach the NAS. The
  encrypted artifact must be nonempty, fsynced, renamed atomically, and
  directory-fsynced before the complete metadata structure is written and read back.
  The owner-approved CIFS mount forces `file_mode=0755`, so tooling does **not** claim
  NAS ciphertext is mode 0600; NAS access mode follows that mount policy. Metadata says
  `nas_cifs_confirmed`; it explicitly says the independent off-site copy was
  `not_verified_by_this_script`. Local process output alone never proves off-host or
  off-site durability.
- Never use `docker compose down -v`, remove `postgres-data`, restore over production,
  rotate live credentials, or target broad Docker cleanup during a drill.

## Public-key adapter contract

`BACKUP_ENCRYPTOR` must contain one executable path, never a command string or secret
argument. It is invoked as:

```text
adapter <temporary-encrypted-output-path>
```

Plain archive bytes arrive on stdin. The adapter must use the approved public age
recipient from root/service-readable configuration, create a nonempty age artifact at
the exact output path, return nonzero on incomplete encryption, and never log plaintext,
recipient-private material, credentials, or signed URLs. There is deliberately no
plaintext/GPG/symmetric fallback.

Restore-only `BACKUP_DECRYPTOR` is likewise one executable path and is invoked as
`adapter <encrypted-input-path>`, writing custom-archive bytes to stdout. Its private
identity belongs only in the approved restore authority, never in the backup service
configuration or on the application host.

## Manual backup after the encryption decision

On loki, prepare a reviewed checkout at `/opt/daily-firehose`, BatchMode SSH
host-key/identity policy for the `daily-firehose` alias under `feoh`, and the existing
NAS directory. The systemd service runs as `feoh` because the mounted CIFS share is
force-owned by uid/gid 1000 and mode 0755; a distinct service UID cannot write it
without changing the owner-approved mount. The remote SSH account must be limited to
the access needed to run the three fixed Compose operations. Do not copy database or
NAS secrets to it.

Create `/etc/daily-firehose-backup/backup.conf` from the example and install the
approved public-key adapter outside this repository. Then run one foreground backup as
the service identity, without enabling a timer:

```bash
cd /opt/daily-firehose
sudo -u feoh env \
  BACKUP_ENCRYPTOR=/usr/local/libexec/daily-firehose-backup-age-encrypt \
  python3 -m scripts.postgres_backup
```

Success produces a matching `.dump.age`/`.json` pair directly under the NAS path.
Independently inspect NAS state and its off-site system; script output and metadata do
not prove that independent backup. Stop if the source/path, mount type, archive listing,
encryption, fsync, or metadata confirmation fails.

## Tiered retention

Retention is dry-run by default and only considers matched `.dump.age`/`.json` pairs
whose metadata, nonzero size, SHA-256, archive-list, encryption, fsync, and NAS status
all validate. It keeps:

- every twice-daily point through age 7 days (inclusive);
- the newest point per UTC day after 7 days through 30 days;
- the newest point per ISO week after 30 days through 90 days;
- the newest point per UTC month after 90 days through 365 days; then
- older validated pairs, plus duplicate points within a tier bucket, become eligible,
  **except that the newest validated pair (the last known good) is always preserved**, no
  matter how old it becomes during an outage.

Age is measured conservatively from metadata `recovery_point_at` (the dump
`started_at`), never from the later completion time. At steady twice-daily cadence this
is **56–60 encrypted dump files × 14.47 MiB ≈ 0.8–0.85 GiB** for the current database,
plus negligible JSON sidecars, safely below 1 GiB. At 10× current data, plan roughly
**8–10 GiB** including ordinary variation.
These are measured-size projections, not quotas.

Preview repeatedly; a dry-run is idempotent and deletes nothing:

```bash
cd /opt/daily-firehose
python3 -m scripts.postgres_backup_retention
```

Only after reviewing every line and separately confirming the NAS/off-site policy may
an operator explicitly apply deletion:

```bash
python3 -m scripts.postgres_backup_retention --apply
```

`--apply` revalidates the active CIFS destination immediately before its deletion
loop, deletes only the matched validated NAS pairs shown by the same policy, and fsyncs
that directory after each pair. There is no remote application-host deletion and no
separate NAS/provider lifecycle action. No apply has been authorized or performed by
this patch.

## Isolated restore on loki/new resources

Select a matching encrypted artifact and metadata directly from the NAS path. With the
approved private identity available only to the restore authority, run from loki (or an
approved new restore host with the NAS mounted at the same path):

```bash
cd /opt/daily-firehose
docker compose build web
export BACKUP_DECRYPTOR=/usr/local/libexec/daily-firehose-backup-age-decrypt
mkdir -p "$HOME/.local/state/daily-firehose/restore-evidence"
chmod 0700 "$HOME/.local/state/daily-firehose/restore-evidence"
python3 -m scripts.postgres_restore_verify \
  --artifact /nas/homes/backups/daily-firehose/daily-firehose-postgres-<id>.dump.age \
  --metadata /nas/homes/backups/daily-firehose/daily-firehose-postgres-<id>.json \
  --evidence-dir "$HOME/.local/state/daily-firehose/restore-evidence"
```

The verifier validates the encrypted SHA-256, creates uniquely named/labeled temporary
Docker network, PostgreSQL 17 container, and volume, restores with
`--exit-on-error`, checks required tables, runs Django database/migration checks and
bounded semantic reads, then removes only those exact disposable resources. Decrypt,
readiness, schema, application, and cleanup failures still write bounded evidence
(status/check booleans and failure type, not secret-bearing error text); cleanup never
widens beyond the uniquely named resources created by that run. It never connects to or
cuts over production and never prints row content or temporary secrets.

Measured script duration excludes incident decision, NAS retrieval, application
cutover, and service smoke. Record those separately. No RPO/RTO claim is permitted
until a real encrypted backup is independently confirmed and a production-like timed
drill passes.

## Dormant loki systemd templates

The supplied unit is a template for loki, not the application host. It uses
`feoh` (the NAS mount owner), `/opt/daily-firehose`,
`RequiresMountsFor=/nas/homes`, an explicit `mountpoint` check, and network readiness.
The main script's allowlisted remote `docker compose config --services` operation is
its authoritative source/Compose readiness check; the unit deliberately has no generic
remote `ssh ... test -d` operation outside the three-operation policy. The timer targets
**00:00 and 12:00 UTC**, is persistent, and bounds a run to two hours. The obsolete
app-host push model is not supported.

After the encryption/key decision, successful manual backup, independent NAS/off-site
confirmation, and successful isolated restore, an operator may review and install the
files **without activation**:

```bash
sudo install -d -m 0750 -o root -g feoh /etc/daily-firehose-backup
sudo install -m 0640 -o root -g feoh \
  deploy/systemd/backup.conf.example /etc/daily-firehose-backup/backup.conf
sudo install -m 0644 deploy/systemd/daily-firehose-postgresql-backup.service \
  /etc/systemd/system/daily-firehose-postgresql-backup.service
sudo install -m 0644 deploy/systemd/daily-firehose-postgresql-backup.timer \
  /etc/systemd/system/daily-firehose-postgresql-backup.timer
sudo systemctl daemon-reload
systemd-analyze verify \
  /etc/systemd/system/daily-firehose-postgresql-backup.service \
  /etc/systemd/system/daily-firehose-postgresql-backup.timer
```

Activation (`systemctl enable --now ...`) is a separate production/infrastructure
mutation and remains blocked. After eventual activation, wire completion/age alerts and
collect quarterly restore evidence; a systemd success alone is not restore or off-site
evidence.
