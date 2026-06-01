# Agent instructions for Daily Firehose

## Redeploying this application

Daily Firehose is deployed locally from this repository with Docker Compose.
Redeploy from the repo root:

```bash
git pull --ff-only
docker compose up -d --build
```

That command rebuilds and restarts:

- `web` — Django + Gunicorn
- `refresh-feeds` — background feed refresh loop
- `db` — PostgreSQL (kept on the `postgres-data` volume)

## Post-deploy verification

After redeploying, verify the stack:

```bash
docker compose ps
docker compose logs --no-color --tail=80 web refresh-feeds
curl -I http://127.0.0.1:8000/
```

Expected results:

- `web`, `refresh-feeds`, and `db` are `Up`
- web logs show Gunicorn started successfully
- migrations either apply cleanly or report `No migrations to apply`
- `curl` returns `302 Found` redirecting to `/accounts/login/?next=/` for an anonymous request

## Notes

- Run commands from `/home/feoh/src/personal/daily-firehose`.
- If `.env` is needed, create it from `.env.example`.
- Do **not** remove volumes during a normal redeploy; that would wipe PostgreSQL data.
- If only app code changed, `docker compose up -d --build` is still the preferred redeploy command.
