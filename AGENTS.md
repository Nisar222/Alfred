# Alfred engineering rules

Read `docs/HANDOFF.md` before making product, telephony, deployment, or data
model decisions.

## Product

Alfred is an on-premise telesales dashboard for a nontechnical owner. The
daily flow must stay simple: prepare a campaign → make calls → review each
call → label its outcome → approve any future improvement. It must not become
a dense CRM.

## Hard safety boundaries

- Never commit or display `.env`, 3CX API keys, passwords, recordings, phone
  lists, or VPS private keys.
- PostgreSQL and Redis must never be publicly exposed. The API binds to
  loopback and Nginx is the public HTTPS entry point.
- Browser audio files live in the private Docker `audio_uploads` volume. Store
  metadata only in PostgreSQL; never store audio blobs in the database or Git.
- `CALL_PROVIDER=threecx` is approved only for user-authorised internal/test
  calls or an explicitly approved campaign. Local tests must use the simulator.
- Calling controls live in Alfred Settings and default to off. Do not replace
  them with browser-editable infrastructure secrets.
- Never silently change a campaign's frozen playbook/script/audio. Create a
  new approved playbook version and a new campaign instead.

## UX rules

- Use plain wording and one obvious primary action per state.
- Business outcomes stay exactly: **Sale, Lead, Not interested, Wrong number**.
- Sentiment is a supporting signal, never the source of truth for success.
- An active campaign must be paused before it can be deleted.
- Show useful status: queued, calling, completed, or a human-readable reason
  that needs attention.

## Engineering rules

- Production schema changes require a reviewed Alembic migration and a backup.
  Do not rely on `Base.metadata.create_all` to alter an existing production DB.
- Use PostgreSQL as the queue authority; do not make in-memory worker state the
  only record of a campaign/call.
- A blocked, empty, invalid-audio, or out-of-hours campaign must not consume a
  call slot or block a later valid campaign.
- Test at one live call first. Increase 1 → 2 → 4 → 8 only after call status,
  duration, failure reasons, and logs are verified.

## Checks

From the repository root:

```bash
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -v
node --check backend/app/web/app.js
bash -n ops/backup-postgres.sh ops/restore-postgres.sh
```

For a release with migrations: backup → pull reviewed Git commit → build API →
`docker compose run --rm api alembic upgrade head` → restart API → health check.
