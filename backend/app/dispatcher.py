"""Persistent, deliberately conservative 3CX campaign dispatcher.

The database is the queue authority.  The process only claims queued rows;
therefore a restart cannot create an invisible in-memory campaign queue.
"""
from datetime import datetime, time as datetime_time, timedelta, timezone
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
from .notifications import ensure_routing_notification


class DispatchError(RuntimeError):
    pass


def _within_calling_window(campaign: Campaign, now: datetime | None = None) -> bool:
    window = campaign.calling_window_json or {}
    start, end = window.get("start"), window.get("end")
    if not start or not end:
        return True
    try:
        local_now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(campaign.timezone)).strftime("%H:%M")
    except Exception:
        return True
    return start <= local_now < end if start <= end else local_now >= start or local_now < end


def _next_allowed_time(campaign: Campaign, requested: datetime) -> datetime:
    """Move a retry forward to the next opening of its frozen campaign hours."""
    window = campaign.calling_window_json or {}
    start, end = window.get("start"), window.get("end")
    if not start or not end:
        return requested
    try:
        zone = ZoneInfo(campaign.timezone)
        local = requested.astimezone(zone)
        if _within_calling_window(campaign, requested):
            return requested
        hour, minute = (int(value) for value in start.split(":"))
        opening = datetime.combine(local.date(), datetime_time(hour, minute), zone)
        if local >= opening and start <= end:
            opening += timedelta(days=1)
        elif start > end and local.strftime("%H:%M") < end:
            return requested
        return opening.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return requested


def _eligible_queue_clause(now: datetime | None = None):
    now = now or datetime.now(timezone.utc)
    return (Call.scheduled_for.is_(None)) | (Call.scheduled_for <= now)


def _settings(db: Session) -> GlobalSettings:
    value = db.get(GlobalSettings, 1)
    if value is None:
        value = GlobalSettings(id=1)
        db.add(value); db.flush()
    return value


def _is_dispatchable(campaign: Campaign, db: Session, settings: Settings) -> bool:
    """Skip campaigns that cannot use a call line right now.

    A paused, empty, out-of-hours, or misconfigured campaign must never stop
    a later valid campaign from using the available capacity.
    """
    if campaign.status != CampaignStatus.active or not _within_calling_window(campaign):
        return False
    version = campaign.playbook_version
    if not version or version.status != PlaybookStatus.approved:
        return False
    audio = version.opening_audio
    if not audio or audio.status != AudioAssetStatus.ready:
        return False
    if not (Path(settings.audio_storage_dir) / audio.storage_key).is_file():
        return False
    return bool(db.scalar(select(Call.id).where(
        Call.campaign_id == campaign.id, Call.status == CallStatus.queued, _eligible_queue_clause()
    ).limit(1)))


def _failure_category(exc: ThreeCXError) -> str:
    message = str(exc).lower()
    if "busy" in message:
        return "busy"
    if "not answered" in message or "no answer" in message:
        return "no_answer"
    return "provider_failure"


def _schedule_retry(call: Call, campaign: Campaign, db: Session) -> Call | None:
    policy = (call.configuration_snapshot_json or {}).get("global", {})
    category_enabled = {
        "no_answer": policy.get("retry_no_answer", False),
        "busy": policy.get("retry_busy", False),
        "provider_failure": policy.get("retry_provider_failure", False),
    }
    max_attempts = int(policy.get("retry_max_attempts", 1))
    if call.attempt_number >= max_attempts or not category_enabled.get(call.failure_category or "", False):
        return None
    requested = (call.completed_at or datetime.now(timezone.utc)) + timedelta(minutes=int(policy.get("retry_delay_minutes", 60)))
    retry = Call(
        campaign_id=call.campaign_id, prospect_id=call.prospect_id, phone=call.phone,
        prospect_name=call.prospect_name, details=call.details, previous_attempt_id=call.id,
        attempt_number=call.attempt_number + 1, scheduled_for=_next_allowed_time(campaign, requested),
        configuration_snapshot_json=call.configuration_snapshot_json,
    )
    db.add(retry)
    return retry


def reconcile_campaign_completion(db: Session) -> None:
    """Close active campaigns only after at least one call reaches a final state."""
    active_campaigns = db.scalars(select(Campaign).where(Campaign.status == CampaignStatus.active)).all()
    terminal = (CallStatus.completed, CallStatus.failed, CallStatus.cancelled)
    changed = False
    for campaign in active_campaigns:
        total = db.scalar(select(func.count(Call.id)).where(Call.campaign_id == campaign.id)) or 0
        unfinished = db.scalar(select(func.count(Call.id)).where(
            Call.campaign_id == campaign.id, Call.status.not_in(terminal)
        )) or 0
        if total and unfinished == 0:
            campaign.status = CampaignStatus.completed
            changed = True
    if changed:
        db.commit()


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
    call = db.scalar(select(Call).where(
        Call.campaign_id == campaign_id, Call.status == CallStatus.queued, _eligible_queue_clause()
    ).order_by(Call.scheduled_for, Call.created_at, Call.id).limit(1).with_for_update(skip_locked=True))
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
        snapshot = call.configuration_snapshot_json or {}
        global_policy = snapshot.get("global", {})
        campaign_policy = snapshot.get("campaign", {})
        routing_enabled = bool(global_policy.get("dtmf_routing_enabled"))
        destination = campaign_policy.get("dtmf_queue_extension")
        if routing_enabled and destination:
            with client.monitor_dtmf(provider_call) as monitor:
                client.play_prerecorded_message(provider_call, audio_path)
                call.dtmf_digit = monitor.wait(timeout_seconds=15)
            if call.dtmf_digit == global_policy.get("dtmf_menu_digit", "1"):
                call.routed_destination = str(destination)
                try:
                    client.route_to(provider_call, str(destination), call.id)
                    call.routing_status = "routed"
                    # 803 currently has one member, so its popup can be safely
                    # assigned to that extension. Never broadcast customer
                    # context when a destination has multiple possible agents.
                    try:
                        recipient_extension = client.single_member_extension(str(destination))
                    except ThreeCXError:
                        recipient_extension = None
                    ensure_routing_notification(
                        call, db, recipient_extension=recipient_extension or str(destination)
                    )
                except ThreeCXError as exc:
                    call.routing_status = "route_failed"
                    call.failure_reason = str(exc)
                    try:
                        client.drop_call(provider_call)
                    except ThreeCXError:
                        pass
            else:
                call.routing_status = "no_input" if call.dtmf_digit is None else "invalid_input"
                client.drop_call(provider_call)
        else:
            client.play_prerecorded_message(provider_call, audio_path)
            client.drop_call(provider_call)
        call.status = CallStatus.completed
        call.duration_seconds = max(0, round(time.monotonic() - started))
        call.completed_at = datetime.now(timezone.utc)
    except ThreeCXError as exc:
        call.status = CallStatus.failed
        call.failure_reason = str(exc)
        call.failure_category = _failure_category(exc)
        call.completed_at = datetime.now(timezone.utc)
        _schedule_retry(call, campaign, db)
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
                call.failure_category = "provider_failure"
                call.completed_at = datetime.now(timezone.utc)
                _schedule_retry(call, call.campaign, db)
            db.commit()

    def _run(self) -> None:
        while not self.stop_event.wait(self.poll_seconds):
            settings = get_settings()
            if settings.call_provider != "threecx":
                continue
            with SessionLocal() as db:
                global_settings = _settings(db)
                reconcile_campaign_completion(db)
                if not global_settings.live_campaign_calling_enabled:
                    continue
                active = db.scalar(select(func.count(Call.id)).where(Call.status == CallStatus.in_progress)) or 0
                slots = max(0, global_settings.max_concurrent_calls - active)
                campaigns = db.scalars(select(Campaign).where(Campaign.status == CampaignStatus.active).order_by(Campaign.id)).all()
                for campaign in campaigns:
                    if not _is_dispatchable(campaign, db, settings):
                        continue
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
