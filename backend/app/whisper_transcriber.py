"""On-prem Whisper transcription for linked 3CX recordings."""
from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
import tempfile
import threading
from pathlib import Path

from .config import Settings

_model = None
_model_lock = threading.Lock()


@dataclass(frozen=True)
class TranscriptionResult:
    content: str
    language: str | None
    confidence: int | None
    segments: list[dict]
    summary: str


def _whisper_model(settings: Settings):
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        from faster_whisper import WhisperModel

        _model = WhisperModel(
            settings.whisper_model_size,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
        )
        return _model


def _channel_count(path: Path) -> int:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=channels", "-of", "json", str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams") or []
    if not streams:
        return 1
    return max(1, int(streams[0].get("channels") or 1))


def _extract_channel(source: Path, destination: Path, channel_index: int) -> None:
    split_names = ("left", "right")
    split_label = split_names[channel_index] if channel_index < len(split_names) else f"c{channel_index}"
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(source),
            "-filter_complex", f"[0:a]channelsplit=channel_layout=stereo[{split_names[0]}][{split_names[1]}]",
            "-map", f"[{split_label}]",
            str(destination),
        ],
        capture_output=True,
        check=True,
    )


def _transcribe_path(path: Path, settings: Settings, speaker: str) -> tuple[list[dict], str | None, int | None]:
    model = _whisper_model(settings)
    segments_iter, info = model.transcribe(
        str(path),
        beam_size=settings.whisper_beam_size,
        vad_filter=True,
    )
    segments: list[dict] = []
    lines: list[str] = []
    probs: list[float] = []
    for segment in segments_iter:
        text = (segment.text or "").strip()
        if not text:
            continue
        segments.append({
            "speaker": speaker,
            "text": text,
            "start": round(float(segment.start), 2),
            "end": round(float(segment.end), 2),
        })
        lines.append(text)
        if segment.avg_logprob is not None:
            probs.append(float(segment.avg_logprob))
    language = getattr(info, "language", None)
    confidence = None
    if probs:
        # Map average logprob (-inf..0) to a simple 0-100 style confidence.
        average = sum(probs) / len(probs)
        confidence = max(35, min(95, int(80 + average * 20)))
    return segments, language, confidence


def transcribe_recording_bytes(audio_bytes: bytes, settings: Settings) -> TranscriptionResult:
    """Transcribe one recording in memory via a short-lived temp file."""
    from .call_analysis import generate_call_summary

    with tempfile.TemporaryDirectory(prefix="alfred-whisper-") as tmp:
        root = Path(tmp)
        source = root / "recording.wav"
        source.write_bytes(audio_bytes)

        all_segments: list[dict] = []
        languages: list[str] = []
        confidences: list[int] = []

        channels = _channel_count(source)
        if channels >= 2:
            speakers = ("agent", "customer")
            for index, speaker in enumerate(speakers):
                channel_path = root / f"{speaker}.wav"
                _extract_channel(source, channel_path, index)
                segments, language, confidence = _transcribe_path(channel_path, settings, speaker)
                all_segments.extend(segments)
                if language:
                    languages.append(language)
                if confidence is not None:
                    confidences.append(confidence)
        else:
            segments, language, confidence = _transcribe_path(source, settings, "agent")
            all_segments.extend(segments)
            if language:
                languages.append(language)
            if confidence is not None:
                confidences.append(confidence)

        all_segments.sort(key=lambda item: (item.get("start") or 0, item.get("speaker") or ""))
        content = "\n".join(
            f"{'Agent' if seg['speaker'] == 'agent' else 'Customer' if seg['speaker'] == 'customer' else 'Speaker'}: {seg['text']}"
            for seg in all_segments
        )
        summary = generate_call_summary(content)
        return TranscriptionResult(
            content=content,
            language=languages[0] if languages else None,
            confidence=round(sum(confidences) / len(confidences)) if confidences else None,
            segments=all_segments,
            summary=summary,
        )
