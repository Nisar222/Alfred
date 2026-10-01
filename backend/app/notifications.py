"""Durable, idempotent popup notifications for routed campaign calls."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AgentNotification, Call, CallStatus, Campaign, CampaignStatus, User

DIAGNOSTIC_CAMPAIGN_NAME = "Alfred diagnostic"


def recipient_user_id_for_extension(db: Session, extension: str | None) -> int | None:
    """Resolve the active Alfred user linked to a 3CX extension."""
    if not extension:
        return None
    return db.scalar(
        select(User.id).where(User.threecx_extension == extension, User.is_active.is_(True))
    )


def ensure_routing_notification(
    call: Call,
    db: Session,
    *,
    recipient_user_id: int | None = None,
    recipient_extension: str | None = None,
) -> AgentNotification:
    """Create at most one immutable popup snapshot for a routed call."""
    existing = db.scalar(select(AgentNotification).where(AgentNotification.call_id == call.id))
    if existing:
        return existing
    destination = str(call.routed_destination or recipient_extension or "").strip()
    if not destination:
        raise ValueError("A routed destination is required for an agent notification.")
    resolved_extension = recipient_extension or destination
    if recipient_user_id is None:
        recipient_user_id = recipient_user_id_for_extension(db, resolved_extension)
    notification = AgentNotification(
        call_id=call.id,
        recipient_user_id=recipient_user_id,
        recipient_extension=resolved_extension,
        customer_name=call.prospect_name,
        campaign_name=call.campaign.name,
        menu_option=call.dtmf_digit,
        routed_destination=destination,
    )
    db.add(notification)
    db.flush()
    return notification


def _diagnostic_campaign(db: Session) -> Campaign:
    campaign = db.scalar(select(Campaign).where(Campaign.name == DIAGNOSTIC_CAMPAIGN_NAME))
    if campaign:
        return campaign
    campaign = Campaign(
        name=DIAGNOSTIC_CAMPAIGN_NAME,
        script="Owner-triggered diagnostic calls from Alfred Settings.",
        status=CampaignStatus.paused,
        timezone="Asia/Dubai",
        calling_window_json={"start": "00:00", "end": "23:59"},
    )
    db.add(campaign)
    db.flush()
    return campaign


def record_diagnostic_keypresses(
    db: Session,
    *,
    digit: str | None,
    events: list[dict],
    routing_status: str,
) -> Call:
    """Store keys pressed on a test call that was not transferred to an agent."""
    call = Call(
        campaign_id=_diagnostic_campaign(db).id,
        phone="diagnostic",
        prospect_name="Test caller",
        status=CallStatus.completed,
        dtmf_digit=digit,
        dtmf_events_json=list(events),
        routing_status=routing_status,
        completed_at=datetime.now(timezone.utc),
        configuration_snapshot_json={"source": "test-dtmf"},
    )
    db.add(call)
    db.flush()
    return call


def ensure_diagnostic_routing_notification(
    db: Session,
    *,
    destination: str,
    digit: str,
    recipient_extension: str | None,
) -> AgentNotification:
    """Record a popup for a successful owner-triggered test-dtmf transfer."""
    campaign = _diagnostic_campaign(db)

    resolved_extension = recipient_extension or destination
    call = Call(
        campaign_id=campaign.id,
        phone="diagnostic",
        prospect_name="Test caller",
        status=CallStatus.completed,
        dtmf_digit=digit,
        routed_destination=destination,
        routing_status="routed",
        completed_at=datetime.now(timezone.utc),
        configuration_snapshot_json={"source": "test-dtmf"},
    )
    db.add(call)
    db.flush()
    return ensure_routing_notification(
        call,
        db,
        recipient_extension=resolved_extension,
    )
