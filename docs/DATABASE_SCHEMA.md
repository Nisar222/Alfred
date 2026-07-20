# Database schema — Phase 1

PostgreSQL is the system of record.  The database stores operational facts; it
does not store audio blobs. `recordings.storage_key` points to encrypted local
object/file storage, allowing a retention worker to remove media safely.

## Core workflow

`campaigns` groups a dialling run. `prospects` is the durable contact record.
`calls` is one dial attempt and snapshots the dialled phone/name so later edits
do not rewrite history. A completed call can have one `transcripts` record, one
`recordings` record and one `call_metrics` record. Human labels live on `calls`
with the responsible user and timestamp.

## Learning and accountability

`prompts` holds immutable-style script versions and their approval/deployment
state. A campaign references the prompt used at launch; the legacy `script`
snapshot remains for compatibility and reproducibility. `audit_events` records
who changed a business object. `users` is deliberately small until real SSO is
introduced.

## Integrity and retention rules

- A provider call ID and idempotency key can each occur only once.
- Scores are constrained to 0–10; call duration cannot be negative.
- A call may retain its metrics and outcome after transcript/audio deletion.
- `prospects.do_not_call` must be checked by the call dispatcher before any
  dial attempt. This check belongs in application policy as well as UI.
- Timestamps are timezone-aware UTC in PostgreSQL. Campaign calling windows
  retain an IANA timezone and JSON window for the scheduler.

## Migrations

From `backend/`, use a real PostgreSQL `DATABASE_URL` and run:

```bash
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

For now the API still creates tables automatically for local prototype use.
Before the first shared/staging deployment, switch startup to `alembic upgrade
head` and remove automatic table creation so migrations are the only schema
authority.
