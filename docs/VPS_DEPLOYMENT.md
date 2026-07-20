# VPS deployment guide (Phase 1)

This guide deploys the dashboard API, PostgreSQL, and Redis to one Ubuntu VPS.
It does **not** configure telephony, AI models, customer imports, Tailscale, or
public DNS. Keep `CALL_PROVIDER=simulator` until the separate integration approval.

## Security boundary

The database is intentionally not published to the internet. The API binds to
`127.0.0.1:8000`; a separately configured TLS reverse proxy is the only allowed
public entry point. Do not expose ports 5432 or 6379. Restrict SSH to key-based
access, use a non-root deployment account, and keep the VPS patched.

Backups contain prospect and call data. Store them outside the Docker volume on
an encrypted filesystem or encrypted backup destination with access limited to
the deployment account. Test a restore before the first real campaign.

## First deployment

1. Install Docker Engine and the Docker Compose plugin using Docker's official
   Ubuntu instructions. Add the dedicated deployment account to the `docker`
   group, then start a new login session. Do not run the app as `root`.
2. Clone the approved project release into a directory owned by that account.
   Create the runtime settings with `cp .env.example .env`, then set a unique
   32+ character `POSTGRES_PASSWORD` (for example, from a password manager).
   Set the final HTTPS dashboard origin in `CORS_ORIGINS` before enabling a proxy.
3. Review configuration before starting it:

   ```bash
   docker compose config
   docker compose build --pull
   docker compose up -d
   docker compose ps
   curl --fail http://127.0.0.1:8000/health
   ```

4. Verify the dashboard locally on the VPS and inspect logs:

   ```bash
   docker compose logs --tail=100 api db redis
   ```

5. Only after this succeeds, configure a TLS reverse proxy and firewall rules.
   Publish HTTPS (443) and SSH only; redirect HTTP (80) to HTTPS if it is used.

## Database migrations

The current app creates its development schema at startup, but production
schema changes must be tracked through Alembic revisions before deployment.
The initial operational schema is committed in `backend/alembic/versions`.
For every release:

1. Take a verified backup.
2. Pull the reviewed release.
3. Run `docker compose run --rm api alembic upgrade head`.
4. Start the release with `docker compose up -d --build` and check `/health`.

Never run `alembic revision --autogenerate` against the production database.
Create and review revisions in local/staging development, then commit them.

## Backup and restore

Run a backup at least daily and before any migration:

```bash
set -a; . ./.env; set +a
BACKUP_DIR=/srv/jamal-backups ./ops/backup-postgres.sh
```

Set `/srv/jamal-backups` ownership and filesystem encryption before use. Add a
systemd timer or another approved scheduler only after a successful manual run.
For disaster recovery, stop the API, run `ops/restore-postgres.sh BACKUP_FILE`,
start the API, and verify the health endpoint. Perform a restore drill monthly.

## Operational checks

- Daily: `docker compose ps`, API `/health`, and that the scheduled backup exists.
- Before releases: backup, migration review, and a smoke test with simulator data.
- Monthly: restore drill, OS/Docker patching window, access review, disk usage.
- Immediately investigate: container restart loops, failed backups, unexplained
  database growth, or any public exposure of PostgreSQL/Redis.
