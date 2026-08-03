"""Durable, idempotent popup notifications for routed campaign calls."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AgentNotification, Call


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
    notification = AgentNotification(
        call_id=call.id,
        recipient_user_id=recipient_user_id,
        recipient_extension=(recipient_extension or destination),
        customer_name=call.prospect_name,
        campaign_name=call.campaign.name,
        menu_option=call.dtmf_digit,
        routed_destination=destination,
    )
    db.add(notification)
    db.flush()
    return notification
