# Daily Firehose

Daily Firehose is a personal, accessible Django RSS reader for daily information flow.

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
