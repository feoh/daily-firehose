# Daily Firehose

Daily Firehose is a personal, accessible Django RSS reader for daily information flow.

## Screenshot

![Daily Firehose article reading view showing today’s articles and read/save controls](docs/images/article-reading-view.png)

## Goals

- Show Today’s Firehose, plus week/month/feed views.
- Keep readability and WCAG AA accessibility central.
- Support Django auth, spacious article cards, keyboard-friendly controls, and high-contrast themes.
- Import and export feed subscriptions as OPML.
- Save articles to Linkding and track local saved-article metadata for future recommendations.
- Expose agent-friendly digest JSON.

## Architecture and features

See the [current-state architecture inventory](docs/architecture/current-state.md) for the component, route, data, integration, deployment, operational-risk, and incremental-refactor maps at the documented commit snapshot.

See the [feature and behavioral-contract catalog](docs/features/catalog.md) and [cross-feature contracts](docs/features/contracts.md) for stable IDs, browser/API/operational behavior, known defects, and their maintenance protocols.

See the [current-suite feature-to-test traceability matrix](docs/features/test-traceability.md) for exact test identities, evidence levels, expected failures, dimension-specific gaps, and executable drift checks.

See the [reliability objectives and risk register](docs/reliability/objectives-and-risks.md) for desired SLIs/SLOs, current measurement gaps, ranked risks, alert policy, and the dependency-ordered mitigation portfolio.

See the [PostgreSQL backup and isolated restore runbook](docs/operations/postgresql-backups.md) for the approved direct SSH push with no remote deletion operation, receipt-based local TrueNAS maintenance, isolated-restore tooling, exact middleware installation gates, and the currently unknown RPO/RTO evidence.

## Local setup with uv

```bash
cd ~/src/personal/daily-firehose
uv sync
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Open <http://127.0.0.1:8000/> and sign in.

### Tests, including Playwright

Install the locked development dependencies and Chromium once per environment:

```bash
uv sync --dev
uv run playwright install chromium
```

On a clean Ubuntu CI runner, install Chromium and its system packages with:

```bash
uv run playwright install --with-deps chromium
```

SQLite remains the fast local lane:

```bash
uv run python manage.py test feeds
```

PostgreSQL 17 is the required integration lane:

```bash
./scripts/test_postgresql.sh
```

The integration runner starts a disposable Compose PostgreSQL 17 service on an
ephemeral loopback port, runs the complete Django suite with the opt-in
`daily_firehose.postgresql_test_settings` module, and always removes its
container, network, and tmpfs-backed data. It never uses the production Compose
file or volume. Before Django starts, the script clears inherited `DJANGO_ENV`,
`DJANGO_DEBUG`, `DATABASE_URL`, and base `POSTGRES_*` variables, then supplies
only the opt-in test settings and `POSTGRES_TEST_*` connection. PostgreSQL-only
`TransactionTestCase` coverage uses distinct worker connections, bounded
barriers/events, and 5-second PostgreSQL statement/lock timeouts; injected write
failures are labeled transaction-boundary tests, not concurrency tests. Pass
normal Django test options (for example `--verbosity 2`) to the script. Override
only the bounded startup wait with `POSTGRES_TEST_WAIT_TIMEOUT_SECONDS` when
necessary.

Browser screenshots and DOM snapshots are written to the ignored
`test-artifacts/playwright/` directory only when a browser assertion fails
(including a documented expected-failure characterization).

## Docker Compose setup

The compose stack includes the Django web app, PostgreSQL, a one-shot migration
service, and a supervised feed-refresh worker. Migrations run to completion in
their own service before the web app serves or the worker refreshes, so a failed
migration stops the deployment instead of leaving new code on an old schema.

```bash
cp .env.example .env
# Edit .env, especially DJANGO_SECRET_KEY and LINKDING_TOKEN.
docker compose up --build
```

Create a superuser in the running web container:

```bash
docker compose exec web python manage.py createsuperuser
```

Open <http://127.0.0.1:8000/> and sign in.

### Production configuration and preflight

Canonical Compose defaults application services to fail-closed `production` so
starting without `.env` cannot expose development mode. `.env.example`
explicitly opts into `development` for local Compose. A production `.env` must
set every value below rather than inherit development placeholders:

```dotenv
DJANGO_ENV=production
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=<a unique high-entropy value of at least 50 characters>
DJANGO_ALLOWED_HOSTS=daily-firehose.reedfish-regulus.ts.net
DJANGO_CSRF_TRUSTED_ORIGINS=https://daily-firehose.reedfish-regulus.ts.net
POSTGRES_DB=daily_firehose
POSTGRES_USER=daily_firehose
POSTGRES_PASSWORD=<a non-development database password>
POSTGRES_HOST=db
POSTGRES_PORT=5432
```

Generate a Django secret without committing it, for example with
`uv run python -c 'import secrets; print(secrets.token_urlsafe(64))'`. Compose
passes PostgreSQL fields separately, so reserved characters such as `%`, `@`,
`/`, and `:` are not reinterpreted as URL syntax.

For the existing production volume, preserve its stored database credential:
the current password is already a non-default 43-character URL-safe value, so
this deployment does **not** require rotation. Merely changing
`POSTGRES_PASSWORD` in `.env` does not change the role password stored in the
volume. Start only PostgreSQL, then run both the configuration check and a real
connectivity probe before restarting the application:

```bash
docker compose up -d db
docker compose run --rm --no-deps --build web \
  python manage.py check --deploy --fail-level WARNING
docker compose run --rm --no-deps web python manage.py shell -c \
  "from django.db import connection; connection.ensure_connection(); print('database connection ok')"
```

The probe detects a stored-role/`.env` mismatch. If it fails authentication, do
not remove the volume and do not start the full stack. Restore the last working
`.env` password, or deliberately rotate the stored role interactively without
placing a password in shell history:

```bash
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

At the `psql` prompt run `\password <database-role>`, enter the new value twice,
then `\q`; update `.env` to the same value and rerun the connectivity probe.
Never pass the password with `-c`, print it, or delete `postgres-data` during
recovery.

Production startup rejects debug mode, missing or development secrets,
malformed/non-DNS/IP host entries, non-exact HTTPS CSRF origins, incomplete
PostgreSQL fields, and the documented development database password. An
explicit `DATABASE_URL` remains supported for non-Compose deployments and must
be a complete PostgreSQL URL in production. Errors name invalid variables but
never echo their values.

Gunicorn listens on `0.0.0.0` inside the web container, while Docker publishes
port 8000 only on host loopback. The verified Tailscale Funnel path terminates
TLS and proxies to that port with `X-Forwarded-Proto: https`. Django trusts only
that scheme header, redirects direct HTTP to HTTPS, and marks session and CSRF
cookies secure. The refresh worker waits for the web healthcheck, which becomes
healthy only after migrations complete and `/health/ready` proves real database
access and applied migrations.

HSTS remains explicitly disabled (`SECURE_HSTS_SECONDS=0`) until an operator
confirms a staged rollout will not lock out recovery paths. Only deploy-check
warning `security.W004` is silenced for that documented decision; the preflight
above fails on every other warning.

For an application-code rollback, check out the last known-good revision and run
`docker compose up -d --build`; preserve `.env` and all volumes. Compose normally
recreates only changed application services and leaves the unchanged `db`
container running. Code rollback does not automatically reverse schema changes:
use a migration-specific, verified reverse migration when safe, or restore a
known-good database backup.

## Configuration

Environment variables:

- `DJANGO_ENV` — exactly `development` or `production`. Direct uv defaults to development; canonical Compose defaults to production, while `.env.example` explicitly selects development.
- `DJANGO_SECRET_KEY` — optional development value; required, strong, and non-default in production.
- `DJANGO_DEBUG` — strictly parsed boolean, defaulting to `true` only for development; must be `false` in production.
- `DJANGO_ALLOWED_HOSTS` — comma-separated host list. Local defaults are supplied only in development; production requires explicit public hostnames.
- `DJANGO_CSRF_TRUSTED_ORIGINS` — comma-separated origins. Production requires explicit HTTPS origins whose hostnames are allowed.
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, and `POSTGRES_PORT` — discrete PostgreSQL connection fields used by Compose. Partial values fail startup; production rejects missing fields and the development password.
- `DATABASE_URL` — optional alternative for non-Compose deployments. Direct uv development uses SQLite when neither it nor discrete PostgreSQL fields are set; production accepts only a complete PostgreSQL URL with a non-development password.
- `LINKDING_URL` — defaults to `https://linkding.reedfish-regulus.ts.net`.
- `LINKDING_TOKEN` — API token used by **Save to Linkding**.
- `FEED_FETCH_CONNECT_TIMEOUT_SECONDS` — feed connection timeout, default `5`.
- `FEED_FETCH_READ_TIMEOUT_SECONDS` — maximum socket inactivity while reading a feed, default `20`.
- `FEED_FETCH_TOTAL_TIMEOUT_SECONDS` — deadline checked before requests and between chunks, default `60`.
- `FEED_FETCH_MAX_BYTES` — maximum identity-encoded feed response size, default `5000000`.
- `FEED_FETCH_MAX_REDIRECTS` — maximum redirects per feed request, default `3`.
- `FEED_REFRESH_SECONDS` — delay between refresh cycles, default `3600`.
- `DJANGO_LOG_FORMAT` — exactly `json` (default) or `plain`.
- `DJANGO_LOG_LEVEL` — level for request, health, job, and worker records, default `INFO`.
- `FEED_REFRESH_LOG_LEVEL` — level for per-feed refresh records, default `INFO`.
- `JOB_LEASE_SECONDS` — how long a worker's claim on the refresh job survives without a heartbeat, default `900`.
- `JOB_HEARTBEAT_SECONDS` — minimum interval between heartbeats during a cycle, default `60`; must be below the lease.
- `JOB_MAX_HEARTBEAT_AGE_SECONDS` — heartbeat age above which a running cycle is reported stale, default `300`.
- `JOB_MAX_SUCCESS_AGE_SECONDS` — age of the last successful cycle above which the worker is reported stale, defaulting to two refresh intervals plus the stale-heartbeat allowance.

## Feeds and OPML

Feeds can be added from the **Feeds** page or from Django admin.

OPML support:

- Import: `/opml/import/`
- Export: `/opml/export/`

## Refreshing feeds

Run the management command manually or from cron/systemd:

```bash
uv run python manage.py refresh_feeds
```

Before deploying Article identity changes, run the read-only audit against the target
PostgreSQL database. It reports only Feed IDs/counts and exits nonzero if duplicate
`(feed, guid)` or `(feed, url)` identities exist; it never reconciles or deletes data:

```bash
uv run python manage.py audit_article_identity
```

Feed refresh and metadata discovery use the same bounded downloader. It accepts
only credential-free HTTP(S) URLs on ports 80 and 443, rejects every non-global
network target (including redirect destinations), requests identity encoding,
and limits redirects and downloaded bytes. Separate connect/read timeouts apply;
the total deadline is checked before requests and between response chunks.

The total deadline is not a hard wall-clock interrupt: a socket operation may
run until its read timeout, and an adversarial slow-drip response can exploit
the interval between checks. Network-level egress controls plus a process-level
watchdog or explicit minimum-rate enforcement are required to close that gap
and the DNS-validation/request DNS-rebinding interval completely.

For each active feed, metadata, article, and success-state writes commit in one
atomic database transaction. Article identity is canonical within its Feed: a stable
GUID or URL reconciles the existing row in place, preserving first-seen, read, save,
and newsletter associations. Under a per-Feed lock, the complete document resolves
against an immutable pre-write identity snapshot; split or colliding evidence fails
safely before the deterministic write plan runs. PostgreSQL serializes writes per Feed,
and a monotonic attempt generation prevents stale completion from overwriting newer
status; stale callers report `superseded` with authoritative persisted retry metadata.
A failed article write rolls back that transaction without aborting later
feeds. Attempt state is recorded before network work, and classified failure state is
recorded after rollback, so both intentionally persist outside the content transaction.
Operational state is retained on
`Feed`: `last_attempt_at`, safe error code/message,
consecutive failures, and `next_retry_at`. `last_fetched_at` is the authoritative
last-success timestamp and changes only after all feed metadata and article
writes commit successfully. Failures use exponential backoff starting at five
minutes and capped at 24 hours; skipped feeds remain visible in command, browser,
and API summaries. Refresh completion logs include safe bounded feed identity,
status, duration, write counts or error code, failure count, and retry time. The
management command logs unexpected exceptions with tracebacks while returning
only classified safe messages to users. Refresh adapters use a four-state terminal
contract: `succeeded`, `failed`, `skipped`, and `superseded`. A superseded result
means a stale caller completed after a newer attempt took ownership: it was attempted,
but it is not a failure and does not make the management command exit nonzero. Browser,
command, and API summaries report superseded results separately from failed feeds. In
the refresh API, `checked` remains the total number of result rows (including backoff
skips), while `attempted` excludes only `skipped` rows and therefore includes
`superseded` rows. The API returns a separate `superseded` aggregate count and retains
each checked row in `feeds`. The management command exits nonzero only when at least one
result is actually `failed`.

## Health, observability, and the refresh worker

Three endpoints report runtime state:

| Path | Auth | Purpose |
| --- | --- | --- |
| `/health/live` | none | The web process answers HTTP. Touches no database, so a database outage never reports a working listener as dead. |
| `/health/ready` | none | Real database connection plus applied migrations. Returns `503` when either fails. |
| `/health/status` | bearer token | Worker heartbeat, last successful cycle, consecutive failed cycles, and aggregate active/failing/backing-off feed counts. Returns `503` when the worker is stale. |

`/health/live` and `/health/ready` are exempt from the HTTPS redirect so the
container probe on loopback receives a real result. They report booleans only;
failure detail goes to logs. `/health/status` reports counts and never feed
titles or URLs.

The refresh worker runs as a management command rather than a shell loop:

```bash
uv run python manage.py run_refresh_worker          # loop until stopped
uv run python manage.py run_refresh_worker --once   # a single cycle
uv run python manage.py check_refresh_worker        # nonzero when stale
```

Each cycle claims the refresh job in the `JobRun` table. A partial unique index
on running cycles is the overlap lock, so a second worker is refused instead of
refreshing the same feeds concurrently; PostgreSQL enforces this across
connections. A cycle heartbeats between feeds and renews its lease, so a slow
run is never stolen while a crashed worker's lease expires and is reclaimed.
`SIGTERM` finishes the feed in flight, records the interrupted cycle, releases
the lock, and exits.

A cycle that evaluates every feed is recorded as succeeded even when individual
feeds failed, because per-feed failure is what backoff, `consecutive_failures`,
and the `feeds.failing` count already report. A cycle is only failed when it
could not finish. That keeps worker staleness meaning "ingestion stopped" rather
than "one feed is broken", which is the distinction the 2026-08-11 incident
needed. The `refresh_feeds` command keeps its separate contract of exiting
nonzero when any feed failed, for manual and cron use.

Logs are one JSON object per record by default, carrying a correlation ID that
ties a response to every record written while serving it. Clients may supply
`X-Correlation-ID`; the value is bounded and character-restricted before use,
and it is echoed on the response. Records name the matched view rather than the
raw path — the Postmark webhook carries its shared secret as a path segment —
and the formatter drops secret-named fields and scrubs configured secret values
from every rendered record.

## Saved articles

When an article is saved, Daily Firehose records the article URL, title, feed, category, timestamp, and Linkding status locally. This preserves a history that can later be used to highlight articles likely to be interesting.

## Agent-friendly API

Create a bearer token for an agent or other program:

```bash
uv run python manage.py create_api_token <username> --name morning-agent
```

Use it with `Authorization: Bearer <token>` (or `Token <token>`) against
`/api/v1/` endpoints. Every nonempty JSON request body must use
`application/json` or an `application/*+json` media type and contain a UTF-8 JSON
object. Nonstandard numeric constants (`NaN` and positive/negative `Infinity`) are
malformed JSON. Fields are typed strictly: JSON booleans are `true`/`false`, not
strings or numbers; missing, `null`, and a value of the wrong type are distinct.
Unknown or repeated query parameters and unknown JSON fields are rejected. This
is an intentional compatibility tightening: clients must stop sending ignored
parameters. Bodyless GET, DELETE, refresh, and feed-mark operations reject any
semantic request body; legacy zero-field multipart bodies remain accepted.

### Endpoints and input contracts

- `GET /api/v1/briefing/morning/` — today's unread, unsaved articles plus action
  URL templates.
- `GET /api/v1/articles/` — article list. `period` is `today`, `week`, or `month`.
  `start=YYYY-MM-DD` and `end=YYYY-MM-DD` must be supplied together and ordered.
  Optional `feed_id` is a positive integer. `include_read` and `include_saved`
  accept the lowercase query values `true` or `false` only.
- `POST` or `PATCH /api/v1/articles/<id>/read/` — optional
  `{"is_read": true}`; an omitted field preserves the legacy default of `true`.
- `POST` or `PATCH /api/v1/articles/<id>/saved/` — optional
  `{"is_saved": true, "notes": "...", "interest_score": 4.5}`. `saved` is a
  compatibility alias for `is_saved`, but both cannot be sent together.
  `interest_score` is nullable and otherwise must be a finite number from 0 to 5
  inclusive. `DELETE` the same URL to unsave locally. Unsave responses preserve
  the article's independent read state.
- `POST /api/v1/mark-period-read/` — `scope` is `day`, `week`, or `month`.
  Optional `period_start` and `period_end` ISO dates must be supplied together and
  ordered.
- `GET` or `POST /api/v1/feeds/` — list feeds or create/update one by its unique
  `feed_url`. Write fields are `feed_url`, `title`, `site_url`, `description`,
  nullable positive-integer `category_id`, and boolean `is_active`.
- `GET`, `PATCH`, or `DELETE /api/v1/feeds/<id>/` — inspect, update, or deactivate
  a feed. `POST /api/v1/feeds/<id>/mark-read/` marks that feed read.
- `GET` or `POST /api/v1/categories/` — list or create a category with string
  `name` and `slug` fields.
- `GET` or `PATCH /api/v1/preferences/` — inspect or update `theme`, boolean
  `compact`, and boolean `focus_mode`. Theme values are the values exposed by the
  preferences UI (for example `system`, `light`, `dark`, or `dracula`).
- `POST /api/v1/refresh/` — refresh feeds and return succeeded, failed,
  backoff-skipped, and superseded outcomes.

`feed_url` and nonblank `site_url` values must be credential-free HTTP(S) URLs.
Django model length, slug, choice, and URL validation runs before writes. A create request for
an already-identical category or feed retains its documented idempotent success;
a conflicting unique value returns `409`.

Every article representation includes additive per-article `capabilities` and
`actions`. Clients must honor `capabilities.save.allowed` and use the per-article
`actions` object rather than assuming every top-level action template applies.
Ordinary RSS articles advertise save and mark-read actions. Newsletter-backed
articles retain Open/Read and mark-read actions, but omit save and report
`save_not_allowed` because newsletters cannot be saved locally or sent to
Linkding. The morning briefing retains its top-level action templates only for
backward compatibility.

Signed browser-agent actions remain:

- `GET /api/v1/articles/<id>/save-and-go/?sig=...`
- `GET /api/v1/mark-period-read-and-go/?scope=day&sig=...`

A valid signed action returns `302` on success. Invalid signatures return `403`
before query/semantic validation, a valid signature with an invalid scope returns
`422`, and missing agent-link configuration returns `503 not_configured`.
Newsletter save attempts through bearer or signed APIs return
`422 save_not_allowed`; session form/AJAX attempts keep the card visible and show
a safe explanation.

Postmark delivers only to `POST /api/postmark/inbound/<secret>/`; other methods
return `405`. Secret authentication runs before body/query validation. The current
accepted body schema requires only a truthy `MessageID` (or `MessageId`). Missing
subject defaults to “Untitled newsletter,” missing or invalid date uses the current
time, and address/body fields are optional and string-coerced. Ingestion does not
call model `full_clean()`: `422` is only the adapter mapping if the service raises a
Django `ValidationError`, not a promise that malformed email strings or every model
limit are rejected. Integrity conflicts map to `409`. The separate tracked Postmark
atomicity task still owns rollback and race-idempotency; error mapping does not
claim that a failed ingestion rolled back every write.

The older authenticated-session digest remains at `/api/digest/today.json`.

### Error contract

API errors use one envelope; semantic/model errors may include field details:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request fields failed validation.",
    "fields": {"feed_url": ["Enter a valid URL."]}
  }
}
```

- `400 bad_request` — malformed/non-object JSON, invalid UTF-8, nonstandard numeric
  constants, invalid media types, forbidden bodies, missing fields, wrong JSON
  types, incomplete date pairs, repeated parameters, or unknown inputs.
- `401 unauthorized` / `403 forbidden` — missing/invalid credentials or signed
  action authorization.
- `404 not_found` — an article, feed, or category does not exist.
- `405 method_not_allowed` — the endpoint does not support the HTTP method.
- `409 conflict` — a uniqueness or concurrent state conflict.
- `422 validation_error` — a well-typed value violates URL, date ordering, choice,
  range, slug, or Django model validation.
- `503 not_configured` — a signed action's server-side agent identity is absent.

Feed metadata discovery failures retain their stable transport code (for example
`timeout` or `http_failure`) inside the same envelope with HTTP `400`. The
application maps expected authentication, request-validation, lookup,
model-validation, and uniqueness failures on matched API routes to JSON. URL
resolution remains outside that boundary: the unsigned `<int:…>` converters do
not match negative path IDs, which currently receive Django's HTML `404` before
bearer authentication or shared positive-ID validation. Unforeseeable programming
errors, process failures, or database/network outages may still reach Django's
`500` handling and are not claimed to be part of this API error contract.
