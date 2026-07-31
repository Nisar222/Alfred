from datetime import datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .models import Call, CallMetric, CallStatus, Outcome, Sentiment


def analyze_sentiment(call: Call) -> None:
    """Small transparent MVP signal; replace with the local QA model later.

    It intentionally returns ``unknown`` when there is no transcript rather
    than inventing a judgement from call duration or sales outcome.
    """
    transcript = (call.transcript or "").lower()
    if not transcript.strip():
        call.sentiment = Sentiment.unknown
        call.sentiment_confidence = None
        call.sentiment_source = "not_available"
        return

    positive_words = ("thank", "great", "interested", "helpful", "yes", "appointment", "schedule", "sounds good")
    negative_words = ("not interested", "stop calling", "don't call", "do not call", "annoying", "bad", "no thanks")
    positive_hits = sum(word in transcript for word in positive_words)
    negative_hits = sum(word in transcript for word in negative_words)
    if positive_hits > negative_hits:
        call.sentiment = Sentiment.positive
        call.sentiment_confidence = min(90, 55 + positive_hits * 10)
    elif negative_hits > positive_hits:
        call.sentiment = Sentiment.negative
        call.sentiment_confidence = min(90, 55 + negative_hits * 10)
    else:
        call.sentiment = Sentiment.neutral
        call.sentiment_confidence = 55
    call.sentiment_source = "deterministic-v1"


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
    analyze_sentiment(call)


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
