"""Lead model + JSON-file-backed repo.

Single source of truth for "who is the candidate on the other end of this
channel". Every channel adapter calls `LeadRepo.get().get_by_id(...)` or
`find_by_phone(...)` on the first frame.

Today: JSON file under `test-data/leads.json`, process-wide cache.
Tomorrow: swap the body of LeadRepo to read/write Postgres; channels don't
notice — the public API is the same.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, Field

from agent_backend.config import get_settings


# ---------------------------------------------------------------------------
# Funnel status — the brain reads this to pick its opening line.
# Values are stable strings so JSON round-trips cleanly.
# ---------------------------------------------------------------------------
class LeadStatus(StrEnum):
    """Operational call-status the brain reads to pick its opening talk-track.

    Mirrors BusinessLayer's LeadStatus (business/models.py) — keep the two in
    sync. This is the OPERATIONAL axis only. Lead temperature (hot/warm/cold) now
    lives on `Lead.lead_priority` and the admissions lifecycle on
    `Lead.funnel_stage`; the status_playbook / funnel_playbook turn each axis into
    a per-call opening hint drawn from the SNU Counsellor Handbook scenarios.
    """

    # --- operational machine states (dialer/store-owned) ---
    NEW         = "new"           # CRM just created the lead; never contacted
    WELCOMED    = "welcomed"      # WhatsApp welcome sent
    SCHEDULING  = "scheduling"    # slot picker presented, awaiting pick
    SCHEDULED   = "scheduled"     # slot picked, awaiting the call
    IN_CALL     = "in_call"       # call active right now
    CALLED      = "called"        # call done / prior engagement → opens as follow-up
    FOLLOWUP    = "followup"      # nurture follow-ups
    DELEGATED   = "delegated"     # handed to a human counsellor (hot lead)
    RAW         = "raw"           # raw data, not yet a LEAD (is_lead=False); inbound-only

    # --- terminal / negative ---
    NOT_INTERESTED = "not_interested"
    CONVERTED   = "converted"
    LOST        = "lost"
    CLOSED      = "closed"


class Lead(BaseModel):
    """One applicant. Mutates through the funnel; `lead_id` is stable forever."""
    lead_id: str
    full_name: str
    email: str | None = None              # plain str; install pydantic[email] later if you want validation
    phone_e164: str                       # +91XXXXXXXXXX
    source: str = "unknown"
    language_preference: str = "en"       # 'en' | 'hi' | 'ta' | 'te' | 'mr' | ...

    course_interest: str | None = None
    intake_year: int | None = None
    city: str | None = None
    parent_name: str | None = None
    parent_phone_e164: str | None = None

    status: LeadStatus = LeadStatus.NEW
    # Admissions LIFECYCLE stage (mirrors BusinessLayer's Lead.funnel_stage):
    # raw | lead | application_started | fees_pending | application_submitted.
    # The brain reads this to know where the candidate is in the journey (a 'raw'
    # contact is one we already have data for → don't re-ask the basics).
    funnel_stage: str = "lead"
    # Lead TEMPERATURE (hot/warm/cold), a SEPARATE axis from status + funnel_stage.
    # Derived by the BusinessLayer analyzer from the interest score (>=80 hot,
    # 50-79 warm, <50 cold); overlaid here at session start for the LEAD PROFILE.
    # None until the lead has been analyzed. Cosmetic/prioritisation only — it
    # does not change how the brain dials; hot leads are escalated by BusinessLayer.
    lead_priority: str | None = None
    # Raw data (DB contact not yet qualified) vs a real LEAD. Derived from
    # funnel_stage (is_lead == funnel_stage != 'raw'). Raw rows are inbound-only.
    is_lead: bool = True
    consent_whatsapp: bool = False
    consent_call: bool = False

    # Brain-derived fields. Populated by the end-of-session summariser as
    # the lead progresses; rendered into the LEAD PROFILE prompt slot.
    persona_summary: str | None = None
    last_session_summary: str | None = None
    open_concerns: list[str] = Field(default_factory=list)

    # Cumulative candidate facts (marks, entrance scores, budget, ...) extracted
    # by the BusinessLayer's post-call analyzer and overlaid onto this lead at
    # session start. Rendered into LEAD PROFILE so follow-up conversations are
    # context-aware. Empty for leads never analyzed / when BusinessLayer is off.
    facts: dict = Field(default_factory=dict)

    # Materials already DELIVERED to the candidate (e.g. fee/scholarship details
    # sent on WhatsApp), from the BusinessLayer. Rendered into LEAD PROFILE so the
    # agent FOLLOWS UP on them instead of re-offering — closes the "I'll send it"
    # loop. Each item: {"item": str, "channel": str, "at": iso8601}.
    sent_items: list[dict] = Field(default_factory=list)

    # Optional live conversation-state dict (facts/sentiment). The per-turn
    # side-rail extractor that used to populate this was removed — lead scoring
    # now happens once-per-call in the BusinessLayer analyzer (see lead_priority /
    # facts above). The field is retained because the barge-in classifier reads
    # `conversation_state.get("sentiment")` defensively; it's simply None today.
    # Stored as a plain dict on the wire (not a typed model) so this `data` layer
    # stays agnostic of `llm_agent`. None for leads we haven't spoken to yet.
    conversation_state: dict | None = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Repo
# ---------------------------------------------------------------------------
class LeadRepo:
    """Process-singleton lead store. Thread-safe."""

    _instance: "LeadRepo | None" = None
    _instance_lock = threading.Lock()

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._by_id: dict[str, Lead] = {}
        self._by_phone: dict[str, str] = {}

    # --- access ---------------------------------------------------------
    @classmethod
    def get(cls) -> "LeadRepo":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(Path(get_settings().leads_file).resolve())
                cls._instance.reload()
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Test-only hook to force a fresh repo (after env / file changes)."""
        with cls._instance_lock:
            cls._instance = None

    # --- load / save ----------------------------------------------------
    def reload(self) -> None:
        with self._lock:
            self._by_id.clear()
            self._by_phone.clear()
            if not self._path.exists():
                # First-run convenience — empty repo, not an error.
                return
            with self._path.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
            for item in raw:
                lead = Lead.model_validate(item)
                self._by_id[lead.lead_id] = lead
                if lead.phone_e164:
                    self._by_phone[_norm_phone(lead.phone_e164)] = lead.lead_id

    def _persist(self) -> None:
        # No-op by design: leads.json is a READ-ONLY, temporary seed (slated for
        # removal). The repo loads it at boot but never writes back —
        # upsert/set_status mutate the in-memory repo only (so the live call/turn
        # sees updates), while the durable lead store is the BusinessLayer.
        return

    # --- reads ---------------------------------------------------------
    def get_by_id(self, lead_id: str) -> Lead | None:
        with self._lock:
            return self._by_id.get(lead_id)

    def find_by_phone(self, phone_e164: str) -> Lead | None:
        with self._lock:
            lid = self._by_phone.get(_norm_phone(phone_e164))
            return self._by_id.get(lid) if lid else None

    def find_or_create_by_phone(self, phone_e164: str, *, source: str = "inbound_voice") -> Lead:
        """Look up a lead by phone, or create a placeholder one if unknown.

        Used by INBOUND channels (PSTN voice, WhatsApp webhook) where a
        stranger reaches out and we want to start a conversation without
        bouncing them away. The created lead has a synthetic lead_id like
        `inb-<6hex>` and `status=NEW`. The brain can later prompt for
        their name and update the lead.
        """
        normalised = _norm_phone(phone_e164)
        with self._lock:
            lid = self._by_phone.get(normalised)
            if lid:
                existing = self._by_id.get(lid)
                if existing:
                    return existing
        # Create outside the lock to avoid re-entrancy with `upsert`.
        import uuid as _uuid
        new_lead = Lead(
            lead_id=f"inb-{_uuid.uuid4().hex[:6]}",
            full_name="Unknown",
            phone_e164=normalised,
            source=source,
            status=LeadStatus.NEW,
        )
        return self.upsert(new_lead)

    def all(self) -> Iterable[Lead]:
        with self._lock:
            return list(self._by_id.values())

    def count(self) -> int:
        with self._lock:
            return len(self._by_id)

    # --- writes --------------------------------------------------------
    def upsert(self, lead: Lead) -> Lead:
        with self._lock:
            lead.updated_at = datetime.utcnow()
            self._by_id[lead.lead_id] = lead
            if lead.phone_e164:
                self._by_phone[_norm_phone(lead.phone_e164)] = lead.lead_id
            self._persist()
            return lead

    def set_status(self, lead_id: str, status: LeadStatus) -> Lead | None:
        with self._lock:
            lead = self._by_id.get(lead_id)
            if not lead:
                return None
            lead.status = status
            lead.updated_at = datetime.utcnow()
            self._persist()
            return lead


# Analysis-derived fields owned by the BusinessLayer — excluded from leads.json
# on persist (see LeadRepo._persist). They're hydrated into the in-memory Lead
# per call for the prompt, but never written back to the basic-lead file.
_DERIVED_FIELDS = {
    "persona_summary",
    "last_session_summary",
    "open_concerns",
    "facts",
    "sent_items",
    "conversation_state",
}


def _norm_phone(p: str) -> str:
    """Canonicalise a phone number to `+<digits>` E.164 form.

    Providers disagree on the leading '+': ACS/WhatsApp send it, Plivo voice
    sends bare digits ("919610373417"), while leads store "+919610373417". Keying
    on the raw value made an inbound call miss its existing lead → a new
    "Unknown" lead with no memory. Stripping then re-adding a single '+' makes
    the stored key and any inbound number match regardless of provider format.
    """
    p = (p or "").strip().replace(" ", "").replace("-", "").lstrip("+")
    return ("+" + p) if p.isdigit() else (p or "")
