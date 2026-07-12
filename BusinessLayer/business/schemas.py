"""Pydantic request/response schemas for the HTTP API.

Kept separate from the SQLModel tables so the wire contract is explicit and
decoupled from storage. These are what AegisBackend sends and receives.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
class OpenSessionRequest(BaseModel):
    session_id: str = Field(..., description="== AegisBackend conversation_id")
    lead_id: str
    channel: str = "voice"
    direction: str = "outbound"
    provider_call_id: Optional[str] = None
    contact_phone: Optional[str] = Field(
        default=None, description="Caller phone (inbound) — lets the analyzer persist an unknown lead."
    )


class TurnRequest(BaseModel):
    role: str = Field(..., description="'user' | 'bot' | 'system'")
    text: str
    ts: Optional[float] = None


class Turn(BaseModel):
    role: str
    text: str
    ts: Optional[float] = None


class CloseSessionRequest(BaseModel):
    end_reason: Optional[str] = None
    transcript: Optional[list[Turn]] = None
    # Allow close to also create the session if AegisBackend only calls us once.
    lead_id: Optional[str] = None
    channel: Optional[str] = None
    direction: Optional[str] = None


# ---------------------------------------------------------------------------
# Leads / memory
# ---------------------------------------------------------------------------
class MemoryBundle(BaseModel):
    """What the brain reads at session start → fills the LEAD PROFILE slot."""

    lead_id: str
    full_name: str
    language_preference: str
    status: str
    # Optional on the wire so a legacy/NULL DB value never 500s — coerced to
    # "lead" by the validator below (a row with no stage is a plain lead).
    funnel_stage: Optional[str] = "lead"   # admissions lifecycle → drives the talk-track
    lead_priority: Optional[str] = None  # temperature hot/warm/cold (cosmetic); NULL until analyzed
    interest: int
    confidence: int
    facts: dict[str, Any] = {}
    summary: Optional[str] = None
    open_concerns: list[str] = []
    sent_items: list[dict[str, Any]] = []

    @field_validator("funnel_stage", mode="before")
    @classmethod
    def _default_funnel_stage(cls, v: object) -> str:
        return str(v) if v else "lead"


class LeadView(BaseModel):
    lead_id: str
    full_name: str
    email: Optional[str] = None
    phone_e164: str
    source: str
    language_preference: str
    course_interest: Optional[str] = None
    intake_year: Optional[int] = None
    city: Optional[str] = None
    consent_call: bool
    consent_whatsapp: bool
    status: str
    # Optional on the wire so a legacy/NULL DB value never 500s — coerced to
    # "lead" by the validator below.
    funnel_stage: Optional[str] = "lead"
    lead_priority: Optional[str] = None
    is_lead: bool = True
    facts: dict[str, Any] = {}
    interest: int
    confidence: int
    summary: Optional[str] = None
    open_concerns: list[str] = []
    sent_items: list[dict[str, Any]] = []
    call_attempts: int

    @field_validator("funnel_stage", mode="before")
    @classmethod
    def _default_funnel_stage(cls, v: object) -> str:
        return str(v) if v else "lead"
    next_action_at: Optional[datetime] = None
    last_whatsapp_inbound_at: Optional[datetime] = None
    updated_at: datetime


class CreateLeadRequest(BaseModel):
    lead_id: Optional[str] = None
    full_name: str = "Unknown"
    email: Optional[str] = None
    phone_e164: str
    source: str = "api"
    language_preference: str = "en"
    course_interest: Optional[str] = None
    intake_year: Optional[int] = None
    city: Optional[str] = None
    parent_name: Optional[str] = None
    parent_phone_e164: Optional[str] = None
    # Default to consented — a lead we're given (uploaded sheet, API push, web
    # extractor) IS an outbound call/WhatsApp list. Callers can still pass false
    # explicitly to opt a specific lead out.
    consent_call: bool = True
    consent_whatsapp: bool = True
    status: str = "new"
    # Admissions lifecycle stage. Real lead by default ("lead"); raw-data imports
    # pass funnel_stage="raw" → is_lead derived False (inbound-only) until interest
    # promotes them. is_lead is accepted but recomputed from funnel_stage in
    # create_lead, so funnel_stage is the source of truth.
    funnel_stage: str = "lead"
    is_lead: bool = True


class Ack(BaseModel):
    ok: bool = True
    detail: Optional[str] = None
