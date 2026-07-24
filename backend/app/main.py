import csv
import hashlib
import os
import uuid
from contextlib import asynccontextmanager
from io import StringIO
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Response, UploadFile, File, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from .config import get_settings
from .database import Base, engine, get_db
from .models import (AudioAsset, AudioAssetStatus, Call, CallStatus, Campaign, CampaignStatus,
                     GlobalSettings, Playbook, PlaybookStatus, PlaybookVersion)
from .schemas import (AudioAssetOut, CallOut, CampaignCreate, CampaignOut, Contact, ContactUploadResult,
                      GlobalSettingsOut, GlobalSettingsUpdate, OutcomeUpdate, PlaybookCreate,
                      PlaybookOut, PlaybookVersionCreate, PlaybookVersionOut)
from .services import daily_metrics, score_call, simulate_call
from .threecx import ThreeCXClient, ThreeCXError


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Jamal Dialler API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins.split(","), allow_methods=["*"], allow_headers=["*"])


def _global_settings(db: Session) -> GlobalSettings:
    settings = db.get(GlobalSettings, 1)
    if settings is None:
        settings = GlobalSettings(id=1)
        db.add(settings); db.flush()
    return settings


def _call_snapshot(campaign: Campaign, db: Session) -> dict:
    global_settings = _global_settings(db)
    playbook = campaign.playbook_version
    return {
        "global": {"timezone": global_settings.default_timezone, "max_concurrent_calls": global_settings.max_concurrent_calls,
                   "recording_retention_days": global_settings.recording_retention_days},
        "campaign": {"timezone": campaign.timezone, "calling_window": campaign.calling_window_json,
                     "caller_id": campaign.caller_id_override,
                     "max_concurrent_calls": campaign.max_concurrent_calls_override},
        "playbook": None if playbook is None else {"id": playbook.playbook_id, "name": playbook.playbook.name, "version_id": playbook.id,
                     "version": playbook.version, "script": playbook.script, "opening_audio_id": playbook.opening_audio_id,
                     "recording_enabled": playbook.recording_enabled},
    }


def _queued_call(campaign: Campaign, phone: str, name: str | None, details: str | None, db: Session) -> Call:
    return Call(campaign_id=campaign.id, phone=phone, prospect_name=name, details=details,
                configuration_snapshot_json=_call_snapshot(campaign, db))


@app.get("/health")
def health():
    settings = get_settings()
    return {"status": "ok", "call_provider": settings.call_provider, "max_concurrent_calls": settings.max_concurrent_calls}


@app.post("/integrations/3cx/verify")
def verify_threecx():
    """Verify credentials and extension visibility. This endpoint never places a call."""
    settings = get_settings()
    if settings.call_provider != "threecx":
        raise HTTPException(409, "3CX is disabled. Set CALL_PROVIDER=threecx only for the controlled test.")
    client = None
    try:
        client = ThreeCXClient(settings)
        devices = client.list_devices()
    except ThreeCXError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        if client:
            client.close()
    return {
        "status": "connected",
        "extension": settings.threecx_control_extension,
        "devices": [{"id": device.device_id, "user_agent": device.user_agent} for device in devices],
    }


@app.post("/integrations/3cx/inspect")
def inspect_threecx():
    """Read-only Route Point inspection for 3CX integration troubleshooting."""
    settings = get_settings()
    if settings.call_provider != "threecx":
        raise HTTPException(409, "3CX is disabled. Set CALL_PROVIDER=threecx for the controlled test.")
    client = None
    try:
        client = ThreeCXClient(settings)
        entities = client.inspect_accessible_dns()
    except ThreeCXError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        if client:
            client.close()
    return {"configured_source_dn": settings.threecx_app_id, "entities": entities}


@app.post("/integrations/3cx/test-prerecorded-message")
def test_prerecorded_message(db: Session = Depends(get_db)):
    """Place one explicitly enabled test call; recipient is never client supplied."""
    settings = get_settings()
    if settings.call_provider != "threecx":
        raise HTTPException(409, "3CX is disabled. Set CALL_PROVIDER=threecx for the controlled test.")
    if not settings.threecx_test_call_enabled or not _global_settings(db).test_call_enabled:
        raise HTTPException(409, "Test calling is locked. Enable it in Alfred Settings and on the VPS when ready.")
    if not settings.threecx_test_destination:
        raise HTTPException(409, "Set the single approved test destination on the VPS before calling.")
    client = None
    call = None
    try:
        client = ThreeCXClient(settings)
        call = client.start_test_call(settings.threecx_test_destination)
        client.wait_until_connected(call)
        client.play_prerecorded_message(call, Path(settings.prerecorded_message_path))
        client.drop_call(call)
    except ThreeCXError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        if client:
            client.close()
    return {"status": "completed", "destination": settings.threecx_test_destination, "message": "prerecorded message played"}


@app.get("/settings", response_model=GlobalSettingsOut)
def get_global_settings(db: Session = Depends(get_db)):
    settings = _global_settings(db)
    db.commit(); db.refresh(settings)
    return settings


@app.put("/settings", response_model=GlobalSettingsOut)
def update_global_settings(payload: GlobalSettingsUpdate, db: Session = Depends(get_db)):
    settings = _global_settings(db)
    for key, value in payload.model_dump().items():
        setattr(settings, key, value)
    db.commit(); db.refresh(settings)
    return settings


@app.post("/audio-assets", response_model=AudioAssetOut, status_code=status.HTTP_201_CREATED)
async def upload_audio_asset(file: UploadFile = File(...), db: Session = Depends(get_db)):
    allowed = {"audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav"}
    suffix = Path(file.filename or "").suffix.lower()
    content_type = (file.content_type or "").lower()
    if suffix not in {".mp3", ".wav"} or (content_type and content_type not in allowed):
        raise HTTPException(422, "Upload an MP3 or WAV audio file")
    raw = await file.read(get_settings().max_audio_upload_bytes + 1)
    if not raw:
        raise HTTPException(422, "Audio file is empty")
    if len(raw) > get_settings().max_audio_upload_bytes:
        raise HTTPException(413, "Audio file is too large")
    checksum = hashlib.sha256(raw).hexdigest()
    existing = db.scalar(select(AudioAsset).where(AudioAsset.checksum == checksum, AudioAsset.status == AudioAssetStatus.ready))
    if existing:
        return existing
    storage_dir = Path(get_settings().audio_storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_key = f"{uuid.uuid4().hex}{suffix}"
    destination = storage_dir / storage_key
    try:
        destination.write_bytes(raw)
    except OSError as exc:
        raise HTTPException(500, "Alfred could not store the audio file locally") from exc
    asset = AudioAsset(display_name=Path(file.filename or storage_key).name, storage_key=storage_key,
                       content_type=content_type or allowed.get(content_type, "audio/mpeg"), size_bytes=len(raw), checksum=checksum)
    db.add(asset); db.commit(); db.refresh(asset)
    return asset


@app.get("/audio-assets", response_model=list[AudioAssetOut])
def list_audio_assets(db: Session = Depends(get_db)):
    return db.scalars(select(AudioAsset).where(AudioAsset.status == AudioAssetStatus.ready).order_by(AudioAsset.created_at.desc())).all()


@app.delete("/audio-assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_audio_asset(asset_id: int, db: Session = Depends(get_db)):
    asset = db.get(AudioAsset, asset_id)
    if not asset or asset.status == AudioAssetStatus.deleted:
        raise HTTPException(404, "Audio file not found")
    if db.scalar(select(PlaybookVersion.id).where(PlaybookVersion.opening_audio_id == asset_id).limit(1)):
        raise HTTPException(409, "This audio file is used by a playbook and cannot be deleted")
    path = Path(get_settings().audio_storage_dir) / asset.storage_key
    try:
        if path.exists(): path.unlink()
    except OSError as exc:
        raise HTTPException(500, "Alfred could not remove the local audio file") from exc
    asset.status = AudioAssetStatus.deleted
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _valid_audio(asset_id: int | None, db: Session) -> None:
    if asset_id is not None and not db.scalar(select(AudioAsset.id).where(AudioAsset.id == asset_id, AudioAsset.status == AudioAssetStatus.ready)):
        raise HTTPException(422, "Select an available audio file")


@app.post("/playbooks", response_model=PlaybookOut, status_code=status.HTTP_201_CREATED)
def create_playbook(payload: PlaybookCreate, db: Session = Depends(get_db)):
    if db.scalar(select(Playbook).where(Playbook.name == payload.name)):
        raise HTTPException(409, "A playbook with this name already exists")
    _valid_audio(payload.opening_audio_id, db)
    state = PlaybookStatus.approved if payload.approve else PlaybookStatus.draft
    playbook = Playbook(name=payload.name, status=state)
    db.add(playbook); db.flush()
    version = PlaybookVersion(playbook_id=playbook.id, version=1, script=payload.script,
                              opening_audio_id=payload.opening_audio_id, recording_enabled=payload.recording_enabled, status=state)
    db.add(version); db.flush()
    playbook.current_version_id = version.id
    db.commit(); db.refresh(playbook)
    return playbook


@app.get("/playbooks", response_model=list[PlaybookOut])
def list_playbooks(db: Session = Depends(get_db)):
    return db.scalars(select(Playbook).options(selectinload(Playbook.versions)).order_by(Playbook.created_at.desc())).all()


@app.post("/playbooks/{playbook_id}/versions", response_model=PlaybookVersionOut, status_code=status.HTTP_201_CREATED)
def create_playbook_version(playbook_id: int, payload: PlaybookVersionCreate, db: Session = Depends(get_db)):
    playbook = db.get(Playbook, playbook_id)
    if not playbook or playbook.status == PlaybookStatus.retired:
        raise HTTPException(404, "Playbook not found")
    _valid_audio(payload.opening_audio_id, db)
    latest = db.scalar(select(PlaybookVersion.version).where(PlaybookVersion.playbook_id == playbook_id).order_by(PlaybookVersion.version.desc()).limit(1)) or 0
    state = PlaybookStatus.approved if payload.approve else PlaybookStatus.draft
    version = PlaybookVersion(playbook_id=playbook_id, version=latest + 1, script=payload.script,
                              opening_audio_id=payload.opening_audio_id, recording_enabled=payload.recording_enabled, status=state)
    db.add(version); db.flush()
    if payload.approve:
        playbook.status = PlaybookStatus.approved; playbook.current_version_id = version.id
    db.commit(); db.refresh(version)
    return version


@app.post("/playbooks/{playbook_id}/versions/{version_id}/approve", response_model=PlaybookVersionOut)
def approve_playbook_version(playbook_id: int, version_id: int, db: Session = Depends(get_db)):
    version = db.scalar(select(PlaybookVersion).where(PlaybookVersion.id == version_id, PlaybookVersion.playbook_id == playbook_id))
    if not version: raise HTTPException(404, "Playbook version not found")
    version.status = PlaybookStatus.approved; version.playbook.status = PlaybookStatus.approved; version.playbook.current_version_id = version.id
    db.commit(); db.refresh(version)
    return version


@app.post("/campaigns", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
def create_campaign(payload: CampaignCreate, db: Session = Depends(get_db)):
    if db.scalar(select(Campaign).where(Campaign.name == payload.name)):
        raise HTTPException(409, "A campaign with this name already exists")
    values = payload.model_dump()
    playbook_version_id = values.get("playbook_version_id")
    if playbook_version_id is not None:
        version = db.get(PlaybookVersion, playbook_version_id)
        if not version or version.status != PlaybookStatus.approved:
            raise HTTPException(422, "Campaigns must use an approved playbook version")
        # The compatibility script is never the source of truth when a playbook is selected.
        values["script"] = version.script
    settings = _global_settings(db)
    if values["max_concurrent_calls_override"] and values["max_concurrent_calls_override"] > settings.max_concurrent_calls:
        raise HTTPException(422, "Campaign call limit cannot exceed the system limit")
    values["timezone"] = values["timezone"] or settings.default_timezone
    values["calling_window_json"] = values["calling_window_json"] if values["calling_window_json"] is not None else settings.default_calling_window_json
    campaign = Campaign(**values)
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
    campaign = db.get(Campaign, campaign_id)
    if not campaign: raise HTTPException(404, "Campaign not found")
    if not contacts: raise HTTPException(422, "Provide at least one contact")
    calls = [_queued_call(campaign, c.phone, c.name, c.details, db) for c in contacts]
    db.add_all(calls); db.commit()
    return calls


@app.post("/campaigns/{campaign_id}/contacts/csv", response_model=ContactUploadResult, status_code=status.HTTP_201_CREATED)
async def upload_contacts(campaign_id: int, file: UploadFile, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign: raise HTTPException(404, "Campaign not found")
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
        _queued_call(campaign, row["phone"].strip(), (row.get("name") or None), (row.get("details") or None), db)
        for row in rows
    ])
    db.commit(); return {"queued": len(rows)}


@app.post("/campaigns/{campaign_id}/run-simulation", response_model=list[CallOut])
def run_simulation(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign: raise HTTPException(404, "Campaign not found")
    if campaign.status != CampaignStatus.active: raise HTTPException(409, "Launch the campaign before running calls")
    global_settings = _global_settings(db)
    limit = campaign.max_concurrent_calls_override or global_settings.max_concurrent_calls
    calls = db.scalars(
        select(Call).where(Call.campaign_id == campaign_id, Call.status == CallStatus.queued)
        .order_by(Call.created_at, Call.id).limit(limit)
    ).all()
    for call in calls: simulate_call(call)
    db.commit(); return calls


@app.get("/calls", response_model=list[CallOut])
def list_calls(campaign_id: int | None = None, db: Session = Depends(get_db)):
    query = select(Call).options(selectinload(Call.metric), selectinload(Call._transcript), selectinload(Call.recording)).order_by(Call.created_at.desc())
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
