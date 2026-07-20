"""PostgreSQL domain model for the Jamal Dialler operational data.

Call recordings and transcript text deliberately live in separate tables.  This
keeps the call queue small and permits a retention job to purge sensitive media
without losing the aggregate result needed for learning.
"""
import enum
from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer,
    JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class CampaignStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    paused = "paused"
    completed = "completed"
    archived = "archived"


class CallStatus(str, enum.Enum):
    queued = "queued"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class Outcome(str, enum.Enum):
    sale = "sale"
    lead = "lead"
    reject = "reject"
    wrong_number = "wrong_number"


class TranscriptSource(str, enum.Enum):
    provider = "provider"
    whisper = "whisper"
    human = "human"


class PromptStatus(str, enum.Enum):
    draft = "draft"
    approved = "approved"
    deployed = "deployed"
    retired = "retired"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(150))
    role: Mapped[str] = mapped_column(String(40), default="operator", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Prompt(TimestampMixin, Base):
    __tablename__ = "prompts"
    __table_args__ = (UniqueConstraint("agent_type", "version", name="uq_prompts_agent_version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False, default="sales_agent")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[PromptStatus] = mapped_column(Enum(PromptStatus, name="prompt_status"), default=PromptStatus.draft, nullable=False)
    change_summary: Mapped[str | None] = mapped_column(Text)
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class Campaign(TimestampMixin, Base):
    __tablename__ = "campaigns"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    # Retained for API compatibility.  New campaigns should reference a frozen Prompt.
    script: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[CampaignStatus] = mapped_column(Enum(CampaignStatus, name="campaign_status"), default=CampaignStatus.draft, nullable=False, index=True)
    prompt_id: Mapped[int | None] = mapped_column(ForeignKey("prompts.id", ondelete="SET NULL"), index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Dubai", nullable=False)
    calling_window_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    calls: Mapped[list["Call"]] = relationship(back_populates="campaign", cascade="all, delete-orphan")
    prompt: Mapped["Prompt | None"] = relationship()


class Prospect(TimestampMixin, Base):
    __tablename__ = "prospects"
    __table_args__ = (UniqueConstraint("phone", name="uq_prospects_phone"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    full_name: Mapped[str | None] = mapped_column(String(150))
    details: Mapped[str | None] = mapped_column(Text)
    attributes_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    do_not_call: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    calls: Mapped[list["Call"]] = relationship(back_populates="prospect")


class Call(TimestampMixin, Base):
    __tablename__ = "calls"
    __table_args__ = (
        CheckConstraint("duration_seconds IS NULL OR duration_seconds >= 0", name="ck_calls_nonnegative_duration"),
        Index("ix_calls_campaign_status_created", "campaign_id", "status", "created_at"),
        Index("ix_calls_campaign_outcome", "campaign_id", "outcome"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    prospect_id: Mapped[int | None] = mapped_column(ForeignKey("prospects.id", ondelete="SET NULL"), index=True)
    # Snapshots preserve the historical dialled identity if the prospect changes.
    phone: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    prospect_name: Mapped[str | None] = mapped_column(String(120))
    details: Mapped[str | None] = mapped_column(Text)
    status: Mapped[CallStatus] = mapped_column(Enum(CallStatus, name="call_status"), default=CallStatus.queued, nullable=False, index=True)
    outcome: Mapped[Outcome | None] = mapped_column(Enum(Outcome, name="call_outcome"), index=True)
    outcome_labeled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome_labeled_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    provider_call_id: Mapped[str | None] = mapped_column(String(120), unique=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    campaign: Mapped["Campaign"] = relationship(back_populates="calls")
    prospect: Mapped["Prospect | None"] = relationship(back_populates="calls")
    metric: Mapped["CallMetric | None"] = relationship(back_populates="call", cascade="all, delete-orphan", uselist=False)
    _transcript: Mapped["Transcript | None"] = relationship(back_populates="call", cascade="all, delete-orphan", uselist=False)
    recording: Mapped["Recording | None"] = relationship(back_populates="call", cascade="all, delete-orphan", uselist=False)

    # Compatibility boundary for the current API.  New integrations should use
    # the Transcript row (including segments and source) directly.
    @property
    def transcript(self) -> str | None:
        return self._transcript.content if self._transcript else None

    @transcript.setter
    def transcript(self, content: str | None) -> None:
        self._transcript = Transcript(content=content) if content is not None else None


class Transcript(TimestampMixin, Base):
    __tablename__ = "transcripts"
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(16))
    source: Mapped[TranscriptSource] = mapped_column(Enum(TranscriptSource, name="transcript_source"), default=TranscriptSource.whisper, nullable=False)
    confidence: Mapped[int | None] = mapped_column(Integer)
    segments_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    call: Mapped["Call"] = relationship(back_populates="_transcript")


class Recording(TimestampMixin, Base):
    __tablename__ = "recordings"
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), primary_key=True)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    content_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    checksum: Mapped[str | None] = mapped_column(String(128))
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    call: Mapped["Call"] = relationship(back_populates="recording")


class CallMetric(TimestampMixin, Base):
    __tablename__ = "call_metrics"
    __table_args__ = tuple(CheckConstraint(f"{field} BETWEEN 0 AND 10", f"ck_metrics_{field}") for field in ("tone", "clarity", "engagement", "objection", "close"))
    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), unique=True)
    tone: Mapped[int] = mapped_column(Integer, nullable=False)
    clarity: Mapped[int] = mapped_column(Integer, nullable=False)
    engagement: Mapped[int] = mapped_column(Integer, nullable=False)
    objection: Mapped[int] = mapped_column(Integer, nullable=False)
    close: Mapped[int] = mapped_column(Integer, nullable=False)
    strength: Mapped[str] = mapped_column(Text, nullable=False)
    weakness: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion: Mapped[str] = mapped_column(Text, nullable=False)
    evaluator_version: Mapped[str] = mapped_column(String(80), default="deterministic-v1", nullable=False)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    call: Mapped["Call"] = relationship(back_populates="metric")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    before_json: Mapped[dict | None] = mapped_column(JSON)
    after_json: Mapped[dict | None] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
