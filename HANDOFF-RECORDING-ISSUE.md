# Handoff: Recording Investigation (Resolved)

**Original date:** 2026-08-13
**Resolved:** 2026-08-13
**Status:** Closed — root causes found and fixed. See "Actual Root Causes" below.

---

## Summary

What looked like one problem ("recordings not linking to calls") was actually
two unrelated problems stacked together:

1. **The dashboard was non-functional.** A merge commit (`a23ecf6`) landed on
   `main` with unresolved git merge-conflict markers still inside
   `backend/app/web/app.js`. A Docker image was built from that broken state
   and deployed to production. A JS syntax error at the top of the file meant
   *no* JavaScript on the page executed — not just recording playback.
   Settings, campaign actions, everything was silently dead. This is very
   likely why the issue looked unresolved for days: nobody could trigger a
   working test call through the UI to check.
2. **3CX only records calls for users with the per-user `RecordCalls`
   permission enabled.** Only extensions 101 (Nisar) and 105 (Gudo) had it on.
   Every other agent extension had it off, so any campaign call routed to
   another agent would never produce a 3CX recording — independent of
   anything in Alfred's code.

Neither of the two theories in the original version of this doc (a 3CX
Route Point recording setting, or a regression in the phone-matching logic
in `recordings.py`) was the actual cause. Both were reasonable guesses made
without directly querying 3CX's raw `/xapi/v1/Users` payload or checking
whether the deployed frontend matched the git source — worth remembering as
a lesson for future investigations: verify what's actually running/configured
before fixing code.

---

## ✅ Already Fixed & Deployed (prior work, still valid)

1. **Ghost Calls** - Complete ghost call monitoring system deployed:
   - `dispatcher.py` - Fixed commit error handling
   - `ghost_monitor.py` - New background service (checks every 5min)
   - `main.py` - Integrated into lifespan
   - `/health/ghost-calls` endpoint added
   - **Status:** Live on VPS, protecting campaigns

2. **UI Fixes** - Committed (commit d12256c):
   - Fixed `formatCallLogTime` undefined error
   - Changed default filter to "All"
   - Added campaign filter dropdown
   - Mobile scrolling improvements
   - Better empty states

3. **Settings** - Form binding fixed with `settingsFormBound` flag

4. **3CX Permissions** - Call Control API enabled and working

---

## Actual Root Causes & Fixes

### 1. Broken production image (app.js merge conflict)

- **Cause:** `a23ecf6` ("Merge ghost call fixes from origin") was committed
  with 6 unresolved `<<<<<<<`/`=======`/`>>>>>>>` blocks still in
  `backend/app/web/app.js`. It was fixed two commits later in `d12256c`, but
  an image had already been built from the broken `a23ecf6` state and never
  rebuilt after the fix landed.
- **Fix:** Rebuilt the API image from the current (clean) working tree and
  redeployed (`docker compose build api && docker compose up -d api`).
  Verified the served `/app.js` has zero conflict markers and byte-matches
  the git working tree.
- **Prevention:** See `.github/workflows/ci.yml` — every push/PR now fails
  fast if conflict markers exist anywhere in `backend/`. There's also a local
  pre-commit hook (`.pre-commit-config.yaml`) and a post-deploy smoke check
  (`./alfred-vps.sh verify`, folded into `./alfred-vps.sh deploy`).

### 2. Per-user 3CX recording permission

- **Cause:** 3CX's `RecordCalls` user setting was `True` only for extensions
  101 and 105. Confirmed directly via the raw XAPI payload
  (`/xapi/v1/Users`, field `RecordCalls`) — Alfred's own client only exposes
  a narrow, safe field subset (id/name/extension/email) by design, so this
  was invisible anywhere in Alfred's code, logs, or UI.
- **Fix:** Enabled `RecordCalls` for all agent extensions in the 3CX admin
  console (102, 104, 105, 107, 108, 109). Extension 100 (David Eriksson) was
  never enabled — David manages his own extension.
- **Verification:** Live end-to-end test on 2026-08-13 — placed a test call
  (Alfred call id 4824) via `/integrations/3cx/test-call`, pressed DTMF `1`,
  call routed to extension 101, 3CX produced recording `Id 85`, Alfred's
  `RecordingSync` background worker linked it automatically within 30s, and
  the recording streamed successfully via `/calls/4824/recording` (200 OK).
- **Update (2026-08-13, later same day):** The customer requested recording
  be turned off for now. `RecordCalls` was reverted to `False` for every
  extension except 101 (kept on for Alfred's own diagnostic/test calls).
  **This is intentional, not a regression** — if a future check finds most
  extensions with `RecordCalls: False` again, confirm with the customer
  before treating it as a bug.

### Outstanding

- [ ] Recording is currently OFF for all real agent extensions by customer
      request. Re-enable per-extension in the 3CX admin console (`RecordCalls`)
      when the customer wants recording back on.
- [ ] No automated regression test covers "every enabled extension has
      `RecordCalls` on" — this would need to poll 3CX directly (out of scope
      for the unit test suite, which never talks to live 3CX per `AGENTS.md`).
      Low priority now that recording is intentionally off.

---

## Reference: How recording sync actually works (still accurate)

1. **RecordingSync** (daemon thread) runs every 30 seconds.
2. Calls `sync_threecx_recordings_safe(db, settings)` — a no-op, with no log
   output at all, if `CALL_PROVIDER != "threecx"`. This makes log-grepping
   for "record" an unreliable diagnostic signal on its own; check the DB
   `recordings` table and 3CX's `/xapi/v1/Recordings` directly instead.
3. Fetches recordings from 3CX XAPI: `client.list_xapi_recordings()`.
4. For each 3CX recording, extracts phone numbers from metadata and finds a
   matching Alfred call within a 15-minute window (`recordings.py`).
5. Frontend checks `call.recording_available`; audio streams from
   `/calls/{id}/recording`.

### Key files
- `backend/app/recordings.py` — matching logic
- `backend/app/recording_sync.py` — background worker
- `backend/app/main.py` — lifespan integration, `/calls/{id}/recording`
- `backend/app/web/app.js` — audio player rendering in the Call Log

---

## Deploying safely (updated process)

Use `./alfred-vps.sh deploy` (pulls the CI-tested GHCR image; verifies the
served frontend afterward) rather than a bare `git pull && restart`, which
does **not** rebuild the image and would have silently kept serving the
broken `app.js` even after `d12256c` fixed it in git. See
`.github/workflows/ci.yml` and the comments in `docker-compose.yml` /
`alfred-vps.sh` for the full flow.
