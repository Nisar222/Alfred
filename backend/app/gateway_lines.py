"""Dinstar gateway lines: the single place that chooses a line and builds a dial string.

3CX sends ``<prefix><number>`` out on the matching gateway SIM channel (8801 is
channel 1 … 8832 is channel 32).  A campaign with no lines selected keeps the
standard 3CX route and dials the number unchanged.

Whether a line is busy is derived from ``Call.status`` in PostgreSQL, so a
restart or stale-call recovery frees lines without any in-memory state.

When gateway SIM checks are set up, only lines whose SIM the gateway reported
as registered in the latest check are dialled.  Checks run on demand (Settings,
campaign dialog, campaign start, "Check SIMs now"), never on a timer.  If the
latest check failed, or none has run yet, every gateway line is paused until a
check succeeds (fail closed).
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .dinstar import REGISTERED, DinstarClient, DinstarError, SimStatus
from .models import Call, CallStatus, Campaign, GatewayLine, GlobalSettings

LINE_COUNT = 32
ATTENTION_FAILURES = 3
GATEWAY_UNREACHABLE = "Can't reach the gateway to check SIMs"


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def sim_gate(db: Session, settings: Settings | None = None) -> str:
    """'off' when SIM checks are not set up, 'ok' after a successful check, 'error' otherwise."""
    settings = settings or get_settings()
    if not settings.dinstar_configured:
        return "off"
    row = db.get(GlobalSettings, 1)
    last_success = _aware(row.sim_check_last_success_at) if row else None
    if row is None or row.sim_check_error or last_success is None:
        return "error"
    return "ok"


def _dialable_lines_query(numbers: list[int], gate: str):
    query = select(GatewayLine).where(GatewayLine.number.in_(numbers), GatewayLine.enabled.is_(True))
    if gate == "ok":
        query = query.where(GatewayLine.sim_registration == REGISTERED)
    return query.order_by(GatewayLine.last_used_at.asc().nulls_first(), GatewayLine.number)


def global_cooloff_seconds(db: Session) -> int:
    row = db.get(GlobalSettings, 1)
    return max(0, int(row.line_cooloff_seconds or 0)) if row else 0


def effective_cooloff_seconds(db: Session, campaign: Campaign) -> int:
    """The campaign's own rest time if set, otherwise the Settings default."""
    override = campaign.line_cooloff_seconds_override
    return max(0, int(override)) if override is not None else global_cooloff_seconds(db)


def resting_line_numbers(db: Session, cooloff_seconds: int) -> dict[int, datetime]:
    """Lines whose last call (any outcome) ended less than ``cooloff_seconds`` ago → when they are free.

    Derived from ``calls.completed_at`` so a restart or stale-call recovery needs no extra state.
    """
    if cooloff_seconds <= 0:
        return {}
    now = datetime.now(timezone.utc)
    rest = timedelta(seconds=cooloff_seconds)
    resting: dict[int, datetime] = {}
    for number, ended in db.execute(
        select(Call.gateway_line, func.max(Call.completed_at))
        .where(Call.gateway_line.is_not(None), Call.completed_at.is_not(None))
        .group_by(Call.gateway_line)
    ).all():
        free_at = _aware(ended) + rest
        if free_at > now:
            resting[number] = free_at
    return resting


def default_prefix(number: int) -> str:
    return f"88{number:02d}"


def ensure_gateway_lines(db: Session) -> None:
    """Create any missing line rows (the migration seeds them; tests and new installs may not)."""
    existing = set(db.scalars(select(GatewayLine.number)).all())
    missing = [GatewayLine(number=n, prefix=default_prefix(n), enabled=True)
               for n in range(1, LINE_COUNT + 1) if n not in existing]
    if missing:
        db.add_all(missing)
        db.flush()


def normalize_line_numbers(values: list[int] | None) -> list[int]:
    numbers = sorted(set(int(value) for value in (values or [])))
    invalid = [n for n in numbers if not 1 <= n <= LINE_COUNT]
    if invalid:
        raise ValueError(f"Gateway lines must be between 1 and {LINE_COUNT}")
    return numbers


def campaign_line_numbers(campaign: Campaign) -> list[int]:
    """The campaign's selected lines; empty means the standard route (no prefix)."""
    try:
        return normalize_line_numbers(campaign.gateway_lines_json)
    except (TypeError, ValueError):
        return []


def dial_destination(phone: str, line: GatewayLine | None) -> str:
    """Build exactly what 3CX dials: the line prefix plus the number as uploaded."""
    return f"{line.prefix}{phone}" if line else phone


def busy_line_numbers(db: Session) -> set[int]:
    return set(db.scalars(select(Call.gateway_line).where(
        Call.status == CallStatus.in_progress, Call.gateway_line.is_not(None)
    )).all())


def free_lines(db: Session, campaign: Campaign, settings: Settings | None = None) -> list[GatewayLine]:
    """Lines this campaign could dial on right now, in rotation order."""
    numbers = campaign_line_numbers(campaign)
    gate = sim_gate(db, settings)
    if not numbers or gate == "error":
        return []
    unavailable = busy_line_numbers(db) | set(resting_line_numbers(db, effective_cooloff_seconds(db, campaign)))
    lines = db.scalars(_dialable_lines_query(numbers, gate)).all()
    return [line for line in lines if line.number not in unavailable]


def next_line_free_at(db: Session, campaign: Campaign, settings: Settings | None = None) -> datetime | None:
    """When the first of this campaign's otherwise-usable lines finishes resting (None if none is resting)."""
    numbers = campaign_line_numbers(campaign)
    gate = sim_gate(db, settings)
    if not numbers or gate == "error":
        return None
    resting = resting_line_numbers(db, effective_cooloff_seconds(db, campaign))
    busy = busy_line_numbers(db)
    usable = [line.number for line in db.scalars(_dialable_lines_query(numbers, gate)).all() if line.number not in busy]
    times = [resting[number] for number in usable if number in resting]
    return min(times) if times else None


def allocate_line(db: Session, campaign: Campaign, settings: Settings | None = None) -> GatewayLine | None:
    """Reserve the least recently used free line for this campaign.

    Least-recently-used order gives the 8801 → 8832 → 8801 rotation and skips
    busy, switched-off, resting (cool-off), and (when SIM checks are on)
    unregistered lines.
    PostgreSQL row locks stop two workers taking the same line; the caller
    must assign it to a call in the same commit.
    """
    numbers = campaign_line_numbers(campaign)
    gate = sim_gate(db, settings)
    if not numbers or gate == "error":
        return None
    busy = select(Call.gateway_line).where(
        Call.status == CallStatus.in_progress, Call.gateway_line.is_not(None)
    )
    query = _dialable_lines_query(numbers, gate).where(GatewayLine.number.not_in(busy))
    resting = resting_line_numbers(db, effective_cooloff_seconds(db, campaign))
    if resting:
        query = query.where(GatewayLine.number.not_in(list(resting)))
    # PostgreSQL locks the chosen line row; SQLite ignores the clause for local tests.
    line = db.scalar(query.limit(1).with_for_update(skip_locked=True))
    if line:
        line.last_used_at = datetime.now(timezone.utc)
    return line


def _start_of_local_day(tz_name: str) -> datetime:
    local_now = datetime.now(ZoneInfo(tz_name))
    return local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def _needs_attention(db: Session, number: int) -> bool:
    recent = db.scalars(select(Call).where(
        Call.gateway_line == number, Call.status.in_((CallStatus.completed, CallStatus.failed))
    ).order_by(Call.completed_at.desc(), Call.id.desc()).limit(ATTENTION_FAILURES)).all()
    return len(recent) == ATTENTION_FAILURES and all(
        call.status == CallStatus.failed and call.failure_category == "provider_failure" for call in recent
    )


def _sim_status(line: GatewayLine, gate: str) -> str:
    if gate == "off":
        return "off"
    if gate == "error" or line.sim_registration is None:
        return "unknown"
    return "ready" if line.sim_registration == REGISTERED else "not_ready"


def describe_lines(db: Session, tz_name: str = "Asia/Dubai", settings: Settings | None = None) -> list[dict]:
    """Every line with its live status and today's usage, for Settings and the campaign picker."""
    ensure_gateway_lines(db)
    gate = sim_gate(db, settings)
    resting = resting_line_numbers(db, global_cooloff_seconds(db))
    start = _start_of_local_day(tz_name)
    busy = busy_line_numbers(db)
    counts: dict[tuple[int, CallStatus], int] = {
        (number, status): total for number, status, total in db.execute(
            select(Call.gateway_line, Call.status, func.count(Call.id))
            .where(Call.gateway_line.is_not(None), Call.started_at >= start)
            .group_by(Call.gateway_line, Call.status)
        ).all()
    }
    described = []
    for line in db.scalars(select(GatewayLine).order_by(GatewayLine.number)).all():
        attention = line.enabled and _needs_attention(db, line.number)
        sim_status = _sim_status(line, gate)
        if not line.enabled:
            status = "off"
        elif line.number in busy:
            status = "calling"
        elif sim_status == "unknown":
            status = "sim_unknown"
        elif sim_status == "not_ready":
            status = "sim_not_ready"
        elif line.number in resting:
            status = "resting"
        elif attention:
            status = "attention"
        else:
            status = "ready"
        completed = counts.get((line.number, CallStatus.completed), 0)
        failed = counts.get((line.number, CallStatus.failed), 0)
        in_progress = counts.get((line.number, CallStatus.in_progress), 0)
        described.append({
            "number": line.number, "prefix": line.prefix, "label": line.label,
            "enabled": line.enabled, "status": status, "needs_attention": attention,
            "calls_today": completed + failed + in_progress,
            "answered_today": completed, "failed_today": failed,
            "sim_status": sim_status, "sim_registration": line.sim_registration,
            "sim_signal": line.sim_signal, "sim_checked_at": _aware(line.sim_checked_at),
            "resting_until": resting.get(line.number) if line.enabled else None,
        })
    return described


def sim_check_summary(db: Session, settings: Settings | None = None) -> dict:
    """Whether SIM checks are on, and the outcome of the latest one, for Settings and live status."""
    settings = settings or get_settings()
    row = db.get(GlobalSettings, 1)
    gate = sim_gate(db, settings)
    error = row.sim_check_error if row else None
    if gate == "error" and not error:
        error = "SIMs have not been checked yet"
    return {
        "enabled": settings.dinstar_configured, "state": gate, "error": error if gate == "error" else None,
        "last_attempt_at": _aware(row.sim_check_last_attempt_at) if row else None,
        "last_success_at": _aware(row.sim_check_last_success_at) if row else None,
        "ready_lines": db.scalar(select(func.count(GatewayLine.number)).where(
            GatewayLine.sim_registration == REGISTERED, GatewayLine.enabled.is_(True))) or 0
            if gate == "ok" else 0,
    }


def record_sim_check(db: Session, statuses: dict[int, SimStatus] | None, error: str | None) -> None:
    """Store one check's outcome. A failed check keeps the last-known SIM data for display."""
    ensure_gateway_lines(db)
    row = db.get(GlobalSettings, 1)
    if row is None:
        row = GlobalSettings(id=1)
        db.add(row)
    now = datetime.now(timezone.utc)
    row.sim_check_last_attempt_at = now
    if error is not None:
        row.sim_check_error = error
        return
    row.sim_check_error = None
    row.sim_check_last_success_at = now
    for line in db.scalars(select(GatewayLine)).all():
        status = (statuses or {}).get(line.number)
        # A port the gateway did not report is treated as not ready, never as registered.
        line.sim_registration = status.registration if status else "NOT_REPORTED"
        line.sim_signal = status.signal if status else None
        line.sim_checked_at = now


def check_sims(db: Session, settings: Settings | None = None, transport=None) -> dict:
    """Ask the gateway for every SIM's status now, store it, and return the summary."""
    settings = settings or get_settings()
    if not settings.dinstar_configured:
        return sim_check_summary(db, settings)
    try:
        with DinstarClient(settings, transport=transport) as client:
            record_sim_check(db, client.sim_statuses(), None)
    except DinstarError as exc:
        record_sim_check(db, None, str(exc))
    db.commit()
    return sim_check_summary(db, settings)
