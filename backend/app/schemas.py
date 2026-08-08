from datetime import datetime
import re
from pydantic import BaseModel, Field, field_validator
from .models import AudioAssetStatus, CampaignStatus, CallStatus, Outcome, PlaybookStatus, Sentiment


class CampaignCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    # Retained for existing clients while they move to playbooks.
    script: str = Field(default="Use the approved call playbook.", min_length=10)
    playbook_version_id: int | None = None
    timezone: str | None = Field(default=None, max_length=64)
    calling_window_json: dict | None = None
    caller_id_override: str | None = Field(default=None, max_length=80)
    max_concurrent_calls_override: int | None = Field(default=None, ge=1, le=16)
    dtmf_queue_extension_override: str | None = Field(default=None, pattern=r"^\d{2,10}$")


class CampaignOut(BaseModel):
    id: int; name: str; script: str; status: CampaignStatus; created_at: datetime
    playbook_version_id: int | None = None; timezone: str
    calling_window_json: dict = Field(default_factory=dict)
    caller_id_override: str | None = None; max_concurrent_calls_override: int | None = None
    dtmf_queue_extension_override: str | None = None
    model_config = {"from_attributes": True}


class Contact(BaseModel):
    phone: str = Field(min_length=3, max_length=40)
    name: str | None = None
    details: str | None = None


def normalize_dial_destination(raw: str) -> str:
    """Accept E.164 or national digits for 3CX outbound rules that prepend a prefix."""
    cleaned = re.sub(r"[\s\-().]", "", raw.strip())
    if not cleaned:
        raise ValueError("Enter a phone number to call.")
    if cleaned.startswith("+"):
        if not re.fullmatch(r"\+[1-9]\d{7,14}", cleaned):
            raise ValueError("Use + followed by country code and number, for example +46793555436.")
        return cleaned
    if cleaned.startswith("0"):
        cleaned = cleaned.lstrip("0")
    if not re.fullmatch(r"[1-9]\d{5,14}", cleaned):
        raise ValueError(
            "Use digits only (for example 793555436 when 3CX adds the country code) "
            "or full international format (+46793555436)."
        )
    return cleaned


class TestCallRequest(BaseModel):
    destination: str = Field(min_length=6, max_length=24)

    def model_post_init(self, __context) -> None:
        object.__setattr__(self, "destination", normalize_dial_destination(self.destination))


class DtmfDiagnosticOut(BaseModel):
    status: str
    digit: str | None = None
    destination: str | None = None


class ForwardChainDiagnosticOut(BaseModel):
    status: str
    digit: str | None = None
    first_destination: str | None = None
    forward_destination: str | None = None
    transfer_message_status: str | None = None
    message: str | None = None


class HoldThenStreamDiagnosticOut(BaseModel):
    status: str
    digit: str | None = None
    first_destination: str | None = None
    forward_destination: str | None = None
    hold_detected: bool = False
    participant_status: str | None = None
    stream_status: str | None = None
    forward_status: str | None = None
    message: str | None = None


class AgentNotificationOut(BaseModel):
    id: int
    call_id: int
    recipient_user_id: int | None
    recipient_extension: str | None
    customer_name: str | None
    campaign_name: str
    menu_option: str | None
    routed_destination: str
    created_at: datetime
    delivered_at: datetime | None
    read_at: datetime | None
    model_config = {"from_attributes": True}


class ThreeCXDirectoryUserOut(BaseModel):
    user_id: str
    name: str
    extension: str | None = None
    email: str | None = None


class ThreeCXDirectoryMemberOut(BaseModel):
    user_id: str | None = None
    extension: str | None = None


class ThreeCXDirectoryGroupOut(BaseModel):
    group_id: str
    extension: str | None = None
    name: str
    members: list[ThreeCXDirectoryMemberOut] = Field(default_factory=list)


class ThreeCXDirectoryOut(BaseModel):
    users: list[ThreeCXDirectoryUserOut] = Field(default_factory=list)
    ring_groups: list[ThreeCXDirectoryGroupOut] = Field(default_factory=list)
    queues: list[ThreeCXDirectoryGroupOut] = Field(default_factory=list)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=256)


class CurrentUserOut(BaseModel):
    id: int
    email: str
    display_name: str
    role: str
    threecx_extension: str | None = None


class LoginOut(BaseModel):
    user: CurrentUserOut
    csrf_token: str


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=8, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class AdminUserOut(CurrentUserOut):
    is_active: bool
    threecx_user_id: str | None = None


class AdminUserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=150)
    role: str = Field(default="agent", pattern=r"^(owner|supervisor|agent)$")
    threecx_user_id: str | None = Field(default=None, max_length=80)
    threecx_extension: str | None = Field(default=None, pattern=r"^\d{2,10}$")


class ThreeCXLinkUpdate(BaseModel):
    threecx_user_id: str | None = Field(default=None, max_length=80)
    threecx_extension: str | None = Field(default=None, pattern=r"^\d{2,10}$")


class AdminUserAccessUpdate(BaseModel):
    password: str = Field(min_length=12, max_length=256)
    is_active: bool = True


class OutcomeUpdate(BaseModel):
    outcome: Outcome


class SentimentUpdate(BaseModel):
    sentiment: Sentiment


class ContactUploadResult(BaseModel):
    queued: int


class LiveCallOut(BaseModel):
    id: int
    prospect_name: str | None
    phone: str
    started_at: datetime | None
    elapsed_seconds: int = 0


class CampaignLiveStatusOut(BaseModel):
    id: int
    name: str
    lines_in_use: int
    lines_available: int
    queued: int
    completed_today: int
    failed_today: int
    live_calls: list[LiveCallOut] = Field(default_factory=list)


class LiveStatusOut(BaseModel):
    max_concurrent_calls: int
    lines_in_use: int
    active_campaigns: list[CampaignLiveStatusOut] = Field(default_factory=list)


class MetricOut(BaseModel):
    tone: int; clarity: int; engagement: int; objection: int; close: int
    strength: str; weakness: str; suggestion: str
    model_config = {"from_attributes": True}


class CallListItemOut(BaseModel):
    """Lightweight call row for dashboard lists — omits transcript and playbook snapshot."""
    id: int; campaign_id: int; phone: str; prospect_name: str | None; status: CallStatus
    outcome: Outcome | None; duration_seconds: int | None; created_at: datetime
    sentiment: Sentiment = Sentiment.unknown; sentiment_confidence: int | None = None; sentiment_source: str = "not_available"
    failure_reason: str | None = None; attempt_number: int = 1; scheduled_for: datetime | None = None
    started_at: datetime | None = None; completed_at: datetime | None = None
    recording_available: bool = False
    model_config = {"from_attributes": True}


class CallOut(CallListItemOut):
    transcript: str | None = None
    provider_call_id: str | None = None
    failure_category: str | None = None; previous_attempt_id: int | None = None
    dtmf_digit: str | None = None; routed_destination: str | None = None; routing_status: str | None = None
    call_summary: str | None = None
    transcript_segments: list[dict] = Field(default_factory=list)
    configuration_snapshot_json: dict = Field(default_factory=dict)
    metric: MetricOut | None = None


class GlobalSettingsUpdate(BaseModel):
    default_timezone: str = Field(default="Asia/Dubai", max_length=64)
    default_calling_window_json: dict = Field(default_factory=dict)
    max_concurrent_calls: int = Field(default=1, ge=1, le=16)
    recording_retention_days: int = Field(default=30, ge=1, le=3650)
    retry_max_attempts: int = Field(default=1, ge=1, le=5)
    retry_delay_minutes: int = Field(default=60, ge=5, le=10080)
    retry_no_answer: bool = True
    retry_busy: bool = True
    retry_provider_failure: bool = True
    dtmf_routing_enabled: bool = False
    dtmf_menu_digit: str = Field(default="1", pattern=r"^[0-9]$")
    dtmf_queue_extension: str | None = Field(default=None, pattern=r"^\d{2,10}$")
    dtmf_routes_json: dict[str, str] = Field(default_factory=dict)
    test_call_enabled: bool = False
    live_campaign_calling_enabled: bool = False

    @field_validator("dtmf_routes_json")
    @classmethod
    def validate_dtmf_routes_json(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, ext in (value or {}).items():
            digit = str(key).strip()
            if not re.fullmatch(r"[0-9]", digit):
                raise ValueError(f"DTMF route key must be a single digit 0-9, got {key!r}")
            extension = str(ext or "").strip()
            if not extension:
                continue
            if not re.fullmatch(r"\d{2,10}", extension):
                raise ValueError(f"DTMF route extension must be 2-10 digits, got {ext!r}")
            normalized[digit] = extension
        return normalized


class GlobalSettingsOut(GlobalSettingsUpdate):
    id: int
    model_config = {"from_attributes": True}


class AudioAssetOut(BaseModel):
    id: int; display_name: str; content_type: str; size_bytes: int; checksum: str
    status: AudioAssetStatus; created_at: datetime
    reused: bool = False
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
