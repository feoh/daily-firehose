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

Run the complete Django suite with `uv run python manage.py test feeds`. Browser
screenshots and DOM snapshots are written to the ignored
`test-artifacts/playwright/` directory only when a browser assertion fails
(including a documented expected-failure characterization).

## Docker Compose setup

The compose stack includes the Django web app, PostgreSQL, and a simple feed-refresh loop.

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

## Configuration

Environment variables:

- `DJANGO_SECRET_KEY` — production secret key.
- `DJANGO_DEBUG` — defaults to `true` for local development.
- `DJANGO_ALLOWED_HOSTS` — comma-separated host list, defaults to `localhost,127.0.0.1,daily-firehose.reedfish-regulus.ts.net`.
- `DJANGO_CSRF_TRUSTED_ORIGINS` — comma-separated trusted origins for proxied HTTPS, defaults to `https://daily-firehose.reedfish-regulus.ts.net`.
- `DATABASE_URL` — optional database URL. Defaults to local SQLite for uv development; compose sets this to PostgreSQL.
- `LINKDING_URL` — defaults to `https://linkding.reedfish-regulus.ts.net`.
- `LINKDING_TOKEN` — API token used by **Save to Linkding**.
- `FEED_FETCH_CONNECT_TIMEOUT_SECONDS` — feed connection timeout, default `5`.
- `FEED_FETCH_READ_TIMEOUT_SECONDS` — maximum socket inactivity while reading a feed, default `20`.
- `FEED_FETCH_TOTAL_TIMEOUT_SECONDS` — deadline checked before requests and between chunks, default `60`.
- `FEED_FETCH_MAX_BYTES` — maximum identity-encoded feed response size, default `5000000`.
- `FEED_FETCH_MAX_REDIRECTS` — maximum redirects per feed request, default `3`.

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
atomic database transaction. A failed article write rolls back that transaction
without aborting later feeds. Attempt state is recorded before network work, and
classified failure state is recorded after rollback, so both intentionally
persist outside the content transaction. Operational state is retained on
`Feed`: `last_attempt_at`, safe error code/message,
consecutive failures, and `next_retry_at`. `last_fetched_at` is the authoritative
last-success timestamp and changes only after all feed metadata and article
writes commit successfully. Failures use exponential backoff starting at five
minutes and capped at 24 hours; skipped feeds remain visible in command, browser,
and API summaries. Refresh completion logs include safe bounded feed identity,
status, duration, write counts or error code, failure count, and retry time. The
management command logs unexpected exceptions with tracebacks while returning
only classified safe messages to users. In the refresh API, `checked` remains
the total number of result rows, including backoff skips, while `attempted`
counts only feeds for which a refresh was attempted during that request.

## Saved articles

When an article is saved, Daily Firehose records the article URL, title, feed, category, timestamp, and Linkding status locally. This preserves a history that can later be used to highlight articles likely to be interesting.

## Agent-friendly API

Create a bearer token for an agent or other program:

```bash
uv run python manage.py create_api_token <username> --name morning-agent
```

Use it with `Authorization: Bearer <token>` against `/api/v1/` endpoints. Common morning workflow:

- `GET /api/v1/briefing/morning/` — today’s unread, unsaved articles plus action URLs.
- `GET /api/v1/articles/?period=today|week|month` — article lists; optional `include_read=true`, `include_saved=true`, `feed_id=...`, or `start=YYYY-MM-DD&end=YYYY-MM-DD`.
- `POST /api/v1/articles/<id>/read/` with `{"is_read": true}` — mark read or unread.
- `POST /api/v1/articles/<id>/saved/` with `{"is_saved": true, "notes": "..."}` — save locally and to Linkding when configured. `DELETE` the same URL to unsave locally.
- `POST /api/v1/mark-period-read/` with `{"scope": "day"}` — mark day/week/month read.
- `GET/POST/PATCH /api/v1/feeds/…`, `GET/POST /api/v1/categories/`, `GET/PATCH /api/v1/preferences/`, and `POST /api/v1/refresh/` expose feed/category/preference/refresh controls.

The older session-authenticated today digest remains available at `/api/digest/today.json`.
