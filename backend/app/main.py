import csv
from contextlib import asynccontextmanager
from io import StringIO
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from .config import get_settings
from .database import Base, engine, get_db
from .models import Call, CallStatus, Campaign, CampaignStatus
from .schemas import CallOut, CampaignCreate, CampaignOut, Contact, ContactUploadResult, OutcomeUpdate
from .services import daily_metrics, score_call, simulate_call


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Jamal Dialler API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins.split(","), allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health():
    settings = get_settings()
    return {"status": "ok", "call_provider": settings.call_provider, "max_concurrent_calls": settings.max_concurrent_calls}


@app.post("/campaigns", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
def create_campaign(payload: CampaignCreate, db: Session = Depends(get_db)):
    if db.scalar(select(Campaign).where(Campaign.name == payload.name)):
        raise HTTPException(409, "A campaign with this name already exists")
    campaign = Campaign(**payload.model_dump())
    db.add(campaign); db.commit(); db.refresh(campaign)
    return campaign


@app.get("/campaigns", response_model=list[CampaignOut])
def list_campaigns(db: Session = Depends(get_db)):
    return db.scalars(select(Campaign).order_by(Campaign.created_at.desc())).all()


@app.post("/campaigns/{campaign_id}/launch", response_model=CampaignOut)
def launch_campaign(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign: raise HTTPException(404, "Campaign not found")
    if campaign.status in (CampaignStatus.completed, CampaignStatus.archived):
        raise HTTPException(409, "Completed or archived campaigns cannot be launched")
    campaign.status = CampaignStatus.active; db.commit(); db.refresh(campaign)
    return campaign


@app.post("/campaigns/{campaign_id}/pause", response_model=CampaignOut)
def pause_campaign(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign: raise HTTPException(404, "Campaign not found")
    if campaign.status in (CampaignStatus.completed, CampaignStatus.archived):
        raise HTTPException(409, "Completed or archived campaigns cannot be paused")
    campaign.status = CampaignStatus.paused; db.commit(); db.refresh(campaign)
    return campaign


@app.post("/campaigns/{campaign_id}/contacts", response_model=list[CallOut], status_code=status.HTTP_201_CREATED)
def add_contacts(campaign_id: int, contacts: list[Contact], db: Session = Depends(get_db)):
    if not db.get(Campaign, campaign_id): raise HTTPException(404, "Campaign not found")
    if not contacts: raise HTTPException(422, "Provide at least one contact")
    calls = [Call(campaign_id=campaign_id, phone=c.phone, prospect_name=c.name, details=c.details) for c in contacts]
    db.add_all(calls); db.commit()
    return calls


@app.post("/campaigns/{campaign_id}/contacts/csv", response_model=ContactUploadResult, status_code=status.HTTP_201_CREATED)
async def upload_contacts(campaign_id: int, file: UploadFile, db: Session = Depends(get_db)):
    if not db.get(Campaign, campaign_id): raise HTTPException(404, "Campaign not found")
    try:
        content = (await file.read()).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(422, "CSV must be UTF-8 encoded") from exc
    reader = csv.DictReader(StringIO(content))
    if not reader.fieldnames or "phone" not in reader.fieldnames:
        raise HTTPException(422, "CSV must include a phone column")
    rows = [row for row in reader if (row.get("phone") or "").strip()]
    if not rows:
        raise HTTPException(422, "CSV must include at least one phone number")
    db.add_all([
        Call(campaign_id=campaign_id, phone=row["phone"].strip(),
             prospect_name=(row.get("name") or None), details=(row.get("details") or None))
        for row in rows
    ])
    db.commit(); return {"queued": len(rows)}


@app.post("/campaigns/{campaign_id}/run-simulation", response_model=list[CallOut])
def run_simulation(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign: raise HTTPException(404, "Campaign not found")
    if campaign.status != CampaignStatus.active: raise HTTPException(409, "Launch the campaign before running calls")
    limit = get_settings().max_concurrent_calls
    calls = db.scalars(
        select(Call).where(Call.campaign_id == campaign_id, Call.status == CallStatus.queued)
        .order_by(Call.created_at, Call.id).limit(limit)
    ).all()
    for call in calls: simulate_call(call)
    db.commit(); return calls


@app.get("/calls", response_model=list[CallOut])
def list_calls(campaign_id: int | None = None, db: Session = Depends(get_db)):
    query = select(Call).options(selectinload(Call.metric), selectinload(Call._transcript)).order_by(Call.created_at.desc())
    if campaign_id: query = query.where(Call.campaign_id == campaign_id)
    return db.scalars(query).all()


@app.post("/calls/{call_id}/outcome", response_model=CallOut)
def label_outcome(call_id: int, payload: OutcomeUpdate, db: Session = Depends(get_db)):
    call = db.scalar(select(Call).options(selectinload(Call.metric)).where(Call.id == call_id))
    if not call: raise HTTPException(404, "Call not found")
    if call.status != CallStatus.completed: raise HTTPException(409, "Only completed calls can be labeled")
    call.outcome = payload.outcome
    from datetime import datetime, timezone
    call.outcome_labeled_at = datetime.now(timezone.utc)
    if not call.metric: call.metric = score_call(call)
    db.commit(); db.refresh(call)
    return call


@app.get("/metrics/daily")
def get_daily_metrics(db: Session = Depends(get_db)):
    return daily_metrics(db)


frontend = Path(__file__).parent / "web"
app.mount("/", StaticFiles(directory=frontend, html=True), name="web")
