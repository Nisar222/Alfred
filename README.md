# Jamal Dialler — Phase 1 MVP

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

