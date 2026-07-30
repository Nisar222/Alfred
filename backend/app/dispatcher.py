"""Persistent, deliberately conservative 3CX campaign dispatcher.

The database is the queue authority.  The process only claims queued rows;
therefore a restart cannot create an invisible in-memory campaign queue.
"""
from datetime import datetime, timezone
from pathlib import Path
import threading
import time
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import SessionLocal
from .models import AudioAssetStatus, Call, CallStatus, Campaign, CampaignStatus, GlobalSettings, PlaybookStatus
from .threecx import ThreeCXClient, ThreeCXError


class DispatchError(RuntimeError):
    pass


def _within_calling_window(campaign: Campaign) -> bool:
    window = campaign.calling_window_json or {}
    start, end = window.get("start"), window.get("end")
    if not start or not end:
        return True
    try:
        now = datetime.now(ZoneInfo(campaign.timezone)).strftime("%H:%M")
    except Exception:
        return True
    return start <= now < end if start <= end else now >= start or now < end


def _settings(db: Session) -> GlobalSettings:
    value = db.get(GlobalSettings, 1)
    if value is None:
        value = GlobalSettings(id=1)
        db.add(value); db.flush()
    return value


def place_next_call(campaign_id: int, db: Session, settings: Settings | None = None) -> Call:
    """Claim and run one queued call.  Safe to call from HTTP or the worker."""
    settings = settings or get_settings()
    if settings.call_provider != "threecx":
        raise DispatchError("3CX calling is not configured on this VPS")
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise DispatchError("Campaign not found")
    if campaign.status != CampaignStatus.active:
        raise DispatchError("Start the campaign before placing its next call")
    if not _within_calling_window(campaign):
        raise DispatchError("This campaign is outside its calling hours")
    version = campaign.playbook_version
    if not version or version.status != PlaybookStatus.approved:
        raise DispatchError("Choose an approved call playbook before placing a live call")
    audio = version.opening_audio
    if not audio or audio.status != AudioAssetStatus.ready:
        raise DispatchError("The selected playbook needs an available opening audio file")
    audio_path = Path(settings.audio_storage_dir) / audio.storage_key
    if not audio_path.is_file():
        raise DispatchError("The selected opening audio is missing from local VPS storage")

    limits = _settings(db)
    if not limits.live_campaign_calling_enabled:
        raise DispatchError("Live campaign calling is turned off in Alfred Settings")
    limit = campaign.max_concurrent_calls_override or limits.max_concurrent_calls
    active_for_campaign = db.scalar(select(func.count(Call.id)).where(Call.campaign_id == campaign_id, Call.status == CallStatus.in_progress)) or 0
    active_global = db.scalar(select(func.count(Call.id)).where(Call.status == CallStatus.in_progress)) or 0
    if active_for_campaign >= limit or active_global >= limits.max_concurrent_calls:
        raise DispatchError("All configured call lines are currently in use")

    # PostgreSQL locks this queue row; SQLite ignores the clause for local tests.
    call = db.scalar(select(Call).where(Call.campaign_id == campaign_id, Call.status == CallStatus.queued).order_by(Call.created_at, Call.id).limit(1).with_for_update(skip_locked=True))
    if not call:
        raise DispatchError("There are no queued contacts left in this campaign")
    call.status = CallStatus.in_progress
    call.started_at = datetime.now(timezone.utc)
    db.commit()

    started = time.monotonic(); client = None; provider_call = None
    try:
        client = ThreeCXClient(settings)
        provider_call = client.start_test_call(call.phone)
        call.provider_call_id = str(provider_call.participant_id)
        db.commit()
        client.wait_until_connected(provider_call)
        client.play_prerecorded_message(provider_call, audio_path)
        client.drop_call(provider_call)
        call.status = CallStatus.completed
        call.duration_seconds = max(0, round(time.monotonic() - started))
        call.completed_at = datetime.now(timezone.utc)
    except ThreeCXError as exc:
        call.status = CallStatus.failed
        call.failure_reason = str(exc)
        call.completed_at = datetime.now(timezone.utc)
    finally:
        if client:
            client.close()
    db.commit(); db.refresh(call)
    return call


class CampaignDispatcher:
    """Fills available lines for active campaigns, never exceeding DB limits."""
    def __init__(self, poll_seconds: int = 3):
        self.poll_seconds = poll_seconds
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.workers: set[threading.Thread] = set()
        self.lock = threading.Lock()

    def start(self) -> None:
        self._recover_stale_calls()
        self.thread = threading.Thread(target=self._run, name="campaign-dispatcher", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def _recover_stale_calls(self) -> None:
        with SessionLocal() as db:
            stuck = db.scalars(select(Call).where(Call.status == CallStatus.in_progress)).all()
            for call in stuck:
                call.status = CallStatus.failed
                call.failure_reason = "Alfred restarted before the call status was confirmed"
                call.completed_at = datetime.now(timezone.utc)
            db.commit()

    def _run(self) -> None:
        while not self.stop_event.wait(self.poll_seconds):
            settings = get_settings()
            if settings.call_provider != "threecx":
                continue
            with SessionLocal() as db:
                global_settings = _settings(db)
                if not global_settings.live_campaign_calling_enabled:
                    continue
                active = db.scalar(select(func.count(Call.id)).where(Call.status == CallStatus.in_progress)) or 0
                slots = max(0, global_settings.max_concurrent_calls - active)
                campaigns = db.scalars(select(Campaign).where(Campaign.status == CampaignStatus.active).order_by(Campaign.id)).all()
                for campaign in campaigns:
                    campaign_active = db.scalar(select(func.count(Call.id)).where(
                        Call.campaign_id == campaign.id, Call.status == CallStatus.in_progress
                    )) or 0
                    campaign_limit = campaign.max_concurrent_calls_override or global_settings.max_concurrent_calls
                    for _ in range(min(slots, max(0, campaign_limit - campaign_active))):
                        worker = threading.Thread(target=self._execute, args=(campaign.id,), daemon=True)
                        with self.lock:
                            self.workers = {item for item in self.workers if item.is_alive()}
                            self.workers.add(worker)
                        worker.start()
                        slots -= 1
                    if slots == 0:
                        break

    def _execute(self, campaign_id: int) -> None:
        with SessionLocal() as db:
            try:
                place_next_call(campaign_id, db)
            except DispatchError:
                return
