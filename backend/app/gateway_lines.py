"""Dinstar gateway lines: the single place that chooses a line and builds a dial string.

3CX sends ``<prefix><number>`` out on the matching gateway SIM channel (8801 is
channel 1 … 8832 is channel 32).  A campaign with no lines selected keeps the
standard 3CX route and dials the number unchanged.

Whether a line is busy is derived from ``Call.status`` in PostgreSQL, so a
restart or stale-call recovery frees lines without any in-memory state.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Call, CallStatus, Campaign, GatewayLine

LINE_COUNT = 32
ATTENTION_FAILURES = 3


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


def free_lines(db: Session, campaign: Campaign) -> list[GatewayLine]:
    """Lines this campaign could dial on right now, in rotation order."""
    numbers = campaign_line_numbers(campaign)
    if not numbers:
        return []
    busy = busy_line_numbers(db)
    lines = db.scalars(select(GatewayLine).where(
        GatewayLine.number.in_(numbers), GatewayLine.enabled.is_(True)
    ).order_by(GatewayLine.last_used_at.asc().nulls_first(), GatewayLine.number)).all()
    return [line for line in lines if line.number not in busy]


def allocate_line(db: Session, campaign: Campaign) -> GatewayLine | None:
    """Reserve the least recently used free line for this campaign.

    Least-recently-used order gives the 8801 → 8832 → 8801 rotation and skips
    busy or switched-off lines.  PostgreSQL row locks stop two workers taking
    the same line; the caller must assign it to a call in the same commit.
    """
    numbers = campaign_line_numbers(campaign)
    if not numbers:
        return None
    busy = select(Call.gateway_line).where(
        Call.status == CallStatus.in_progress, Call.gateway_line.is_not(None)
    )
    # PostgreSQL locks the chosen line row; SQLite ignores the clause for local tests.
    line = db.scalar(select(GatewayLine).where(
        GatewayLine.number.in_(numbers), GatewayLine.enabled.is_(True),
        GatewayLine.number.not_in(busy),
    ).order_by(GatewayLine.last_used_at.asc().nulls_first(), GatewayLine.number)
     .limit(1).with_for_update(skip_locked=True))
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


def describe_lines(db: Session, tz_name: str = "Asia/Dubai") -> list[dict]:
    """Every line with its live status and today's usage, for Settings and the campaign picker."""
    ensure_gateway_lines(db)
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
        if not line.enabled:
            status = "off"
        elif line.number in busy:
            status = "calling"
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
        })
    return described
