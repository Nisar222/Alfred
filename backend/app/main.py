import csv
import hashlib
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Response, UploadFile, File, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload
from .config import get_settings
from .database import Base, SessionLocal, engine, get_db
from .models import (AgentNotification, AudioAsset, AudioAssetStatus, AuthSession, Call, CallStatus, Campaign, CampaignStatus,
                     GlobalSettings, Playbook, PlaybookStatus, PlaybookVersion, User)
from .schemas import (AudioAssetOut, CallOut, CampaignCreate, CampaignOut, Contact, ContactUploadResult, DtmfDiagnosticOut,
                      GlobalSettingsOut, GlobalSettingsUpdate, OutcomeUpdate, PlaybookCreate,
                      PlaybookOut, PlaybookVersionCreate, PlaybookVersionOut, SentimentUpdate, TestCallRequest,
                      ThreeCXDirectoryOut, CurrentUserOut, LoginOut, LoginRequest, PasswordChangeRequest, AdminUserCreate, AdminUserOut,
                      ThreeCXLinkUpdate, AdminUserAccessUpdate, AgentNotificationOut)
from .auth import SESSION_COOKIE, create_session, current_session, current_user, hash_password, require_csrf, require_roles, verify_password
from .services import analyze_sentiment, daily_metrics, score_call, simulate_call
from .threecx import ThreeCXClient, ThreeCXError
from .dispatcher import CampaignDispatcher, DispatchError, place_next_call
from .notifications import ensure_diagnostic_routing_notification
from .recording_sync import RecordingSync
from .recordings import parse_threecx_recording_id, sync_threecx_recordings_safe
from .transcript_sync import TranscriptSync


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    dispatcher = CampaignDispatcher()
    recording_sync = RecordingSync()
    transcript_sync = TranscriptSync()
    dispatcher.start()
    recording_sync.start()
    transcript_sync.start()
    settings = get_settings()
    if settings.call_provider == "threecx":
        with SessionLocal() as db:
            sync_threecx_recordings_safe(db, settings)
    try:
        yield
    finally:
        transcript_sync.stop()
        recording_sync.stop()
        dispatcher.stop()


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
               "recording_retention_days": global_settings.recording_retention_days,
               "retry_max_attempts": global_settings.retry_max_attempts,
               "retry_delay_minutes": global_settings.retry_delay_minutes,
               "retry_no_answer": global_settings.retry_no_answer,
               "retry_busy": global_settings.retry_busy,
               "retry_provider_failure": global_settings.retry_provider_failure,
               "dtmf_routing_enabled": global_settings.dtmf_routing_enabled,
               "dtmf_menu_digit": global_settings.dtmf_menu_digit,
               "dtmf_queue_extension": global_settings.dtmf_queue_extension},
        "campaign": {"timezone": campaign.timezone, "calling_window": campaign.calling_window_json,
                     "caller_id": campaign.caller_id_override,
                     "max_concurrent_calls": campaign.max_concurrent_calls_override,
                     "dtmf_queue_extension": campaign.dtmf_queue_extension_override or global_settings.dtmf_queue_extension},
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


def _current_user_out(user: User) -> dict:
    return {
        "id": user.id, "email": user.email, "display_name": user.display_name,
        "role": user.role, "threecx_extension": user.threecx_extension,
    }


@app.post("/auth/login", response_model=LoginOut)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    """Create a new opaque browser session; failures do not reveal account existence."""
    identifier = payload.email.strip().lower()
    user = db.scalar(select(User).where(or_(User.email == identifier, User.threecx_extension == identifier)))
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email or password is incorrect.")
    token, csrf_token, _session = create_session(db, user)
    db.commit()
    response.set_cookie(
        SESSION_COOKIE, token, max_age=get_settings().session_ttl_hours * 3600,
        secure=True, httponly=True, samesite="strict", path="/",
    )
    return {"user": _current_user_out(user), "csrf_token": csrf_token}


@app.get("/auth/me", response_model=CurrentUserOut)
def me(user: User = Depends(current_user)):
    return _current_user_out(user)


@app.post("/auth/csrf")
def refresh_csrf(session: AuthSession = Depends(current_session), db: Session = Depends(get_db)):
    """Issue a replacement CSRF token after a browser refresh; only its hash is stored."""
    import hashlib
    import secrets
    token = secrets.token_urlsafe(32)
    session.csrf_token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    db.commit()
    return {"csrf_token": token}


@app.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, session: AuthSession = Depends(require_csrf), db: Session = Depends(get_db)):
    session.revoked_at = datetime.now(timezone.utc)
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@app.get("/agent/notifications", response_model=list[AgentNotificationOut])
def list_agent_notifications(unread_only: bool = True, user: User = Depends(current_user),
                             db: Session = Depends(get_db)):
    query = select(AgentNotification)
    if user.role != "owner":
        recipient = [AgentNotification.recipient_user_id == user.id]
        if user.threecx_extension:
            recipient.append(AgentNotification.recipient_extension == user.threecx_extension)
        query = query.where(or_(*recipient))
    if unread_only:
        query = query.where(AgentNotification.read_at.is_(None))
    notifications = db.scalars(query.order_by(AgentNotification.created_at.desc(), AgentNotification.id.desc()).limit(100)).all()
    if user.role != "owner":
        now = datetime.now(timezone.utc)
        changed = False
        for notification in notifications:
            if notification.delivered_at is None:
                notification.delivered_at = now
                changed = True
        if changed:
            db.commit()
    return notifications


@app.post("/agent/notifications/{notification_id}/ack", response_model=AgentNotificationOut)
def acknowledge_agent_notification(notification_id: int, user: User = Depends(current_user),
                                   _csrf: AuthSession = Depends(require_csrf), db: Session = Depends(get_db)):
    notification = db.get(AgentNotification, notification_id)
    if not notification:
        raise HTTPException(404, "Agent notification not found.")
    addressed_to_user = notification.recipient_user_id == user.id
    addressed_to_extension = bool(user.threecx_extension and notification.recipient_extension == user.threecx_extension)
    if user.role != "owner" and not (addressed_to_user or addressed_to_extension):
        raise HTTPException(404, "Agent notification not found.")
    now = datetime.now(timezone.utc)
    notification.delivered_at = notification.delivered_at or now
    notification.read_at = notification.read_at or now
    db.commit()
    db.refresh(notification)
    return notification


@app.put("/auth/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(payload: PasswordChangeRequest, session: AuthSession = Depends(require_csrf),
                    db: Session = Depends(get_db)):
    if not verify_password(payload.current_password, session.user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password is incorrect.")
    session.user.password_hash = hash_password(payload.new_password)
    now = datetime.now(timezone.utc)
    for other in db.scalars(select(AuthSession).where(
        AuthSession.user_id == session.user_id, AuthSession.id != session.id,
        AuthSession.revoked_at.is_(None),
    )):
        other.revoked_at = now
    db.commit()


def _admin_user_out(user: User) -> dict:
    return {**_current_user_out(user), "is_active": user.is_active, "threecx_user_id": user.threecx_user_id}


@app.get("/admin/users", response_model=list[AdminUserOut])
def list_alfred_users(_owner: User = Depends(require_roles("owner")), db: Session = Depends(get_db)):
    return [_admin_user_out(user) for user in db.scalars(select(User).order_by(User.display_name)).all()]


@app.post("/admin/users", response_model=AdminUserOut, status_code=status.HTTP_201_CREATED)
def create_alfred_agent(payload: AdminUserCreate, _owner: User = Depends(require_roles("owner")),
                         _csrf: AuthSession = Depends(require_csrf), db: Session = Depends(get_db)):
    """Create an inactive local account; an owner must provision its password separately."""
    if db.scalar(select(User.id).where(User.email == payload.email.strip().lower())):
        raise HTTPException(409, "An Alfred user already has that email.")
    if payload.threecx_user_id and db.scalar(select(User.id).where(User.threecx_user_id == payload.threecx_user_id)):
        raise HTTPException(409, "That 3CX user is already linked to Alfred.")
    if payload.threecx_extension and db.scalar(select(User.id).where(User.threecx_extension == payload.threecx_extension)):
        raise HTTPException(409, "That 3CX extension is already linked to Alfred.")
    user = User(email=payload.email.strip().lower(), display_name=payload.display_name.strip(), role=payload.role,
                is_active=False, threecx_user_id=payload.threecx_user_id,
                threecx_extension=payload.threecx_extension)
    db.add(user)
    db.commit()
    db.refresh(user)
    return _admin_user_out(user)


@app.put("/admin/users/{user_id}/threecx-link", response_model=AdminUserOut)
def update_alfred_user_link(user_id: int, payload: ThreeCXLinkUpdate, _owner: User = Depends(require_roles("owner")),
                             _csrf: AuthSession = Depends(require_csrf), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "Alfred user not found.")
    if not payload.threecx_user_id and not payload.threecx_extension:
        raise HTTPException(422, "Choose a 3CX user or extension to link.")
    duplicate = db.scalar(select(User.id).where(
        User.id != user.id,
        (User.threecx_user_id == payload.threecx_user_id) | (User.threecx_extension == payload.threecx_extension),
    ))
    if duplicate:
        raise HTTPException(409, "That 3CX identity is already linked to another Alfred user.")
    user.threecx_user_id = payload.threecx_user_id
    user.threecx_extension = payload.threecx_extension
    user.threecx_last_synced_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return _admin_user_out(user)


@app.put("/admin/users/{user_id}/access", response_model=AdminUserOut)
def set_alfred_user_access(user_id: int, payload: AdminUserAccessUpdate, _owner: User = Depends(require_roles("owner")),
                            _csrf: AuthSession = Depends(require_csrf), db: Session = Depends(get_db)):
    """Owner sets an account password explicitly; no password is returned or logged."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "Alfred user not found.")
    user.password_hash = hash_password(payload.password)
    user.is_active = payload.is_active
    db.commit()
    db.refresh(user)
    return _admin_user_out(user)


@app.post("/integrations/3cx/verify", dependencies=[Depends(require_roles("owner")), Depends(require_csrf)])
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


@app.post("/integrations/3cx/inspect", dependencies=[Depends(require_roles("owner")), Depends(require_csrf)])
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
    return {"entities": entities}


@app.get("/integrations/3cx/directory", response_model=ThreeCXDirectoryOut,
         dependencies=[Depends(require_roles("owner"))])
def get_threecx_directory():
    """Read the visible 3CX people and destinations without changing either system.

    The response is intentionally limited to the identity fields needed for an
    owner to approve a later Alfred-user link.  It never includes API settings
    or any credentials.
    """
    settings = get_settings()
    if settings.call_provider != "threecx":
        raise HTTPException(409, "3CX is disabled. Enable it only while administering the integration.")
    client = None
    try:
        client = ThreeCXClient(settings)
        users, ring_groups, queues = client.list_xapi_directory()
    except ThreeCXError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        if client:
            client.close()
    return {
        "users": [user.__dict__ for user in users],
        "ring_groups": [
            {**group.__dict__, "members": [member.__dict__ for member in group.members]}
            for group in ring_groups
        ],
        "queues": [
            {**group.__dict__, "members": [member.__dict__ for member in group.members]}
            for group in queues
        ],
    }


@app.post("/integrations/3cx/test-dtmf", response_model=DtmfDiagnosticOut,
          dependencies=[Depends(require_roles("owner")), Depends(require_csrf)])
def test_threecx_dtmf(db: Session = Depends(get_db)):
    """Place one approved diagnostic call and capture one Route Point DTMF digit."""
    settings = get_settings()
    if settings.call_provider != "threecx":
        raise HTTPException(409, "3CX is disabled. Enable it only for the controlled test.")
    if not _global_settings(db).test_call_enabled:
        raise HTTPException(409, "Test calling is locked. Enable it in Alfred Settings when ready.")
    if not settings.threecx_test_destination:
        raise HTTPException(409, "Set the single approved test destination on the VPS before calling.")
    client = None
    provider_call = None
    digit = None
    dropped = False
    try:
        client = ThreeCXClient(settings)
        provider_call = client.start_test_call(settings.threecx_test_destination)
        client.wait_until_connected(provider_call)
        with client.monitor_dtmf(provider_call) as monitor:
            digit, finish_playback = client.play_prerecorded_message_with_dtmf(
                provider_call, monitor, Path(settings.prerecorded_message_path), timeout_seconds=15,
            )
            try:
                routing = _global_settings(db)
                if (routing.dtmf_routing_enabled and routing.dtmf_queue_extension
                        and digit == routing.dtmf_menu_digit):
                    try:
                        recipient_extension = client.single_member_extension(routing.dtmf_queue_extension)
                    except ThreeCXError:
                        recipient_extension = None
                    ensure_diagnostic_routing_notification(
                        db,
                        destination=routing.dtmf_queue_extension,
                        digit=digit,
                        recipient_extension=recipient_extension,
                    )
                    db.commit()
                    client.route_to(provider_call, routing.dtmf_queue_extension, 0)
                    dropped = True
                    return {"status": "routed", "digit": digit, "destination": routing.dtmf_queue_extension}
                try:
                    client.drop_call(provider_call)
                except ThreeCXError:
                    pass
                finally:
                    dropped = True
            finally:
                finish_playback()
    except ThreeCXError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        if client:
            if provider_call is not None and not dropped:
                try:
                    client.drop_call(provider_call)
                except ThreeCXError:
                    pass
            client.close()
    return {"status": "received" if digit else "no_input", "digit": digit, "destination": None}


@app.post("/integrations/3cx/test-prerecorded-message",
          dependencies=[Depends(require_roles("owner")), Depends(require_csrf)])
def test_prerecorded_message(db: Session = Depends(get_db)):
    """Place one explicitly enabled test call; recipient is never client supplied."""
    settings = get_settings()
    if settings.call_provider != "threecx":
        raise HTTPException(409, "3CX is disabled. Set CALL_PROVIDER=threecx for the controlled test.")
    if not _global_settings(db).test_call_enabled:
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


@app.post("/integrations/3cx/test-call",
          dependencies=[Depends(require_roles("owner")), Depends(require_csrf)])
def place_individual_test_call(payload: TestCallRequest, db: Session = Depends(get_db)):
    """An operator-triggered, one-off test call; never part of a campaign."""
    settings = get_settings()
    if settings.call_provider != "threecx":
        raise HTTPException(409, "3CX is not configured on this VPS")
    if not _global_settings(db).test_call_enabled:
        raise HTTPException(409, "Test calling is locked. Enable it in Alfred Settings and on the VPS when ready.")
    client = None
    call = None
    try:
        client = ThreeCXClient(settings)
        call = client.start_test_call(payload.destination)
        client.wait_until_connected(call)
        client.play_prerecorded_message(call, Path(settings.prerecorded_message_path))
        client.drop_call(call)
    except ThreeCXError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        if client:
            client.close()
    return {"status": "completed", "destination": payload.destination, "message": "prerecorded message played"}


@app.get("/settings", response_model=GlobalSettingsOut, dependencies=[Depends(require_roles("owner"))])
def get_global_settings(db: Session = Depends(get_db)):
    settings = _global_settings(db)
    db.commit(); db.refresh(settings)
    return settings


@app.put("/settings", response_model=GlobalSettingsOut,
         dependencies=[Depends(require_roles("owner")), Depends(require_csrf)])
def update_global_settings(payload: GlobalSettingsUpdate, db: Session = Depends(get_db)):
    if payload.dtmf_routing_enabled and not payload.dtmf_queue_extension:
        raise HTTPException(422, "Enter a default queue extension before enabling keypad routing")
    settings = _global_settings(db)
    for key, value in payload.model_dump().items():
        setattr(settings, key, value)
    db.commit(); db.refresh(settings)
    return settings


@app.post("/audio-assets", response_model=AudioAssetOut, status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(require_roles("owner", "supervisor")), Depends(require_csrf)])
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


@app.get("/audio-assets", response_model=list[AudioAssetOut], dependencies=[Depends(current_user)])
def list_audio_assets(db: Session = Depends(get_db)):
    return db.scalars(select(AudioAsset).where(AudioAsset.status == AudioAssetStatus.ready).order_by(AudioAsset.created_at.desc())).all()


@app.delete("/audio-assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT,
            dependencies=[Depends(require_roles("owner", "supervisor")), Depends(require_csrf)])
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


@app.post("/playbooks", response_model=PlaybookOut, status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(require_roles("owner", "supervisor")), Depends(require_csrf)])
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


@app.get("/playbooks", response_model=list[PlaybookOut], dependencies=[Depends(current_user)])
def list_playbooks(db: Session = Depends(get_db)):
    return db.scalars(select(Playbook).options(selectinload(Playbook.versions)).order_by(Playbook.created_at.desc())).all()


@app.post("/playbooks/{playbook_id}/versions", response_model=PlaybookVersionOut, status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(require_roles("owner", "supervisor")), Depends(require_csrf)])
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


@app.post("/playbooks/{playbook_id}/versions/{version_id}/approve", response_model=PlaybookVersionOut,
          dependencies=[Depends(require_roles("owner", "supervisor")), Depends(require_csrf)])
def approve_playbook_version(playbook_id: int, version_id: int, db: Session = Depends(get_db)):
    version = db.scalar(select(PlaybookVersion).where(PlaybookVersion.id == version_id, PlaybookVersion.playbook_id == playbook_id))
    if not version: raise HTTPException(404, "Playbook version not found")
    version.status = PlaybookStatus.approved; version.playbook.status = PlaybookStatus.approved; version.playbook.current_version_id = version.id
    db.commit(); db.refresh(version)
    return version


@app.post("/campaigns", response_model=CampaignOut, status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(require_roles("owner", "supervisor")), Depends(require_csrf)])
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


@app.get("/campaigns", response_model=list[CampaignOut], dependencies=[Depends(current_user)])
def list_campaigns(db: Session = Depends(get_db)):
    return db.scalars(select(Campaign).order_by(Campaign.created_at.desc())).all()


@app.post("/campaigns/{campaign_id}/launch", response_model=CampaignOut,
          dependencies=[Depends(require_roles("owner", "supervisor")), Depends(require_csrf)])
def launch_campaign(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign: raise HTTPException(404, "Campaign not found")
    if campaign.status in (CampaignStatus.completed, CampaignStatus.archived):
        raise HTTPException(409, "Completed or archived campaigns cannot be launched")
    campaign.status = CampaignStatus.active; db.commit(); db.refresh(campaign)
    return campaign


@app.post("/campaigns/{campaign_id}/pause", response_model=CampaignOut,
          dependencies=[Depends(require_roles("owner", "supervisor")), Depends(require_csrf)])
def pause_campaign(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign: raise HTTPException(404, "Campaign not found")
    if campaign.status in (CampaignStatus.completed, CampaignStatus.archived):
        raise HTTPException(409, "Completed or archived campaigns cannot be paused")
    campaign.status = CampaignStatus.paused; db.commit(); db.refresh(campaign)
    return campaign


@app.delete("/campaigns/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT,
            dependencies=[Depends(require_roles("owner", "supervisor")), Depends(require_csrf)])
def delete_campaign(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    if campaign.status == CampaignStatus.active:
        raise HTTPException(409, "Pause this campaign before deleting it")
    db.delete(campaign)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/campaigns/{campaign_id}/contacts", response_model=list[CallOut], status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(require_roles("owner", "supervisor")), Depends(require_csrf)])
def add_contacts(campaign_id: int, contacts: list[Contact], db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign: raise HTTPException(404, "Campaign not found")
    if not contacts: raise HTTPException(422, "Provide at least one contact")
    calls = [_queued_call(campaign, c.phone, c.name, c.details, db) for c in contacts]
    db.add_all(calls); db.commit()
    return calls


@app.post("/campaigns/{campaign_id}/contacts/csv", response_model=ContactUploadResult, status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(require_roles("owner", "supervisor")), Depends(require_csrf)])
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


@app.post("/campaigns/{campaign_id}/run-simulation", response_model=list[CallOut],
          dependencies=[Depends(require_roles("owner", "supervisor")), Depends(require_csrf)])
def run_simulation(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign: raise HTTPException(404, "Campaign not found")
    if campaign.status != CampaignStatus.active: raise HTTPException(409, "Launch the campaign before running calls")
    global_settings = _global_settings(db)
    limit = campaign.max_concurrent_calls_override or global_settings.max_concurrent_calls
    calls = db.scalars(
        select(Call).where(
            Call.campaign_id == campaign_id, Call.status == CallStatus.queued,
            (Call.scheduled_for.is_(None)) | (Call.scheduled_for <= datetime.now(timezone.utc)),
        )
        .order_by(Call.created_at, Call.id).limit(limit)
    ).all()
    for call in calls: simulate_call(call)
    db.commit(); return calls


@app.post("/campaigns/{campaign_id}/place-next-call", response_model=CallOut,
          dependencies=[Depends(require_roles("owner", "supervisor")), Depends(require_csrf)])
def place_next_live_call(campaign_id: int, db: Session = Depends(get_db)):
    """Place exactly one approved campaign call through the 3CX Route Point.

    This deliberately is not an automatic dialler: an operator must click the
    action, the VPS-only live-calling switch must be enabled, and a campaign
    cannot exceed one in-progress call while this first live workflow is being
    proven. Every result updates the already-queued call-log row.
    """
    try:
        return place_next_call(campaign_id, db)
    except DispatchError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/calls", response_model=list[CallOut], dependencies=[Depends(current_user)])
def list_calls(campaign_id: int | None = None, db: Session = Depends(get_db)):
    query = select(Call).options(selectinload(Call.metric), selectinload(Call._transcript), selectinload(Call.recording)).order_by(Call.created_at.desc())
    if campaign_id: query = query.where(Call.campaign_id == campaign_id)
    return db.scalars(query).all()


@app.post("/calls/{call_id}/outcome", response_model=CallOut, dependencies=[Depends(require_csrf)])
def label_outcome(call_id: int, payload: OutcomeUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    call = db.scalar(select(Call).options(selectinload(Call.metric)).where(Call.id == call_id))
    if not call: raise HTTPException(404, "Call not found")
    if call.status != CallStatus.completed: raise HTTPException(409, "Only completed calls can be labeled")
    call.outcome = payload.outcome
    call.outcome_labeled_by_id = user.id
    from datetime import datetime, timezone
    call.outcome_labeled_at = datetime.now(timezone.utc)
    if call.sentiment_source == "not_available": analyze_sentiment(call)
    if not call.metric: call.metric = score_call(call)
    db.commit(); db.refresh(call)
    return call


@app.post("/calls/{call_id}/sentiment", response_model=CallOut, dependencies=[Depends(current_user), Depends(require_csrf)])
def label_sentiment(call_id: int, payload: SentimentUpdate, db: Session = Depends(get_db)):
    """Let a reviewer correct the automated signal without changing outcome."""
    call = db.get(Call, call_id)
    if not call:
        raise HTTPException(404, "Call not found")
    if call.status != CallStatus.completed:
        raise HTTPException(409, "Only completed calls can be reviewed")
    call.sentiment = payload.sentiment
    call.sentiment_confidence = 100
    call.sentiment_source = "reviewer"
    db.commit(); db.refresh(call)
    return call


@app.get("/calls/{call_id}/recording", dependencies=[Depends(current_user)])
def stream_call_recording(call_id: int, db: Session = Depends(get_db)):
    """Stream a linked 3CX recording through Alfred without storing audio locally."""
    call = db.scalar(select(Call).options(selectinload(Call.recording)).where(Call.id == call_id))
    if not call or not call.recording or call.recording.deleted_at:
        raise HTTPException(404, "Recording not available for this call")
    recording_id = parse_threecx_recording_id(call.recording.storage_key)
    if recording_id is None:
        raise HTTPException(404, "Recording not available for this call")

    settings = get_settings()
    client = ThreeCXClient(settings)

    def iter_audio():
        try:
            with client.stream_recording(recording_id) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    yield chunk
        finally:
            client.close()

    return StreamingResponse(
        iter_audio(),
        media_type=call.recording.content_type or "audio/x-wav",
        headers={"Content-Disposition": f'inline; filename="call-{call_id}.wav"'},
    )


@app.get("/metrics/daily", dependencies=[Depends(current_user)])
def get_daily_metrics(db: Session = Depends(get_db)):
    return daily_metrics(db)


frontend = Path(__file__).parent / "web"
app.mount("/", StaticFiles(directory=frontend, html=True), name="web")
