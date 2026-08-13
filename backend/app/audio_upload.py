"""Validate and persist browser-uploaded opening audio files."""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .models import AudioAsset, AudioAssetStatus

ALLOWED_AUDIO_SUFFIXES = {
    ".mp3": frozenset({
        "audio/mpeg", "audio/mp3", "audio/mpeg3", "audio/x-mpeg", "application/octet-stream",
    }),
    ".wav": frozenset({
        "audio/wav", "audio/x-wav", "audio/wave", "application/octet-stream",
    }),
}


class AudioUploadError(ValueError):
    """User-facing validation or storage problem."""


def validate_audio_upload(filename: str, content_type: str) -> str:
    """Return normalized suffix or raise AudioUploadError with a clear message."""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_AUDIO_SUFFIXES:
        raise AudioUploadError("Upload an MP3 or WAV file. The filename must end in .mp3 or .wav.")
    normalized_type = (content_type or "").lower().strip()
    if normalized_type and normalized_type not in ALLOWED_AUDIO_SUFFIXES[suffix]:
        raise AudioUploadError(
            f"Alfred cannot use this file type ({content_type or 'unknown'}). "
            f"Choose an MP3 or WAV file."
        )
    return suffix


def normalized_content_type(suffix: str, content_type: str) -> str:
    normalized_type = (content_type or "").lower().strip()
    if normalized_type in ALLOWED_AUDIO_SUFFIXES[suffix]:
        return normalized_type
    return "audio/mpeg" if suffix == ".mp3" else "audio/wav"


def store_audio_asset(
    db: Session,
    settings: Settings,
    *,
    filename: str,
    content_type: str,
    raw: bytes,
) -> tuple[AudioAsset, bool]:
    """Write audio locally and return the asset plus whether it was newly stored."""
    if not raw:
        raise AudioUploadError("The audio file is empty.")
    if len(raw) > settings.max_audio_upload_bytes:
        raise AudioUploadError(
            f"The audio file is too large. Maximum size is "
            f"{settings.max_audio_upload_bytes // (1024 * 1024)} MB."
        )

    suffix = validate_audio_upload(filename, content_type)
    display_name = Path(filename or f"audio{suffix}").name
    resolved_type = normalized_content_type(suffix, content_type)
    checksum = hashlib.sha256(raw).hexdigest()
    existing = db.scalar(select(AudioAsset).where(AudioAsset.checksum == checksum))

    storage_dir = Path(settings.audio_storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)

    if existing and existing.status == AudioAssetStatus.ready:
        return existing, False

    storage_key = f"{uuid.uuid4().hex}{suffix}"
    destination = storage_dir / storage_key
    try:
        destination.write_bytes(raw)
    except OSError as exc:
        raise AudioUploadError("Alfred could not store the audio file on the server.") from exc

    if existing and existing.status == AudioAssetStatus.deleted:
        old_path = storage_dir / existing.storage_key
        existing.display_name = display_name
        existing.storage_key = storage_key
        existing.content_type = resolved_type
        existing.size_bytes = len(raw)
        existing.status = AudioAssetStatus.ready
        db.commit()
        db.refresh(existing)
        if old_path.exists() and old_path != destination:
            try:
                old_path.unlink()
            except OSError:
                pass
        return existing, True

    asset = AudioAsset(
        display_name=display_name,
        storage_key=storage_key,
        content_type=resolved_type,
        size_bytes=len(raw),
        checksum=checksum,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset, True
