# Jamal Dialler project guidance

## Product intent

Build an on-premise AI telesales dashboard for a nontechnical business owner.
The normal daily flow is: launch campaign → calls run → review one call at a
time → approve a proposed script improvement. The dashboard must make the
next useful action obvious and must not feel like a dense CRM.

## Safety boundaries

- Keep `CALL_PROVIDER=simulator` until explicit approval for 3CX/SIP work.
- Never use real prospects, credentials, recordings, or VPS access in local
  tests.
- Do not expose PostgreSQL or Redis publicly. Audio is stored outside the
  database; the database stores only recording metadata.
- Production schema changes use reviewed Alembic migrations. Do not rely on
  `Base.metadata.create_all` in production.

## Development checks

From the repository root, run:

```bash
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -v
node --check backend/app/web/app.js
bash -n ops/backup-postgres.sh ops/restore-postgres.sh
```

## UX rules

- Use plain human wording (for example, “Needs review,” not internal queue terms).
- One primary action per state.
- Keep outcome labelling to four obvious choices: Sale, Lead, Not interested,
  Wrong number.
- Do not silently alter a live sales script; recommendations need approval in
  the MVP.
