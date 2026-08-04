"""Link Whisper transcripts to calls that already have 3CX recordings."""
from __future__ import annotations

import threading

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .call_analysis import analyze_sentiment, build_call_metric
from .config import get_settings
from .database import SessionLocal
from .models import Call, CallStatus, Transcript, TranscriptSource
from .recordings import parse_threecx_recording_id, recording_enabled_for_call
from .threecx import ThreeCXClient, ThreeCXError
from .whisper_transcriber import transcribe_recording_bytes


def _download_recording(client: ThreeCXClient, recording_id: int) -> bytes:
    with client.stream_recording(recording_id) as response:
        response.raise_for_status()
        return b"".join(response.iter_bytes())


def sync_next_transcript(db, settings) -> bool:
    """Transcribe the newest completed call that has a recording but no transcript."""
    if settings.call_provider != "threecx" or not settings.transcript_sync_enabled:
        return False

    candidates = db.scalars(
        select(Call)
        .options(selectinload(Call.recording), selectinload(Call._transcript), selectinload(Call.metric))
        .where(Call.status == CallStatus.completed)
        .order_by(Call.completed_at.desc(), Call.id.desc())
        .limit(50)
    ).all()

    call = next(
        (
            item for item in candidates
            if item.recording
            and item.recording.deleted_at is None
            and item._transcript is None
            and recording_enabled_for_call(item)
        ),
        None,
    )
    if call is None:
        return False

    recording_id = parse_threecx_recording_id(call.recording.storage_key)
    if recording_id is None:
        return False

    client = ThreeCXClient(settings)
    try:
        audio_bytes = _download_recording(client, recording_id)
        result = transcribe_recording_bytes(audio_bytes, settings)
    except (ThreeCXError, OSError, RuntimeError, ValueError):
        db.rollback()
        return False
    finally:
        client.close()

    if not result.content.strip():
        return False

    transcript = Transcript(
        call_id=call.id,
        content=result.content,
        summary=result.summary,
        language=result.language,
        confidence=result.confidence,
        source=TranscriptSource.whisper,
        segments_json=result.segments,
    )
    db.add(transcript)
    call._transcript = transcript
    db.flush()

    analyze_sentiment(call)
    metric = build_call_metric(call, transcript)
    if call.metric:
        for field in ("tone", "clarity", "engagement", "objection", "close", "strength", "weakness", "suggestion", "evaluator_version"):
            setattr(call.metric, field, getattr(metric, field))
    else:
        call.metric = metric

    db.commit()
    return True


def sync_next_transcript_safe(db, settings) -> bool:
    try:
        return sync_next_transcript(db, settings)
    except Exception:
        db.rollback()
        return False


class TranscriptSync:
    def __init__(self, poll_seconds: int = 45):
        self.poll_seconds = poll_seconds
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.workers: set[threading.Thread] = set()
        self.lock = threading.Lock()

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="transcript-sync", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def _run(self) -> None:
        while not self.stop_event.wait(self.poll_seconds):
            settings = get_settings()
            if settings.call_provider != "threecx" or not settings.transcript_sync_enabled:
                continue
            worker = threading.Thread(target=self._execute, daemon=True)
            with self.lock:
                self.workers = {item for item in self.workers if item.is_alive()}
                if len(self.workers) >= 1:
                    continue
                self.workers.add(worker)
            worker.start()

    def _execute(self) -> None:
        with SessionLocal() as db:
            sync_next_transcript_safe(db, get_settings())
