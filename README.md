# Alfred — Phase 1 MVP

An on-premise-ready foundation for the AI telesales platform. It provides campaign management, simulated outbound-call orchestration, outcome labeling, quality scoring, and daily metrics. Real telephony and model inference are behind adapters so they can be enabled only after the 3CX and GPU environments are validated.

## Run locally

```bash
cp .env.example .env
docker compose up --build
```

Open `http://localhost:8000/docs` for the API. The dashboard is served at `http://localhost:8000/`.

For a Python-only development setup:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn app.main:app --app-dir backend --reload
```

## Current scope

- Campaign CRUD and pause/launch controls
- Contact upload using CSV (`phone,name,details`)
- Bounded, idempotent call queue (default maximum: 8 concurrent calls)
- A safe simulator for end-to-end operational testing
- Human outcome labels and deterministic QA scoring
- Daily performance summaries and prompt-version records

## Before real calls

Do not configure a production campaign until consent, calling-hour rules, caller-ID configuration, recording disclosures, retention, and 3CX credentials have been approved for the calling jurisdiction. Set `CALL_PROVIDER=threecx` only after completing the integration smoke test.

## Controlled 3CX prerecorded-message test

This is a one-number proof before campaign calling. It uses the Service Principal
Client ID as the 3CX Route Point (not a user's extension), waits for the one
configured test recipient to answer, converts the source MP3/WAV to 8 kHz mono
PCM in the container, plays it, and ends the call.

On the VPS, keep the message out of Git and place it at:

```bash
mkdir -p ~/jamal-dialler/media
# Copy the approved source message to ~/jamal-dialler/media/test-message.mp3
```

Add these values to the VPS `.env` (the test endpoint never accepts a phone
number from the browser or request body):

```bash
CALL_PROVIDER=threecx
THREECX_TEST_DESTINATION=+15551234567
THREECX_TEST_CALL_ENABLED=false
PRERECORDED_MESSAGE_PATH=/app/media/test-message.mp3
```

After deployment, verify the connection first. Only when an operator is ready
to receive the test call should they change `THREECX_TEST_CALL_ENABLED=true`,
restart the API, and explicitly invoke:

```bash
curl -X POST http://127.0.0.1:8000/integrations/3cx/test-prerecorded-message
```
