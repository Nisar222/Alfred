# Alfred developer handoff

## What Alfred is

Alfred is a private, on-premise telesales dashboard for Jamal. It is not a
generic CRM. The owner should be able to upload a small prospect list, choose
an approved call setup, launch a campaign, review calls, and label outcomes in
minutes.

The immediate customer-demo goal is a reliable prerecorded-message dialler
using 3CX Call Control and a Twilio-connected US trunk. The longer-term goal is
an on-premise AI voice agent that improves from outcome-labelled calls.

## Business requirements

### MVP workflow

1. Jamal creates or selects an approved **Call Playbook**.
2. He uploads a CSV (`phone,name,details`) to a campaign.
3. He sets allowed calling hours, timezone, caller ID, and capacity.
4. He starts the campaign. Alfred makes calls through 3CX.
5. Each attempt is shown in the Call Log with its status, duration, failure
   reason, playbook snapshot, recording availability, outcome, and sentiment.
6. Jamal labels outcomes: **Sale**, **Lead**, **Not interested**, or **Wrong
   number**. These labels, not sentiment, are the business source of truth.

### Scale and quality targets

- Begin at one controlled live call; target 8 concurrent calls and later 16.
- Initial volume: 50–100 calls per day.
- Calls must be reliable, recorded/transcribed when that layer is added, and
  show clear failure reasons.
- Data and future inference stay on Jamal-controlled servers. Do not add cloud
  AI/voice services to the production path without explicit approval.

### Configuration hierarchy

1. **Global Settings:** default hours/timezone, max concurrency, recording
   retention, test-call switch, and live-campaign switch.
2. **Call Playbook:** versioned script/prompt, opening audio, recording choice.
3. **Campaign:** chosen approved playbook version plus limited overrides.
4. **Call snapshot:** effective configuration stored on each queued call.

Historical calls must keep their original snapshot. Updating a playbook creates
a new version; it must not alter an old campaign silently.

## Current working state (1 August 2026)

### Proven

- Public private dashboard: `https://alfred.ayndigital.com`, protected by
  Nginx HTTP Basic Auth and TLS.
- Docker API, PostgreSQL, and Redis run on the VPS. API is bound only to
  `127.0.0.1:8000`.
- 3CX V20 Service Principal / Route Point integration works.
- The source DN is `3cxapi` (Route Point); extension `101` is authorised for
  Call Control visibility. Do **not** make app calls originate from extension
  101: streaming media belongs to the Route Point.
- A controlled external call reached a personal US mobile through the Twilio
  trunk and displayed the expected Twilio caller ID. MP3 playback works.
- Browser upload of MP3/WAV to private local VPS volume works.
- Campaign CSV upload, selected-playbook audio, campaign launch, automatic
  dispatch, 3CX call, and Call Log have been proven with an authorised test
  number.

### Current limitations / next work

1. Dispatcher now skips active campaigns that are empty, outside hours, lack a
   valid approved playbook/audio, or cannot use a slot. Continue improving the
   visible reason a campaign is waiting.
2. Implement a complete retry policy: distinguish no-answer/busy/provider
   failure, limit attempts, schedule retries inside campaign hours, and retain
   an immutable attempt history.
3. Add campaign editing before launch: playbook version, calling window,
   capacity, and caller-ID override. Preserve historical call snapshots.
4. Improve 3CX call-state reconciliation, duration accuracy, timeout handling,
   and health/alerting before raising concurrency.
5. Add backups for PostgreSQL and `audio_uploads`, an off-server encrypted
   destination, and a tested restore drill.
6. Add real recording/transcription and on-premise sentiment/QA models later;
   the current sentiment feature is a simple supporting MVP signal.

## Technology and code map

| Area | Location |
|---|---|
| FastAPI routes / UI serving | `backend/app/main.py` |
| 3CX V20 client and streaming | `backend/app/threecx.py` |
| Background campaign dispatcher | `backend/app/dispatcher.py` |
| SQLAlchemy models | `backend/app/models.py` |
| API schemas | `backend/app/schemas.py` |
| Deterministic QA/simulator | `backend/app/services.py` |
| Browser app | `backend/app/web/index.html`, `app.js`, CSS files |
| Database migrations | `backend/alembic/versions/` |
| Docker stack | `docker-compose.yml`, `backend/Dockerfile` |
| Backups | `ops/backup-postgres.sh`, `ops/restore-postgres.sh` |

## 3CX behaviour

- 3CX base URL, app ID, API key, approved extension, and filesystem paths are
  VPS `.env` secrets. Never put them in API responses, frontend code, Git, or
  chat.
- The Service Principal client ID is also the 3CX Route Point DN. `3cxapi`
  starts the call with `/callcontrol/{dn}/makecall`.
- Alfred polls the Route Point participant state until connected, streams raw
  8 kHz mono PCM converted with `ffmpeg`, then drops the participant.
- For outbound external calls, the 3CX outbound rule must allow the **3cxapi
  Route Point** to use the Twilio trunk. A rule restricted to extension 101
  will make browser calls work while Alfred calls fail.
- Individual test calls and live campaign calls are enabled in Alfred Settings,
  not environment variables. Both default to off. Before turning on live calls,
  confirm every active campaign is an authorised test or approved campaign.

## Deployment model

### Source of truth

```text
Local repo → GitHub (Nisar222/Alfred) → VPS clone → Docker deployment
```

Develop and review locally. Use the VPS for deployment, logs, and controlled
telephony tests. Do not make the VPS the only source of code changes.

### Important paths

| Environment | Path |
|---|---|
| Mac development clone | `/Users/nisarkhan/Documents/dev2/Alfred` |
| VPS deployment clone | `~/apps/alfred` |
| Dashboard | `https://alfred.ayndigital.com` |
| API health on VPS | `http://127.0.0.1:8000/health` |

### Normal VPS release

```bash
cd ~/apps/alfred
git fetch origin main
git pull --ff-only origin main
docker compose build api
docker compose up -d db redis
docker compose run --rm api alembic upgrade head
docker compose up -d --force-recreate api
docker compose ps
curl --fail http://127.0.0.1:8000/health
```

Before migrations, take a verified backup. If an old database was originally
created by `Base.metadata.create_all` and has no Alembic history, inspect its
schema and use an explicitly reviewed `alembic stamp` baseline **once** before
upgrading. This was required on the current VPS at revision `6c39c4b7ea21`.

### Live-test procedure

1. Use only a number the owner is authorised to test.
2. Create an approved playbook with local MP3/WAV opening audio.
3. Create a campaign with a current permitted calling window and capacity 1.
4. Upload one-row CSV.
5. Ensure older/empty/misconfigured campaigns are paused or the dispatcher
   will skip them; it should not allow them to block this campaign.
6. In Settings, enable **Allow live campaign calls**, save, and monitor the
   Call Log and `docker compose logs -f api`.
7. Turn live calling off again after the test.

## Data model notes

- `Campaign` owns queued `Call` rows. CSV upload creates queued calls.
- `Call` records the dialled identity, provider participant ID, timestamps,
  duration, failure reason, outcome, optional transcript/recording, and a
  frozen `configuration_snapshot_json`.
- `AudioAsset` stores metadata only. Its binary lives in Docker volume
  `audio_uploads` at `/app/media/uploads`.
- `Playbook` and `PlaybookVersion` are immutable in effect: make a new version
  and approve it rather than mutating what historical calls used.
- `GlobalSettings` is one row (`id=1`) and holds the dashboard-operated test
  and live-campaign switches.

## Required validation

```bash
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -v
node --check backend/app/web/app.js
bash -n ops/backup-postgres.sh ops/restore-postgres.sh
```

For each production change: test locally → review migration → backup → deploy
→ health check → controlled internal/test call → inspect Call Log.

## Working with Codex / Cursor / VS Code

- Put this file and root `AGENTS.md` in Git. They are the durable handoff for
  any fresh IDE agent or programmer.
- In a Remote SSH workspace, open `~/apps/alfred` and start the coding agent
  there only for server diagnostics/deployment. Do not paste secrets into the
  agent conversation.
- For normal development, work in the local clone and deploy through GitHub.
- A desktop Codex thread cannot automatically acquire the Remote SSH terminal;
  an IDE-attached agent is a separate task and should begin by reading this
  file.
