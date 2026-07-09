# Agent instructions for Daily Firehose

## Redeploying this application

Daily Firehose runs on the Tailscale host `daily-firehose` and is deployed from this repository with Docker Compose.

**Canonical deployment checkout:** `/home/ubuntu/daily-firehose` on host `daily-firehose`.
Use this path consistently. Do not use `/home/feoh/src/personal/daily-firehose` on the deployment host; that is only a local workstation path and does not exist on the host.

If you are already on the deployment host, redeploy from the repo root:

```bash
cd /home/ubuntu/daily-firehose
git pull --ff-only
docker compose up -d --build
```

If you are on another machine and have SSH/Tailscale SSH access to the deployment host, run the same commands remotely:

```bash
ssh daily-firehose 'cd /home/ubuntu/daily-firehose && git pull --ff-only && docker compose up -d --build'
```

If SSH auth fails, do not invent a new deployment path. Report the SSH/access issue and ask the owner to redeploy with the commands above.

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

- The canonical deployment checkout is `/home/ubuntu/daily-firehose` on the `daily-firehose` host.
- The public URL is `https://daily-firehose.reedfish-regulus.ts.net/`.
- If `.env` is needed, create it from `.env.example` and preserve existing production secrets.
- Do **not** remove volumes during a normal redeploy; that would wipe PostgreSQL data.
- If only app code changed, `docker compose up -d --build` is still the preferred redeploy command.
