from datetime import datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .models import Call, CallMetric, CallStatus, Outcome


def score_call(call: Call) -> CallMetric:
    """Deterministic v1 evaluator; replace with the isolated QA LLM adapter in Phase 2."""
    transcript = (call.transcript or "").lower()
    close = 10 if any(x in transcript for x in ("schedule", "appointment", "next step")) else 0
    engagement = 8 if "?" in transcript else 5
    objection = 8 if any(x in transcript for x in ("understand", "concern", "budget")) else 5
    outcome_bonus = 1 if call.outcome in (Outcome.sale, Outcome.lead) else 0
    return CallMetric(
        tone=7 + outcome_bonus, clarity=7 + outcome_bonus, engagement=engagement,
        objection=objection, close=close,
        strength="Conversation was completed and captured for review.",
        weakness="Automated v1 evaluation is a baseline; confirm scores during review.",
        suggestion="Use the next batch to test one specific prompt improvement.",
    )


def simulate_call(call: Call) -> None:
    call.status = CallStatus.completed
    call.duration_seconds = 42
    call.completed_at = datetime.now(timezone.utc)
    name = call.prospect_name or "there"
    call.transcript = (
        f"Agent: Hi {name}, do you have 60 seconds for a quick question?\n"
        "Prospect: What is this about?\n"
        "Agent: We help businesses reduce time spent on follow-up. Can I schedule a 15-minute appointment?"
    )


def daily_metrics(db: Session) -> dict:
    fields = [CallMetric.tone, CallMetric.clarity, CallMetric.engagement, CallMetric.objection, CallMetric.close]
    averages = db.execute(select(*[func.coalesce(func.avg(x), 0) for x in fields])).one()
    total = db.scalar(select(func.count(Call.id))) or 0
    outcome_counts = {o.value: db.scalar(select(func.count(Call.id)).where(Call.outcome == o)) or 0 for o in Outcome}
    return {
        "calls": total, "scores": dict(zip(["tone", "clarity", "engagement", "objection", "close"], [round(float(v), 1) for v in averages])),
        "outcomes": outcome_counts,
        "conversion_rate": round((outcome_counts["sale"] / total * 100), 1) if total else 0,
    }
