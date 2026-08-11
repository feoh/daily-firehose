# Agent instructions for Daily Firehose

## Redeploying this application

Daily Firehose runs on the Tailscale host `daily-firehose` and is deployed from this repository with Docker Compose.

**Canonical deployment checkout:** `/home/ubuntu/daily-firehose` on host `daily-firehose`.
Use this path consistently. Do not use `/home/feoh/src/personal/daily-firehose` on the deployment host; that is only a local workstation path and does not exist on the host.

If you are already on the deployment host, redeploy from the repo root. Before
the first production-mode deployment, update the existing `.env` in place with
the required explicit values documented in README.md; preserve existing secrets
and database credentials. The current production database password is already a
non-default 43-character URL-safe value, so do not rotate it for this deploy.

```bash
cd /home/ubuntu/daily-firehose
git pull --ff-only
docker compose up -d db
docker compose run --rm --no-deps --build web \
  python manage.py check --deploy --fail-level WARNING
docker compose run --rm --no-deps web python manage.py shell -c \
  "from django.db import connection; connection.ensure_connection(); print('database connection ok')"
docker compose up -d --build
```

If you are on another machine and have SSH/Tailscale SSH access to the deployment host, run the same commands remotely:

```bash
ssh daily-firehose 'cd /home/ubuntu/daily-firehose && git pull --ff-only && docker compose up -d db && docker compose run --rm --no-deps --build web python manage.py check --deploy --fail-level WARNING && docker compose run --rm --no-deps web python manage.py shell -c "from django.db import connection; connection.ensure_connection(); print(\"database connection ok\")" && docker compose up -d --build'
```

If SSH auth fails, do not invent a new deployment path. Report the SSH/access issue and ask the owner to redeploy with the commands above.

That sequence rebuilds and recreates changed application services:

- `web` — Django + Gunicorn
- `refresh-feeds` — background feed refresh loop

The unchanged `db` service normally remains running and always keeps its data on
the `postgres-data` volume. If the connectivity probe fails, stop before the
full-stack restart and follow README.md's interactive credential recovery; do
not print passwords or remove the volume.

## Post-deploy verification

After redeploying, verify the stack:

```bash
docker compose ps
docker compose logs --no-color --tail=80 web refresh-feeds
curl -I -H 'Host: daily-firehose.reedfish-regulus.ts.net' \
  http://127.0.0.1:8000/
curl -I -H 'Host: daily-firehose.reedfish-regulus.ts.net' \
  -H 'X-Forwarded-Proto: https' http://127.0.0.1:8000/
curl -I https://daily-firehose.reedfish-regulus.ts.net/
```

Expected results:

- `web`, `refresh-feeds`, and `db` are `Up`
- web logs show Gunicorn started successfully
- migrations either apply cleanly or report `No migrations to apply`
- direct loopback HTTP returns a `301` HTTPS redirect
- the proxy-header and public HTTPS requests return `302 Found` redirecting to `/accounts/login/?next=/` for an anonymous request

## Notes

- The canonical deployment checkout is `/home/ubuntu/daily-firehose` on the `daily-firehose` host.
- The public URL is `https://daily-firehose.reedfish-regulus.ts.net/`.
- If `.env` is needed, create it from `.env.example` and preserve existing production secrets.
- Do **not** remove volumes during a normal redeploy; that would wipe PostgreSQL data.
- If only app code changed, `docker compose up -d --build` is still the preferred redeploy command.
- To roll back application code, check out the last known-good revision and rebuild while preserving `.env` and volumes. Schema migrations require a migration-specific verified reversal or backup restore; a code checkout alone does not undo them.
