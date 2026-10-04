"""Lightweight live campaign status for the pinned dashboard bar."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .gateway_lines import busy_line_numbers, campaign_line_numbers
from .models import Call, CallStatus, Campaign, CampaignStatus, GatewayLine, GlobalSettings


def _start_of_local_day(tz_name: str) -> datetime:
    local_now = datetime.now(ZoneInfo(tz_name))
    return local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def live_campaign_status(db: Session) -> dict:
    settings = db.scalar(select(GlobalSettings).limit(1))
    max_global = settings.max_concurrent_calls if settings else 1
    default_tz = settings.default_timezone if settings else "Asia/Dubai"
    now = datetime.now(timezone.utc)

    active_campaigns = db.scalars(
        select(Campaign).where(Campaign.status == CampaignStatus.active).order_by(Campaign.created_at.desc())
    ).all()

    busy_lines = busy_line_numbers(db)
    enabled_lines = set(db.scalars(select(GatewayLine.number).where(GatewayLine.enabled.is_(True))).all())

    campaigns_out = []
    for campaign in active_campaigns:
        tz_name = campaign.timezone or default_tz
        start_of_day = _start_of_local_day(tz_name)
        line_limit = campaign.max_concurrent_calls_override or max_global
        gateway_lines = campaign_line_numbers(campaign)
        usable_lines = [number for number in gateway_lines if number in enabled_lines]
        if gateway_lines:
            line_limit = min(line_limit, len(usable_lines))

        queued = db.scalar(
            select(func.count(Call.id)).where(Call.campaign_id == campaign.id, Call.status == CallStatus.queued)
        ) or 0
        completed_today = db.scalar(
            select(func.count(Call.id)).where(
                Call.campaign_id == campaign.id,
                Call.status == CallStatus.completed,
                Call.completed_at.is_not(None),
                Call.completed_at >= start_of_day,
            )
        ) or 0
        failed_today = db.scalar(
            select(func.count(Call.id)).where(
                Call.campaign_id == campaign.id,
                Call.status == CallStatus.failed,
                Call.completed_at.is_not(None),
                Call.completed_at >= start_of_day,
            )
        ) or 0

        live_calls_raw = db.scalars(
            select(Call)
            .where(Call.campaign_id == campaign.id, Call.status == CallStatus.in_progress)
            .order_by(Call.started_at.desc(), Call.id.desc())
        ).all()

        live_calls = []
        for call in live_calls_raw:
            elapsed = 0
            if call.started_at:
                started = call.started_at
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                elapsed = max(0, int((now - started).total_seconds()))
            live_calls.append(
                {
                    "id": call.id,
                    "prospect_name": call.prospect_name,
                    "phone": call.phone,
                    "started_at": call.started_at,
                    "elapsed_seconds": elapsed,
                    "gateway_line": call.gateway_line,
                }
            )

        lines_in_use = len(live_calls)
        waiting_reason = None
        if gateway_lines and not usable_lines:
            waiting_reason = "None of this campaign's lines are switched on"
        elif gateway_lines and queued and all(number in busy_lines for number in usable_lines):
            waiting_reason = "Waiting for a free line"
        campaigns_out.append(
            {
                "id": campaign.id,
                "name": campaign.name,
                "lines_in_use": lines_in_use,
                "lines_available": line_limit,
                "queued": queued,
                "completed_today": completed_today,
                "failed_today": failed_today,
                "live_calls": live_calls,
                "gateway_lines": gateway_lines,
                "waiting_reason": waiting_reason,
            }
        )

    global_in_progress = db.scalar(select(func.count(Call.id)).where(Call.status == CallStatus.in_progress)) or 0
    return {
        "max_concurrent_calls": max_global,
        "lines_in_use": global_in_progress,
        "active_campaigns": campaigns_out,
    }
