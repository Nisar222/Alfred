from datetime import datetime
from pydantic import BaseModel, Field
from .models import CampaignStatus, CallStatus, Outcome


class CampaignCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    script: str = Field(min_length=10)


class CampaignOut(BaseModel):
    id: int; name: str; script: str; status: CampaignStatus; created_at: datetime
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
    metric: MetricOut | None = None
    model_config = {"from_attributes": True}
