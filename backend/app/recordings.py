"""Link 3CX call recordings to Alfred calls without copying audio to the VPS."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Call, CallStatus, GlobalSettings, Recording
from .threecx import ThreeCXClient, ThreeCXError

THREECX_STORAGE_PREFIX = "threecx:"
MATCH_WINDOW_SECONDS = 900
DIAGNOSTIC_PHONE = "diagnostic"


def is_diagnostic_call(call: Call) -> bool:
    return call.phone == DIAGNOSTIC_PHONE


def threecx_storage_key(recording_id: int | str) -> str:
    return f"{THREECX_STORAGE_PREFIX}{recording_id}"


def parse_threecx_recording_id(storage_key: str) -> int | None:
    if not storage_key.startswith(THREECX_STORAGE_PREFIX):
        return None
    suffix = storage_key[len(THREECX_STORAGE_PREFIX):]
    return int(suffix) if suffix.isdigit() else None


def phone_key(value: str | None) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if len(digits) >= 10 else digits


def parse_threecx_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def recording_enabled_for_call(call: Call) -> bool:
    playbook = (call.configuration_snapshot_json or {}).get("playbook") or {}
    return bool(playbook.get("recording_enabled", True))


def _call_anchor(call: Call) -> datetime | None:
    return call.completed_at or call.started_at or call.created_at


def best_matching_call(recording: dict[str, Any], candidates: Iterable[Call]) -> Call | None:
    """Return the closest unmatched Alfred call for one 3CX recording row."""
    caller = phone_key(str(recording.get("FromCallerNumber") or recording.get("FromDisplayName") or ""))
    start = parse_threecx_timestamp(recording.get("StartTime"))
    if start is None:
        return None

    best: Call | None = None
    best_delta: float | None = None
    for call in candidates:
        if not is_diagnostic_call(call):
            if not caller or phone_key(call.phone) != caller:
                continue
        anchor = _call_anchor(call)
        if anchor is None:
            continue
        delta = abs((start - anchor).total_seconds())
        if delta > MATCH_WINDOW_SECONDS:
            continue
        if best is None or best_delta is None or delta < best_delta:
            best = call
            best_delta = delta
    return best


def _retention_until(db: Session) -> datetime:
    settings = db.get(GlobalSettings, 1)
    days = settings.recording_retention_days if settings else 30
    return datetime.now(timezone.utc) + timedelta(days=days)


def sync_threecx_recordings(db: Session, client: ThreeCXClient) -> int:
    """Attach new 3CX recording metadata to completed Alfred calls."""
    rows = client.list_xapi_recordings()
    if not rows:
        return 0

    existing_keys = set(db.scalars(select(Recording.storage_key)).all())
    linked_call_ids = set(db.scalars(select(Recording.call_id)).all())
    query = select(Call).where(Call.status == CallStatus.completed)
    if linked_call_ids:
        query = query.where(Call.id.not_in(linked_call_ids))
    candidates = db.scalars(query.order_by(Call.created_at.desc())).all()
    candidates = [call for call in candidates if recording_enabled_for_call(call)]
    if not candidates:
        return 0

    retention_until = _retention_until(db)
    linked = 0
    used_call_ids: set[int] = set()

    for row in sorted(rows, key=lambda item: str(item.get("StartTime") or "")):
        recording_id = row.get("Id")
        if recording_id is None:
            continue
        storage_key = threecx_storage_key(recording_id)
        if storage_key in existing_keys:
            continue
        call = best_matching_call(row, [item for item in candidates if item.id not in used_call_ids])
        if call is None:
            continue
        db.add(Recording(
            call_id=call.id,
            storage_key=storage_key,
            content_type="audio/x-wav",
            retention_until=retention_until,
        ))
        used_call_ids.add(call.id)
        existing_keys.add(storage_key)
        linked += 1

    if linked:
        db.commit()
    return linked


def sync_threecx_recordings_safe(db: Session, settings) -> int:
    if settings.call_provider != "threecx":
        return 0
    client = ThreeCXClient(settings)
    try:
        return sync_threecx_recordings(db, client)
    except ThreeCXError:
        db.rollback()
        return 0
    finally:
        client.close()
