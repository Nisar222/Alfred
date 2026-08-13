# 3CX Call Recording — Per-User Permission (Resolved 2026-08-13)

## What was actually wrong

Recording is controlled by a **per-user** 3CX setting, `RecordCalls`, not a
system-wide or Route-Point-level setting as originally suspected. Confirmed
directly from the raw XAPI user payload (`GET /xapi/v1/Users`, field
`RecordCalls`):

| Extension | Name | RecordCalls |
|---|---|---|
| 101 | Nisar Khan | ✅ (was already on) |
| 105 | Gudo | ✅ (was already on) |
| 102 | MANDO | ✅ (enabled 2026-08-13) |
| 104 | Erik | ✅ (enabled 2026-08-13) |
| 107 | Quest | ✅ (enabled 2026-08-13) |
| 108 | Nisar2 test | ✅ (enabled 2026-08-13) |
| 109 | Nemo | ✅ (enabled 2026-08-13) |
| 100 | David Eriksson | ❌ still off — ask David to enable it himself |

Extensions with `RecordCalls: False` never produce a recording no matter
what Alfred's code, matching logic, or 3CX call routing does. Since Alfred
routes campaign calls via DTMF to whichever agent extension is configured,
any campaign that reaches an extension with this off will show "recording
unavailable" for that call, even though everything else worked.

## How to check/change it

1. Log into the 3CX Management Console as administrator.
2. Go to the affected user's extension settings.
3. Find the **Call Recording** section and enable the recording permission
   for that user (labelled `RecordCalls` in the API; the console UI wording
   may differ by 3CX version — look for "Record calls" or similar on the
   user's own settings page, not a system-wide toggle).
4. Repeat per extension. There's no bulk/group-level way to check this from
   Alfred — the client is intentionally read-only and only exposes a safe
   subset of user fields to the app.

## Verifying it worked

Place a call that reaches the extension in question (a real campaign call,
or Alfred's own test-call flow at Settings → individual test call), then:

```bash
docker compose exec -T api python3 -c "
from app.config import get_settings
from app.threecx import ThreeCXClient
c = ThreeCXClient(get_settings())
try:
    rows = c.list_xapi_recordings()
    latest = sorted(rows, key=lambda r: str(r.get('StartTime') or ''), reverse=True)[0]
    print(latest)
finally:
    c.close()
"
```

If a fresh recording appears with the right `StartTime`/`To`/`From`, 3CX
recorded it. Alfred's `RecordingSync` background worker (30s poll) should
link it automatically — check the `recordings` table:

```bash
docker compose exec -T db psql -U jamal -d jamal_dialler -c \
  "SELECT * FROM recordings ORDER BY created_at DESC LIMIT 5;"
```

(Note: the correct Postgres credentials are `jamal` / `jamal_dialler`, not
`alfred` / `alfred` — an earlier version of this doc and of
`alfred-vps.sh` had this wrong.)

## What this is NOT

- **Not** a Route Point vs. extension recording config issue. Alfred's
  outbound leg (the `3cxapi` Route Point) itself has never appeared in 3CX's
  recordings list, in any test performed. What gets recorded is the *second*
  leg — the transferred call that lands on a real extension via DTMF
  routing — which is why this is a per-user setting problem, not a Route
  Point problem.
- **Not** a bug in `recordings.py`'s phone-matching logic. That logic works
  correctly (verified live end-to-end on 2026-08-13, call id 4824 → recording
  id 85, linked automatically). See `HANDOFF-RECORDING-ISSUE.md` for the full
  incident writeup, including the separate broken-deployment issue that was
  also blocking testing at the same time.
