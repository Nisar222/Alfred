from datetime import datetime
from pydantic import BaseModel, Field
from .models import AudioAssetStatus, CampaignStatus, CallStatus, Outcome, PlaybookStatus


class CampaignCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    # Retained for existing clients while they move to playbooks.
    script: str = Field(default="Use the approved call playbook.", min_length=10)
    playbook_version_id: int | None = None
    timezone: str | None = Field(default=None, max_length=64)
    calling_window_json: dict | None = None
    caller_id_override: str | None = Field(default=None, max_length=80)
    max_concurrent_calls_override: int | None = Field(default=None, ge=1, le=16)


class CampaignOut(BaseModel):
    id: int; name: str; script: str; status: CampaignStatus; created_at: datetime
    playbook_version_id: int | None = None; timezone: str
    calling_window_json: dict = Field(default_factory=dict)
    caller_id_override: str | None = None; max_concurrent_calls_override: int | None = None
    model_config = {"from_attributes": True}


class Contact(BaseModel):
    phone: str = Field(min_length=3, max_length=40)
    name: str | None = None
    details: str | None = None


class OutcomeUpdate(BaseModel):
    outcome: Outcome


class ContactUploadResult(BaseModel):
    queued: int


class MetricOut(BaseModel):
    tone: int; clarity: int; engagement: int; objection: int; close: int
    strength: str; weakness: str; suggestion: str
    model_config = {"from_attributes": True}


class CallOut(BaseModel):
    id: int; campaign_id: int; phone: str; prospect_name: str | None; status: CallStatus
    outcome: Outcome | None; transcript: str | None; duration_seconds: int | None; created_at: datetime
    provider_call_id: str | None = None; failure_reason: str | None = None
    started_at: datetime | None = None; completed_at: datetime | None = None
    recording_available: bool = False
    configuration_snapshot_json: dict = Field(default_factory=dict)
    metric: MetricOut | None = None
    model_config = {"from_attributes": True}


class GlobalSettingsUpdate(BaseModel):
    default_timezone: str = Field(default="Asia/Dubai", max_length=64)
    default_calling_window_json: dict = Field(default_factory=dict)
    max_concurrent_calls: int = Field(default=1, ge=1, le=16)
    recording_retention_days: int = Field(default=30, ge=1, le=3650)
    test_call_enabled: bool = False


class GlobalSettingsOut(GlobalSettingsUpdate):
    id: int
    model_config = {"from_attributes": True}


class AudioAssetOut(BaseModel):
    id: int; display_name: str; content_type: str; size_bytes: int; checksum: str
    status: AudioAssetStatus; created_at: datetime
    model_config = {"from_attributes": True}


class PlaybookVersionCreate(BaseModel):
    script: str = Field(min_length=10)
    opening_audio_id: int | None = None
    recording_enabled: bool = True
    approve: bool = False


class PlaybookCreate(PlaybookVersionCreate):
    name: str = Field(min_length=2, max_length=150)


class PlaybookVersionOut(BaseModel):
    id: int; playbook_id: int; version: int; script: str; opening_audio_id: int | None
    recording_enabled: bool; status: PlaybookStatus; created_at: datetime
    model_config = {"from_attributes": True}


class PlaybookOut(BaseModel):
    id: int; name: str; status: PlaybookStatus; current_version_id: int | None; created_at: datetime
    versions: list[PlaybookVersionOut] = Field(default_factory=list)
    model_config = {"from_attributes": True}
