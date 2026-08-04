from datetime import datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .call_analysis import analyze_sentiment, build_call_metric, generate_call_summary
from .models import Call, CallMetric, CallStatus, Outcome, Transcript, TranscriptSource


def score_call(call: Call) -> CallMetric:
    """Deterministic v1 evaluator; replace with the isolated QA LLM adapter in Phase 2."""
    return build_call_metric(call, call._transcript)


def simulate_call(call: Call) -> None:
    call.status = CallStatus.completed
    call.duration_seconds = 42
    call.completed_at = datetime.now(timezone.utc)
    name = call.prospect_name or "there"
    content = (
        f"Agent: Hi {name}, do you have 60 seconds for a quick question?\n"
        "Customer: What is this about?\n"
        "Agent: We help businesses reduce time spent on follow-up. Can I schedule a 15-minute appointment?"
    )
    segments = [
        {"speaker": "agent", "text": f"Hi {name}, do you have 60 seconds for a quick question?", "start": 0.0, "end": 4.0},
        {"speaker": "customer", "text": "What is this about?", "start": 4.5, "end": 6.0},
        {"speaker": "agent", "text": "We help businesses reduce time spent on follow-up. Can I schedule a 15-minute appointment?", "start": 6.5, "end": 12.0},
    ]
    call._transcript = Transcript(
        content=content,
        summary=generate_call_summary(content, prospect_name=call.prospect_name),
        source=TranscriptSource.whisper,
        segments_json=segments,
        confidence=88,
    )
    analyze_sentiment(call)
    call.metric = score_call(call)


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
