"""SQLModel table definitions — the 3-table operational store.

  leads     — system of record + cumulative agent memory (facts/summary rollup)
  sessions  — one row per interaction (transcript + per-session analysis as JSON)
  tasks     — outbox of side-effects to execute (send brochure, callback, ...)

All timestamps are stored as naive UTC (`datetime.utcnow()`), consistent across
the codebase, so comparisons are well-defined. JSON columns hold the flexible
bits (facts, transcript, analysis, payload) so the schema stays small.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Column, DateTime
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# Status / enum-ish string constants (kept as plain str for clean JSON + SQL).
# ---------------------------------------------------------------------------
class LeadStatus:
    """Lead status — the OPERATIONAL machine state ONLY (one axis).

    `status` now carries solely the dialer/store campaign state — it no longer
    encodes lead temperature or the admissions lifecycle. Those moved to their
    own columns so each axis is independent:

      • temperature (hot/warm/cold)  → `Lead.lead_priority` (see LeadPriority),
        derived from the analyzer's 0-100 interest score. Cosmetic + escalation
        trigger only; it does NOT gate dialing.
      • admissions lifecycle         → `Lead.funnel_stage` (see FunnelStage):
        raw → lead → application_started → fees_pending → application_submitted.

    RAW (paired with `Lead.is_lead == False`) is the "raw data, not yet a LEAD"
    operational state: never dialed outbound, revived only by an INBOUND contact,
    promoted to a real lead the moment interest is shown (funnel_stage→lead).
    """

    # --- operational machine states (dialer/store-owned) ---
    NEW = "new"
    IN_CALL = "in_call"
    #: A completed call / prior engagement. Set on insert when a lead arrives
    #: already at an application stage (it's been engaged before), so the brain
    #: opens as a follow-up, not first contact. Mirrors AegisBackend LeadStatus.
    CALLED = "called"
    FOLLOWUP = "followup"
    SCHEDULED = "scheduled"
    #: Delegated to a human counsellor (HOT lead / candidate asked for a human),
    #: set AFTER the counsellor-handoff email goes out. Not dialable — the
    #: counsellor owns the next touch — but NOT terminal: the counsellor or a
    #: later conversation can move it on.
    DELEGATED = "delegated"

    #: Raw data in the DB but NOT yet a lead (is_lead=False). Inbound-only.
    RAW = "raw"

    # --- terminal / negative states ---
    #: Candidate showed no interest and no follow-up was agreed. Not dialable,
    #: but NOT terminal — an inbound contact can still revive it (unlike LOST).
    NOT_INTERESTED = "not_interested"
    CONVERTED = "converted"
    LOST = "lost"
    CLOSED = "closed"

    #: Terminal states — the analyzer's rollup must never downgrade out of these.
    TERMINAL = frozenset({CONVERTED, LOST, CLOSED})
    #: Statuses the dialer is allowed to (re)dial from. NEW = a fresh lead,
    #: CALLED = a lead already engaged (inserted at an application stage, or a
    #: prior call) we still want to call for the next step; FOLLOWUP/SCHEDULED are
    #: nurture/retries. RAW is ABSENT (raw data is inbound-only) and DELEGATED is
    #: ABSENT (a human owns it). Lead temperature lives in `lead_priority` and no
    #: longer affects dialability — a warm/cold lead rests on one of these
    #: operational states. Raw data is additionally gated by is_lead=False.
    DIALABLE = frozenset(
        {
            NEW,
            CALLED,
            FOLLOWUP,
            SCHEDULED,
        }
    )


class LeadPriority:
    """Lead temperature — a SEPARATE axis from `status` and `funnel_stage`.

    Derived from the analyzer's 0-100 `interest` score (see `from_interest`).
    Purely for prioritisation/display + the hot-lead escalation trigger; it does
    NOT gate dialing (the dialer reads `status`). HOT leads are escalated to a
    human counsellor (status → DELEGATED); WARM/COLD continue normal nurture.

    Thresholds (per spec): >=80 hot · 50-79 warm · <50 cold.
    """

    HOT = "hot"
    WARM = "warm"
    COLD = "cold"

    ALL = frozenset({HOT, WARM, COLD})

    @staticmethod
    def from_interest(interest: Any) -> Optional[str]:
        """Map a 0-100 interest score onto a temperature tier.

        Returns None when there's no usable score so a never-analyzed lead stays
        NULL rather than defaulting to a misleading 'cold'.
        """
        try:
            n = int(round(float(interest)))
        except (TypeError, ValueError):
            return None
        if n <= 0:
            return None
        if n >= 80:
            return LeadPriority.HOT
        if n >= 50:
            return LeadPriority.WARM
        return LeadPriority.COLD


class FunnelStage:
    """Admissions LIFECYCLE stage — a SEPARATE axis from `status`.

    `status` carries the operational + quality state (new/in_call/followup,
    cold/warm/hot). `funnel_stage` carries WHERE the candidate is in the
    admissions journey:

        raw                   → raw data, not yet a qualified lead (is_lead=False)
        lead                  → a qualified lead (pre-application)
        application_started   → application in progress
        fees_pending          → application complete, fee/payment due
        application_submitted → paid + submitted (under review)

    is_lead is DERIVED from this: a row is a real lead iff funnel_stage != raw
    (see `is_lead_for_stage`). The two columns move together — promote a raw
    contact by setting funnel_stage=lead, which flips is_lead True.
    """

    RAW = "raw"
    LEAD = "lead"
    APPLICATION_STARTED = "application_started"
    FEES_PENDING = "fees_pending"
    APPLICATION_SUBMITTED = "application_submitted"

    #: Every valid stage (for validation on import / advance).
    ALL = frozenset({RAW, LEAD, APPLICATION_STARTED, FEES_PENDING, APPLICATION_SUBMITTED})
    #: Application-lifecycle stages (post-qualification).
    APPLICATION_STAGES = frozenset({APPLICATION_STARTED, FEES_PENDING, APPLICATION_SUBMITTED})


def is_lead_for_stage(funnel_stage: str | None) -> bool:
    """is_lead is derived from the funnel stage: everything is a real lead EXCEPT
    raw data. A missing/blank stage is treated as a lead (back-compat: existing
    rows had no funnel_stage and were real leads)."""
    return (funnel_stage or "").strip().lower() != FunnelStage.RAW


class SessionStatus:
    ACTIVE = "active"
    ENDED = "ended"
    FAILED = "failed"


class TaskStatus:
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"


class KnowledgeStatus:
    PENDING = "pending"        # awaiting director decision
    APPROVED = "approved"      # ingested as a new fact (no supersede)
    SUPERSEDED = "superseded"  # ingested + replaced the conflicting point(s)
    REJECTED = "rejected"      # discarded
    ERROR = "error"            # approved but ingest failed (see ingest_error)


# ---------------------------------------------------------------------------
# leads
# ---------------------------------------------------------------------------
class Lead(SQLModel, table=True):
    __tablename__ = "leads"

    lead_id: str = Field(primary_key=True)
    full_name: str = Field(default="Unknown")
    email: Optional[str] = Field(default=None)
    phone_e164: str = Field(default="", index=True)
    source: str = Field(default="unknown")
    language_preference: str = Field(default="en")

    course_interest: Optional[str] = Field(default=None)
    intake_year: Optional[int] = Field(default=None)
    city: Optional[str] = Field(default=None)
    parent_name: Optional[str] = Field(default=None)
    parent_phone_e164: Optional[str] = Field(default=None)

    consent_call: bool = Field(default=False)
    consent_whatsapp: bool = Field(default=False)
    #: Operational + quality state (new/in_call/followup, cold/warm/hot). The
    #: dialer and analyzer read/write this. SEPARATE from funnel_stage.
    status: str = Field(default=LeadStatus.NEW, index=True)
    #: Admissions LIFECYCLE stage (raw/lead/application_started/fees_pending/
    #: application_submitted) — see FunnelStage. Defaults to "lead" so existing
    #: paths (Excel upload, API push, web extractor) produce real leads; raw
    #: imports set "raw" explicitly. is_lead is derived from this.
    funnel_stage: str = Field(default=FunnelStage.LEAD, index=True)
    #: Raw data (DB contact not yet qualified) vs a real LEAD. DERIVED from
    #: funnel_stage (is_lead == funnel_stage != "raw") and kept in sync wherever
    #: funnel_stage is written. Raw rows are NEVER cold-called (inbound-only); a
    #: conversation that shows interest promotes them (funnel_stage→lead, is_lead
    #: →True). The CRM leads screen filters on is_lead=True; the "Raw Data" view
    #: shows the False rows.
    is_lead: bool = Field(default=True, index=True)

    # --- analysis rollup (written by the analyzer; read as agent memory) ---
    facts: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    interest: int = Field(default=0)         # 0-100
    confidence: int = Field(default=0)       # 0-100
    #: Lead temperature (hot/warm/cold), DERIVED from `interest` by the analyzer
    #: via LeadPriority.from_interest (>=80 hot, 50-79 warm, <50 cold). NULL until
    #: first analyzed. Drives prioritisation/display + hot-lead escalation; does
    #: NOT gate dialing (that reads `status`). SEPARATE axis from status.
    lead_priority: Optional[str] = Field(default=None, index=True)
    # Cumulative, rolling narrative across ALL conversations (not just the last
    # session). Rewritten each analysis by folding the prior summary + the new
    # session, so a counsellor reading it knows the full history before a call.
    summary: Optional[str] = Field(default=None)
    open_concerns: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # Things actually DELIVERED to the candidate (e.g. fee/scholarship details
    # sent on WhatsApp), appended by the action worker on successful send. The
    # next call sees these and follows up instead of re-offering — closes the
    # loop so the agent doesn't keep promising the same thing.
    # Each item: {"item": str, "channel": str, "at": iso8601}
    sent_items: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    last_analyzed_session_ended_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime)
    )
    # Timestamp of the candidate's LAST inbound WhatsApp message — the durable
    # record of the Meta 24h customer-care window. Stamped on every inbound
    # WhatsApp `open_session`; read by AegisBackend's send path (via the lead
    # view) to choose free-form vs template. Survives a backend restart, unlike
    # the in-memory window cache.
    last_whatsapp_inbound_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime)
    )

    # --- campaign control (owned by the dialer) ---
    call_attempts: int = Field(default=0)
    last_dialed_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime))
    next_action_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, index=True)
    )

    version: int = Field(default=0)          # optimistic concurrency
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime))


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------
class Session(SQLModel, table=True):
    __tablename__ = "sessions"

    session_id: str = Field(primary_key=True)   # == AegisBackend conversation_id
    lead_id: str = Field(index=True)
    channel: str = Field(default="voice")        # voice | whatsapp | chat
    direction: str = Field(default="outbound")   # inbound | outbound
    provider_call_id: Optional[str] = Field(default=None)
    # Caller's phone for INBOUND from an unknown number — lets the analyzer mint
    # a durable lead (keyed by phone) after the conversation, so the next contact
    # is recognised. Null for outbound / already-registered sessions.
    contact_phone: Optional[str] = Field(default=None)

    status: str = Field(default=SessionStatus.ACTIVE, index=True)
    end_reason: Optional[str] = Field(default=None)
    analyzed: bool = Field(default=False, index=True)

    # transcript: [{"role": "user"|"bot", "text": str, "ts": float}, ...]
    transcript: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON)
    )
    # analysis: this session's snapshot {summary, interest, sentiment, actions}
    analysis: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))

    started_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime))
    ended_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime))


# ---------------------------------------------------------------------------
# tasks (outbox)
# ---------------------------------------------------------------------------
class Task(SQLModel, table=True):
    __tablename__ = "tasks"

    id: str = Field(primary_key=True)
    lead_id: str = Field(index=True)
    session_id: Optional[str] = Field(default=None)
    type: str = Field(default="")                # send_brochure | schedule_followup | callback
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    channel: Optional[str] = Field(default=None)

    status: str = Field(default=TaskStatus.PENDING, index=True)
    dedupe_key: str = Field(index=True, unique=True)
    attempts: int = Field(default=0)
    max_attempts: int = Field(default=4)
    scheduled_for: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, index=True)
    )
    last_error: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime))


# ---------------------------------------------------------------------------
# knowledge_candidates — facts captured from a director video call, awaiting
# approval before they enter the RAG knowledge base. AegisBackend owns the
# capture/contradiction/ingest logic; this table is the durable record + audit
# trail + the post-call /knowledge-review queue. Scalar columns are what we
# filter/sort on (get them right — create_all can't ALTER later); the rich,
# evolving bits live in JSON (conflict_items, resolution, events, meta).
# ---------------------------------------------------------------------------
class KnowledgeCandidate(SQLModel, table=True):
    __tablename__ = "knowledge_candidates"

    candidate_id: str = Field(primary_key=True)             # "kc_<uuid>"
    conversation_id: str = Field(default="", index=True)    # avatar session it came from
    tenant_id: str = Field(default="", index=True)          # KB tenant (multi-tenant routing)
    lead_id: Optional[str] = Field(default=None, index=True)
    status: str = Field(default=KnowledgeStatus.PENDING, index=True)

    text: str = Field(default="")                           # the fact to ingest
    heading: str = Field(default="")
    topic: str = Field(default="")
    kb: str = Field(default="university")                   # target KB label
    source_span: Optional[str] = Field(default=None)        # raw director utterance
    trigger: str = Field(default="explicit")               # explicit | wake_phrase | auto

    confidence: int = Field(default=0)                      # 0-100
    conflict_score: int = Field(default=0)                  # 0-100
    blocking: bool = Field(default=False)

    ingested: bool = Field(default=False)
    ingested_point_id: Optional[str] = Field(default=None)  # first point (supersede/unlearn link)
    ingest_error: Optional[str] = Field(default=None)
    resolved_by: Optional[str] = Field(default=None)
    # Revision link: a candidate that edits an already-Active fact points at the
    # live fact's point id so approval supersedes-self.
    supersedes: Optional[str] = Field(default=None)

    # Rich / evolving — JSON so future shape changes need no migration.
    conflict_items: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    resolution: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    events: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    meta: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    version: int = Field(default=0)                         # optimistic concurrency
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime))
    resolved_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime))
