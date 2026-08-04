"""Deterministic call review signals derived from transcript text."""
from __future__ import annotations

import re

from .models import Call, CallMetric, Outcome, Sentiment, Transcript


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def generate_call_summary(transcript: str, *, prospect_name: str | None = None) -> str:
    """Build a short plain-language summary without an external LLM."""
    text = (transcript or "").strip()
    if not text:
        return "The call was recorded but no transcript text was captured."

    normalized = _normalized(text)
    name = prospect_name or "the contact"
    highlights: list[str] = []

    if any(word in normalized for word in ("appointment", "schedule", "book", "next step", "call back", "callback")):
        highlights.append(f"{name} discussed a follow-up or next step.")
    if any(word in normalized for word in ("interested", "sounds good", "tell me more", "yes please")):
        highlights.append("The prospect showed interest during the conversation.")
    if any(word in normalized for word in ("not interested", "no thanks", "stop calling", "don't call", "do not call")):
        highlights.append("The prospect pushed back or declined.")
    if any(word in normalized for word in ("price", "budget", "cost", "expensive")):
        highlights.append("Pricing or budget came up.")
    if any(word in normalized for word in ("who is this", "what is this", "why are you calling")):
        highlights.append("The prospect asked for context about the call.")

    if highlights:
        return " ".join(highlights[:3])

    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    if sentences:
        lead = sentences[0]
        if len(lead) > 220:
            lead = lead[:217].rstrip() + "..."
        return f"Conversation with {name}. {lead}"
    return f"Conversation with {name} was captured for review."


def analyze_sentiment(call: Call) -> None:
    """Update the call row with a transparent transcript-based sentiment signal."""
    transcript = _normalized(call.transcript or "")
    if not transcript:
        call.sentiment = Sentiment.unknown
        call.sentiment_confidence = None
        call.sentiment_source = "not_available"
        return

    positive_words = (
        "thank", "great", "interested", "helpful", "yes", "appointment", "schedule",
        "sounds good", "perfect", "sure", "absolutely",
    )
    negative_words = (
        "not interested", "stop calling", "don't call", "do not call", "annoying",
        "bad", "no thanks", "leave me alone", "never call",
    )
    positive_hits = sum(word in transcript for word in positive_words)
    negative_hits = sum(word in transcript for word in negative_words)
    if positive_hits > negative_hits:
        call.sentiment = Sentiment.positive
        call.sentiment_confidence = min(92, 58 + positive_hits * 8)
    elif negative_hits > positive_hits:
        call.sentiment = Sentiment.negative
        call.sentiment_confidence = min(92, 58 + negative_hits * 8)
    else:
        call.sentiment = Sentiment.neutral
        call.sentiment_confidence = 56
    call.sentiment_source = "transcript-v1"


def build_call_metric(call: Call, transcript_row: Transcript | None = None) -> CallMetric:
    """Create review scores and coaching notes from transcript content."""
    transcript = _normalized(call.transcript or "")
    summary = (transcript_row.summary if transcript_row else None) or generate_call_summary(
        call.transcript or "", prospect_name=call.prospect_name
    )
    questions = (call.transcript or "").count("?")
    close = 9 if any(x in transcript for x in ("schedule", "appointment", "next step", "call back", "callback")) else 4
    engagement = min(10, 5 + questions)
    objection = 8 if any(x in transcript for x in ("understand", "concern", "budget", "price", "not interested")) else 5
    clarity = 8 if len(transcript) > 120 else 6
    tone = 8 if call.sentiment == Sentiment.positive else 6 if call.sentiment == Sentiment.neutral else 4
    if call.outcome in (Outcome.sale, Outcome.lead):
        close = max(close, 8)
        tone = max(tone, 7)

    weakness = "No strong objection handling was detected." if objection < 7 else "Objections came up and need a clearer response."
    if call.sentiment == Sentiment.negative:
        weakness = "The prospect tone was negative; review the opening and qualification."
    suggestion = "Confirm one concrete next step earlier in the call."
    if close >= 8:
        suggestion = "Keep this close pattern and reuse it in the next playbook version."
    elif "not interested" in transcript:
        suggestion = "Try a shorter opening and ask permission before pitching."

    return CallMetric(
        tone=tone,
        clarity=clarity,
        engagement=engagement,
        objection=objection,
        close=close,
        strength=summary,
        weakness=weakness,
        suggestion=suggestion,
        evaluator_version="transcript-v1",
    )
