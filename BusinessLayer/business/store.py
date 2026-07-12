"""Data-access layer — every DB read/write goes through one `Store`.

Backed by SQLAlchemy async over PostgreSQL (asyncpg). The cross-channel merge
(`apply_session_analysis`) runs under a per-lead in-process lock + a `version`
compare-and-set; when you scale to multiple worker processes, swap the in-process
lock for a Postgres advisory lock — nothing else changes.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, text

from business.db import session_scope
from business.domain.merge import (
    clamp_score,
    fold_status,
    merge_facts,
    merge_open_concerns,
)
from business.logging import get_logger
from business.models import (
    FunnelStage,
    KnowledgeCandidate,
    Lead,
    LeadPriority,
    LeadStatus,
    Session,
    SessionStatus,
    Task,
    TaskStatus,
)

log = get_logger(__name__)


def _now() -> datetime:
    return datetime.utcnow()


# Floor applied when a just-called lead is folded into a dialable status but the
# analysis didn't carry a concrete follow-up time. A real `schedule_followup`
# action (if the analyzer emitted one) overwrites this shortly after via the
# actions worker; this is only the safety net for the gap, and matches the
# next-day default used everywhere else (schedule_followup / missed-call retry).
_DEFAULT_FOLLOWUP_MINUTES = 1440

# A raw-data contact (funnel_stage=raw) is promoted to a real LEAD when the
# analyzed conversation shows genuine interest. Temperature/status are now
# separate axes, so the gate is: the analyzer measured at least this much
# interest, OR a human was looped in (status delegated), OR the candidate reached
# an application stage (funnel advanced below). A not_interested/near-zero read
# keeps it raw (inbound-only). The 10-point floor matches the old "any interest
# tier present" boundary. Promotion sets funnel_stage=lead + is_lead=True.
_PROMOTE_MIN_INTEREST = 10

# Admissions-lifecycle ordering for a FORWARD-ONLY funnel fold — a later nurture
# call must never regress a paid/submitted lead back to an earlier stage.
_FUNNEL_RANK = {
    FunnelStage.RAW: 0,
    FunnelStage.LEAD: 1,
    FunnelStage.APPLICATION_STARTED: 2,
    FunnelStage.FEES_PENDING: 3,
    FunnelStage.APPLICATION_SUBMITTED: 4,
}


class Store:
    def __init__(self) -> None:
        self._lead_locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def _lead_lock(self, lead_id: str) -> asyncio.Lock:
        async with self._guard:
            lk = self._lead_locks.get(lead_id)
            if lk is None:
                lk = asyncio.Lock()
                self._lead_locks[lead_id] = lk
            return lk

    # ======================================================================
    # Leads
    # ======================================================================
    async def get_lead(self, lead_id: str) -> Lead | None:
        async with session_scope() as db:
            return await db.get(Lead, lead_id)

    async def find_lead_by_phone(self, phone_e164: str) -> Lead | None:
        # Match regardless of how the stored/queried number carries the leading
        # '+': canonical "+digits", bare "digits", all forms accepted. Providers
        # disagree on the '+' (Plivo sends bare digits inbound), and seeds may
        # store either — so we compare against both forms.
        norm = _norm_phone(phone_e164)          # "+919..."
        digits = norm.lstrip("+")               # "919..."
        async with session_scope() as db:
            res = await db.execute(
                select(Lead).where(Lead.phone_e164.in_([norm, digits]))
            )
            return res.scalars().first()

    async def list_leads(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        status: str | None = None,
        is_lead: bool | None = None,
    ) -> list[Lead]:
        async with session_scope() as db:
            stmt = select(Lead).order_by(Lead.updated_at.desc())
            if status:
                stmt = stmt.where(Lead.status == status)
            # None → no filter (all rows); True → real leads (CRM screen);
            # False → raw data (the "Raw Data" view).
            if is_lead is not None:
                stmt = stmt.where(Lead.is_lead == is_lead)
            stmt = stmt.offset(offset).limit(limit)
            res = await db.execute(stmt)
            return list(res.scalars().all())

    async def count_leads(self) -> int:
        async with session_scope() as db:
            res = await db.execute(select(func.count()).select_from(Lead))
            return int(res.scalar_one())

    async def reset_all(self) -> dict[str, int]:
        """DESTRUCTIVE: empty the three operational tables (leads, sessions,
        tasks) but KEEP the tables/schema. Returns the row counts removed.

        Uses a single TRUNCATE so all three are cleared atomically and fast.
        CASCADE is defensive (no FKs today, but safe if any are added later);
        RESTART IDENTITY resets any identity sequences (no-op for our string
        PKs, kept for correctness)."""
        async with session_scope() as db:
            before = {}
            for t in ("leads", "sessions", "tasks"):
                r = await db.execute(text(f"SELECT count(*) FROM {t}"))  # noqa: S608 — fixed names
                before[t] = int(r.scalar_one())
            await db.execute(
                text("TRUNCATE TABLE tasks, sessions, leads RESTART IDENTITY CASCADE")
            )
            return before

    async def create_lead(self, lead: Lead) -> Lead:
        # Enforce the invariant: is_lead is derived from funnel_stage (raw ⇒
        # False). Keeps a caller that sets only funnel_stage (or only is_lead)
        # internally consistent.
        from business.models import FunnelStage, LeadStatus, is_lead_for_stage

        lead.is_lead = is_lead_for_stage(lead.funnel_stage)
        # A lead inserted ALREADY at an application stage (started / fees_pending /
        # submitted) has, by definition, been engaged before — so set the
        # operational status to CALLED (not the first-contact NEW), unless the
        # caller already chose a specific status. This makes the brain treat them
        # as a returning candidate ("you've spoken before") AND apply the
        # lifecycle talk-track from the funnel stage. Raw/lead keep their status.
        if (
            lead.funnel_stage in FunnelStage.APPLICATION_STAGES
            and lead.status == LeadStatus.NEW
        ):
            lead.status = LeadStatus.CALLED
        async with session_scope() as db:
            db.add(lead)
        return lead

    async def find_or_create_by_phone(
        self, phone_e164: str, *, source: str = "inbound"
    ) -> Lead:
        existing = await self.find_lead_by_phone(phone_e164)
        if existing is not None:
            return existing
        lead = Lead(
            lead_id=f"inb-{uuid.uuid4().hex[:6]}",
            full_name="Unknown",
            phone_e164=_norm_phone(phone_e164),
            source=source,
            status=LeadStatus.NEW,
        )
        return await self.create_lead(lead)

    # ======================================================================
    # Dialer support
    # ======================================================================
    async def claim_due_for_call(self, *, limit: int, max_attempts: int) -> list[Lead]:
        """Atomically claim up to `limit` leads ready to dial, flipping each to
        IN_CALL so a concurrent poll can't re-claim them. Returns the claimed
        leads (with phone + lead_id).

        Due-ness: a NULL `next_action_at` means "dial ASAP" ONLY for a lead that
        has never been dialed (`last_dialed_at` is NULL) — a fresh NEW lead. Once
        a lead HAS been dialed, a concrete future `next_action_at` is required;
        a NULL there is NOT treated as due. This stops the instant re-dial loop
        where the analyzer flips a just-called lead to a dialable status
        (followup/scheduled) before any `schedule_followup` action has set a
        time — leaving next_action_at NULL, which previously meant "redial now,
        top priority"."""
        now = _now()
        claimed: list[Lead] = []
        async with session_scope() as db:
            stmt = (
                select(Lead)
                .where(
                    Lead.status.in_(tuple(LeadStatus.DIALABLE)),
                    Lead.is_lead == True,  # noqa: E712  raw data is inbound-only — never cold-called
                    Lead.consent_call == True,  # noqa: E712
                    Lead.call_attempts < max_attempts,
                    Lead.phone_e164 != "",
                    or_(
                        # Fresh lead, never dialed → dial ASAP.
                        and_(Lead.last_dialed_at.is_(None), Lead.next_action_at.is_(None)),
                        # Already dialed (or explicitly scheduled) → only when due.
                        Lead.next_action_at <= now,
                    ),
                )
                .order_by(Lead.next_action_at.is_(None).desc(), Lead.updated_at.asc())
                .limit(limit)
            )
            res = await db.execute(stmt)
            for lead in res.scalars().all():
                lead.status = LeadStatus.IN_CALL
                lead.call_attempts += 1
                lead.last_dialed_at = now
                lead.updated_at = now
                db.add(lead)
                claimed.append(lead)
        return claimed

    async def reap_stuck_in_call_leads(self, *, older_than_minutes: int) -> int:
        """Leads left IN_CALL long past a plausible call duration (dial accepted
        but the call never connected / no session ever closed) get reverted to
        FOLLOWUP with a short backoff so the dialer can retry. Prevents a lead
        from being stranded mid-funnel forever."""
        cutoff = _now() - timedelta(minutes=older_than_minutes)
        reaped = 0
        async with session_scope() as db:
            stmt = select(Lead).where(
                Lead.status == LeadStatus.IN_CALL,
                or_(Lead.last_dialed_at.is_(None), Lead.last_dialed_at < cutoff),
            )
            res = await db.execute(stmt)
            for lead in res.scalars().all():
                lead.status = LeadStatus.FOLLOWUP
                lead.next_action_at = _now() + timedelta(minutes=5)
                lead.updated_at = _now()
                db.add(lead)
                reaped += 1
        return reaped

    async def mark_dial_result(
        self, lead_id: str, *, success: bool, backoff_minutes: int
    ) -> None:
        """Record a dial outcome. Success leaves the lead IN_CALL (the call is
        live; the analyzer will set the post-call status). Failure schedules a
        retry via FOLLOWUP + next_action_at backoff."""
        async with session_scope() as db:
            lead = await db.get(Lead, lead_id)
            if lead is None:
                return
            if not success:
                lead.status = LeadStatus.FOLLOWUP
                lead.next_action_at = _now() + timedelta(minutes=backoff_minutes)
                lead.updated_at = _now()
                db.add(lead)

    # ======================================================================
    # Sessions
    # ======================================================================
    async def open_session(
        self,
        *,
        session_id: str,
        lead_id: str,
        channel: str,
        direction: str = "outbound",
        provider_call_id: str | None = None,
        contact_phone: str | None = None,
    ) -> Session:
        """Idempotent open — re-opening an existing session_id updates its
        metadata rather than erroring (handles reconnects / retries)."""
        async with session_scope() as db:
            sess = await db.get(Session, session_id)
            if sess is None:
                sess = Session(
                    session_id=session_id,
                    lead_id=lead_id,
                    channel=channel,
                    direction=direction,
                    provider_call_id=provider_call_id,
                    contact_phone=contact_phone,
                    status=SessionStatus.ACTIVE,
                )
            else:
                # Returning to an ENDED session = a NEW episode (common on
                # WhatsApp, which has no hangup). If the prior episode was
                # already analyzed, start its transcript fresh so we only
                # analyze the new turns; if it ended but wasn't analyzed yet,
                # keep the transcript so nothing is lost.
                if sess.status == SessionStatus.ENDED:
                    if sess.analyzed:
                        sess.transcript = []
                        sess.analysis = None
                    sess.analyzed = False
                    sess.ended_at = None
                    sess.end_reason = None
                    sess.status = SessionStatus.ACTIVE
                    sess.started_at = _now()
                sess.lead_id = lead_id or sess.lead_id
                sess.channel = channel or sess.channel
                sess.direction = direction or sess.direction
                if provider_call_id:
                    sess.provider_call_id = provider_call_id
                if contact_phone:
                    sess.contact_phone = contact_phone
                sess.updated_at = _now()
            db.add(sess)

            # Durable 24h WhatsApp window: every inbound WhatsApp message
            # opens/refreshes the candidate's free-form window. Stamp it on the
            # lead so AegisBackend's send path survives a restart (the in-memory
            # window cache there is process-local). Best-effort — never blocks
            # the session open.
            if channel == "whatsapp" and direction == "inbound" and lead_id:
                lead = await db.get(Lead, lead_id)
                if lead is not None:
                    lead.last_whatsapp_inbound_at = _now()
                    lead.updated_at = _now()
                    db.add(lead)

            # A VOICE session opening means the candidate ANSWERED. Reset the
            # dial-attempt counter so `dialer_call_max_attempts` budgets
            # CONSECUTIVE unreached attempts, not lifetime dials — otherwise a
            # lead could never be re-dialed for a scheduled followup once their
            # total call count crossed the cap.
            if channel == "voice" and lead_id:
                lead = await db.get(Lead, lead_id)
                if lead is not None and lead.call_attempts:
                    lead.call_attempts = 0
                    lead.updated_at = _now()
                    db.add(lead)
        return sess

    async def count_in_call_leads(self) -> int:
        """Leads currently IN_CALL — i.e. live (or just-dialed) calls. The
        dialer uses this to gate new claims so MAX_PARALLEL_CALLS bounds LIVE
        calls, not just simultaneous dial requests. Stale IN_CALL rows are
        cleared by reap_stuck_in_call_leads / the missed-call followup, so this
        can't wedge the dialer forever."""
        async with session_scope() as db:
            res = await db.execute(select(Lead).where(Lead.status == LeadStatus.IN_CALL))
            return len(res.scalars().all())

    async def count_exhausted_due_leads(self, *, max_attempts: int) -> int:
        """Leads DUE for a call but out of dial-attempt budget. They are never
        claimed (the dialer filter skips them), so without this count they'd sit
        invisible forever — the dialer loop logs it so an operator can raise the
        budget, reset attempts, or close the lead."""
        now = _now()
        async with session_scope() as db:
            stmt = select(Lead).where(
                Lead.status.in_(tuple(LeadStatus.DIALABLE)),
                Lead.is_lead == True,  # noqa: E712  match the claim query — raw data isn't counted
                Lead.consent_call == True,  # noqa: E712
                Lead.call_attempts >= max_attempts,
                Lead.phone_e164 != "",
                or_(Lead.next_action_at.is_(None), Lead.next_action_at <= now),
            )
            res = await db.execute(stmt)
            return len(res.scalars().all())

    async def append_turn(
        self, *, session_id: str, role: str, text: str, ts: float | None = None
    ) -> bool:
        """Append one turn to a session's transcript. Best-effort: returns False
        if the session doesn't exist (caller decides whether to care)."""
        text = (text or "").strip()
        if not text:
            return False
        async with session_scope() as db:
            sess = await db.get(Session, session_id)
            if sess is None:
                return False
            turns = list(sess.transcript or [])
            turns.append({"role": role, "text": text, "ts": ts if ts is not None else _ts()})
            sess.transcript = turns
            sess.updated_at = _now()
            db.add(sess)
        return True

    async def close_session(
        self,
        *,
        session_id: str,
        end_reason: str | None = None,
        transcript: list[dict[str, Any]] | None = None,
        lead_id: str | None = None,
        channel: str | None = None,
        direction: str | None = None,
    ) -> Session | None:
        """Mark a session ended and ready for analysis. If the session was never
        opened (e.g. AegisBackend only calls us on close), create it now from the
        provided metadata + transcript so nothing is lost."""
        async with session_scope() as db:
            sess = await db.get(Session, session_id)
            if sess is None:
                if not lead_id:
                    log.warning("close_session for unknown session and no lead_id", session_id=session_id)
                    return None
                sess = Session(
                    session_id=session_id,
                    lead_id=lead_id,
                    channel=channel or "voice",
                    direction=direction or "outbound",
                )
            if transcript is not None:
                sess.transcript = transcript
            sess.status = SessionStatus.ENDED
            sess.end_reason = end_reason
            sess.ended_at = _now()
            sess.updated_at = _now()
            # Empty transcript → nothing to analyze; mark analyzed to skip it.
            if not (sess.transcript or []):
                sess.analyzed = True
            db.add(sess)
        return sess

    async def get_session(self, session_id: str) -> Session | None:
        async with session_scope() as db:
            return await db.get(Session, session_id)

    async def list_sessions_for_lead(self, lead_id: str, *, limit: int = 50) -> list[Session]:
        """All sessions for a lead, most-recent first — for the candidate detail
        view (each carries its transcript + per-session analysis)."""
        async with session_scope() as db:
            stmt = (
                select(Session)
                .where(Session.lead_id == lead_id)
                .order_by(Session.started_at.desc())
                .limit(limit)
            )
            res = await db.execute(stmt)
            return list(res.scalars().all())

    async def claim_unanalyzed_sessions(self, *, limit: int) -> list[Session]:
        async with session_scope() as db:
            stmt = (
                select(Session)
                .where(
                    Session.status == SessionStatus.ENDED,
                    Session.analyzed == False,  # noqa: E712
                )
                .order_by(Session.ended_at.asc())
                .limit(limit)
            )
            res = await db.execute(stmt)
            return list(res.scalars().all())

    async def reap_stale_active_sessions(
        self, *, older_than_minutes: int, voice_older_than_minutes: int | None = None
    ) -> int:
        """Close ACTIVE sessions that have gone IDLE (no turn / activity), then
        queue them for analysis. This is how a WhatsApp/chat thread (which has no
        hangup) gets "closed": once it's been quiet long enough, we end it.

        CHANNEL-AWARE cutoffs — this matters:
          - TEXT (whatsapp/chat) refresh `updated_at` on EVERY turn, so a short
            `older_than_minutes` window cleanly ends a finished thread without
            ever cutting a live one.
          - VOICE does NOT refresh `updated_at` mid-call (the transcript is
            flushed once at close), so it uses a SEPARATE, longer backstop
            (`voice_older_than_minutes`) that only catches a CRASHED call. Using
            the short text window for voice would reap an in-progress call mid-
            conversation and lose its post-call analysis. Defaults to
            `older_than_minutes` when not supplied (back-compat)."""
        voice_minutes = (
            older_than_minutes if voice_older_than_minutes is None else voice_older_than_minutes
        )
        text_cutoff = _now() - timedelta(minutes=older_than_minutes)
        voice_cutoff = _now() - timedelta(minutes=voice_minutes)
        reaped = 0
        async with session_scope() as db:
            stmt = select(Session).where(
                Session.status == SessionStatus.ACTIVE,
                or_(
                    and_(Session.channel == "voice", Session.updated_at < voice_cutoff),
                    and_(Session.channel != "voice", Session.updated_at < text_cutoff),
                ),
            )
            res = await db.execute(stmt)
            for sess in res.scalars().all():
                sess.status = SessionStatus.ENDED
                sess.end_reason = "reaped_stale"
                sess.ended_at = _now()
                sess.updated_at = _now()
                if not (sess.transcript or []):
                    sess.analyzed = True
                db.add(sess)
                reaped += 1
        return reaped

    # ======================================================================
    # The fold — apply one session's analysis to the lead (idempotent)
    # ======================================================================
    async def apply_session_analysis(
        self, *, session_id: str, analysis: dict[str, Any]
    ) -> Lead | None:
        """Write the session's analysis snapshot, then merge it into the lead.

        Guarded by a per-lead lock so two channels finishing together can't
        interleave. Recency-guarded by `last_analyzed_session_ended_at` so a
        late, older session can't regress newer state. Re-running is safe.
        """
        sess = await self.get_session(session_id)
        if sess is None:
            log.warning("apply_session_analysis: unknown session", session_id=session_id)
            return None

        lock = await self._lead_lock(sess.lead_id)
        async with lock:
            async with session_scope() as db:
                db_sess = await db.get(Session, session_id)
                lead = await db.get(Lead, sess.lead_id)
                if db_sess is None:
                    return None

                # 1. Persist this session's immutable snapshot.
                session_summary = (
                    analysis.get("session_summary") or analysis.get("summary")
                )
                db_sess.analysis = {
                    "summary": session_summary,
                    "interest": clamp_score(analysis.get("interest")),
                    "confidence": clamp_score(analysis.get("confidence")),
                    "sentiment": analysis.get("sentiment"),
                    "status": analysis.get("status"),
                    "next_best_action": analysis.get("next_best_action"),
                    "actions": analysis.get("actions") or [],
                }
                db_sess.analyzed = True
                db_sess.updated_at = _now()
                db.add(db_sess)

                # 1b. Unknown inbound: no lead row yet, but we captured the
                # caller's phone on the session → create (or find-by-phone) a
                # DURABLE lead now, so the next contact is recognised. Only fires
                # when there's no lead — registered sessions skip this entirely.
                if lead is None and db_sess.contact_phone:
                    norm = _norm_phone(db_sess.contact_phone)
                    digits = norm.lstrip("+")
                    res = await db.execute(
                        select(Lead).where(Lead.phone_e164.in_([norm, digits]))
                    )
                    lead = res.scalars().first()
                    if lead is None:
                        nm = (analysis.get("full_name") or "").strip() or "Unknown"
                        lead = Lead(
                            lead_id=db_sess.lead_id,
                            full_name=nm,
                            phone_e164=norm,
                            source=f"inbound_{db_sess.channel or 'unknown'}",
                            consent_call=True,        # inbound → implied consent
                            consent_whatsapp=True,
                            status="new",
                            # Explicit so the column is never NULL even on this raw
                            # db.add path (which bypasses create_lead's defaulting).
                            # An inbound caller who reached us IS a real lead.
                            funnel_stage=FunnelStage.LEAD,
                            is_lead=True,
                        )
                        db.add(lead)
                        log.info("auto-created lead for unknown inbound",
                                 lead_id=lead.lead_id, channel=db_sess.channel)
                    elif db_sess.lead_id != lead.lead_id:
                        # De-dup: an earlier session already created this lead →
                        # attribute this session to it.
                        db_sess.lead_id = lead.lead_id
                        db.add(db_sess)

                # 2. Fold into the lead (if we have one).
                if lead is not None:
                    # Upgrade a blank/"Unknown" name once the candidate states it;
                    # NEVER overwrite an existing real name (registered leads safe).
                    extracted_name = (analysis.get("full_name") or "").strip()
                    if extracted_name and (not lead.full_name or lead.full_name == "Unknown"):
                        lead.full_name = extracted_name

                    ended_at = db_sess.ended_at or _now()
                    is_newer = (
                        lead.last_analyzed_session_ended_at is None
                        or ended_at >= lead.last_analyzed_session_ended_at
                    )

                    # Facts always merge (additive; last-non-empty wins).
                    lead.facts = merge_facts(lead.facts, analysis.get("facts"))

                    # Promote a few high-value facts onto their dedicated Lead
                    # columns so they're first-class (filterable, shown in the
                    # CRM) and survive even if facts is later restructured. Only
                    # FILL a blank — never overwrite a value already on the lead
                    # (e.g. an email captured at import). This is what closes the
                    # "capture the email for an unknown inbound lead" loop: the
                    # candidate states it on the call → analyzer puts it in facts
                    # → it lands on Lead.email here, ready for the next contact.
                    _facts = analysis.get("facts") or {}
                    _email = str(_facts.get("email") or "").strip()
                    if _email and not (lead.email or "").strip():
                        lead.email = _email
                    _course = str(_facts.get("course_interest") or "").strip()
                    if _course and not (lead.course_interest or "").strip():
                        lead.course_interest = _course

                    lead.open_concerns = merge_open_concerns(
                        lead.open_concerns,
                        analysis.get("open_concerns"),
                        resolved=analysis.get("resolved_concerns"),
                    )

                    # Recency-weighted fields update only from the newest session.
                    if is_newer:
                        lead.interest = clamp_score(
                            analysis.get("interest"), default=lead.interest
                        )
                        lead.confidence = clamp_score(
                            analysis.get("confidence"), default=lead.confidence
                        )
                        # Lead TEMPERATURE — derived from the (just-updated) interest
                        # score on its own axis (hot/warm/cold). Cosmetic + drives
                        # hot-lead escalation; does NOT affect status/dialing. Keep
                        # an existing tier if this read produced no usable score.
                        _tier = LeadPriority.from_interest(lead.interest)
                        if _tier:
                            lead.lead_priority = _tier
                        # Cumulative summary: the analyzer rolled prior history +
                        # this session into one comprehensive narrative. Fall back
                        # to the session summary only if no cumulative was produced.
                        cumulative = (
                            analysis.get("cumulative_summary")
                            or analysis.get("summary")
                            or session_summary
                        )
                        if cumulative:
                            lead.summary = str(cumulative)
                        lead.status = fold_status(lead.status, analysis.get("status"))

                        # Admissions LIFECYCLE — fold the analyzer's read of the
                        # application stage into funnel_stage, FORWARD-ONLY (never
                        # regress a paid/submitted lead on a later nurture call).
                        proposed_funnel = (analysis.get("funnel_stage") or "").strip().lower()
                        if (
                            proposed_funnel in FunnelStage.APPLICATION_STAGES
                            and _FUNNEL_RANK.get(proposed_funnel, 0)
                            > _FUNNEL_RANK.get(lead.funnel_stage, 0)
                        ):
                            lead.funnel_stage = proposed_funnel
                            lead.is_lead = True
                            log.info(
                                "lead funnel stage advanced (analyzer)",
                                lead_id=lead.lead_id, funnel_stage=lead.funnel_stage,
                            )

                        # RAW DATA → LEAD promotion. A raw contact
                        # (funnel_stage=raw, is_lead=False) that shows genuine
                        # interest on an inbound call/chat is promoted to a real
                        # LEAD: enough measured interest, a human looped in
                        # (delegated), or an application stage reached (handled
                        # above → already non-raw). Below that bar (not_interested /
                        # near-zero interest) it stays raw — inbound-only, never
                        # cold-called. One-way; a real lead never reverts to raw.
                        if (
                            lead.funnel_stage == FunnelStage.RAW
                            and lead.status not in LeadStatus.TERMINAL
                            and (
                                lead.interest >= _PROMOTE_MIN_INTEREST
                                or lead.status == LeadStatus.DELEGATED
                            )
                        ):
                            lead.funnel_stage = FunnelStage.LEAD
                            lead.is_lead = True
                            log.info(
                                "raw data promoted to lead",
                                lead_id=lead.lead_id, status=lead.status,
                                interest=lead.interest, priority=lead.lead_priority,
                            )

                        lead.last_analyzed_session_ended_at = ended_at

                        # Any lead the analyzer leaves in a DIALABLE status MUST
                        # carry a concrete next_action_at, otherwise the dialer's
                        # "never dialed + no next_action → dial ASAP" fast path
                        # fires. That path is meant ONLY for fresh uploaded leads
                        # (status NEW, never analyzed). A just-analyzed lead — even
                        # an INBOUND one that was never dialed (last_dialed_at is
                        # None) — would otherwise get cold-called right back. So we
                        # set a next-day floor here regardless of last_dialed_at; a
                        # schedule_followup action (if any) refines it moments later
                        # via the actions worker.
                        from business.domain.lifecycle import is_dialable

                        if is_dialable(lead.status) and lead.next_action_at is None:
                            lead.next_action_at = _now() + timedelta(
                                minutes=_DEFAULT_FOLLOWUP_MINUTES
                            )

                    lead.version += 1
                    lead.updated_at = _now()
                    db.add(lead)

            resolved_id = lead.lead_id if lead is not None else sess.lead_id
            return await self.get_lead(resolved_id)

    async def record_delivery(
        self, *, lead_id: str, item: str, channel: str
    ) -> None:
        """Record that something was actually delivered to the candidate (e.g.
        'fee + scholarship details' sent on WhatsApp). Appended to the lead's
        `sent_items` so the next conversation follows up on it instead of
        re-offering — this is what stops the "I'll send it… I'll send it…" loop.
        De-duped on (item, channel): re-sending the same thing refreshes the
        timestamp rather than piling up duplicates."""
        item = (item or "").strip()
        if not item:
            return
        lock = await self._lead_lock(lead_id)
        async with lock:
            async with session_scope() as db:
                lead = await db.get(Lead, lead_id)
                if lead is None:
                    return
                items = [
                    x for x in (lead.sent_items or [])
                    if not (
                        str(x.get("item", "")).lower() == item.lower()
                        and x.get("channel") == channel
                    )
                ]
                items.append(
                    {"item": item, "channel": channel, "at": _now().isoformat(timespec="seconds")}
                )
                lead.sent_items = items[-20:]  # bound the log
                lead.version += 1
                lead.updated_at = _now()
                db.add(lead)
        log.info("delivery recorded", lead_id=lead_id, item=item, channel=channel)

    # ======================================================================
    # Tasks (outbox)
    # ======================================================================
    async def enqueue_task(
        self,
        *,
        lead_id: str,
        type: str,
        payload: dict[str, Any] | None = None,
        dedupe_key: str,
        session_id: str | None = None,
        channel: str | None = None,
        max_attempts: int = 4,
        scheduled_for: datetime | None = None,
    ) -> Task | None:
        """Insert a task unless an active/completed one with the same `dedupe_key`
        already exists.

        A prior task that exhausted its retries (status FAILED) is REVIVED in
        place — reset to PENDING with fresh attempts and the new payload — so a
        later trigger can retry the same intent. The unique `dedupe_key` would
        otherwise block that intent forever once it failed. A PENDING /
        IN_PROGRESS / DONE duplicate still de-dupes (returns None): we never
        re-fire an in-flight or already-delivered action.

        Returns the task (new or revived), or None if a non-failed duplicate
        already exists.
        """
        async with session_scope() as db:
            res = await db.execute(select(Task).where(Task.dedupe_key == dedupe_key))
            existing = res.scalars().first()
            if existing is not None and existing.status != TaskStatus.FAILED:
                return None
            if existing is not None:
                # Revive the failed task in place (unique dedupe_key prevents a
                # second row) with the latest payload/metadata.
                existing.status = TaskStatus.PENDING
                existing.attempts = 0
                existing.last_error = None
                existing.scheduled_for = scheduled_for
                existing.payload = payload or {}
                existing.type = type
                existing.channel = channel
                existing.session_id = session_id
                existing.max_attempts = max_attempts
                existing.updated_at = _now()
                db.add(existing)
                task = existing
            else:
                task = Task(
                    id=uuid.uuid4().hex,
                    lead_id=lead_id,
                    session_id=session_id,
                    type=type,
                    payload=payload or {},
                    channel=channel,
                    dedupe_key=dedupe_key,
                    max_attempts=max_attempts,
                    scheduled_for=scheduled_for,
                )
                db.add(task)
        return task

    async def reap_stuck_in_progress_tasks(self, *, older_than_minutes: int) -> int:
        """Revert tasks stranded IN_PROGRESS back to PENDING so the loop retries
        them. A task is claimed (committed IN_PROGRESS) BEFORE it executes, so a
        worker that dies/reloads mid-task leaves the row stuck: `claim_pending_tasks`
        only picks PENDING, and `enqueue_task`'s revive only resurrects FAILED rows
        — an IN_PROGRESS row is otherwise stranded forever (never retried, never
        re-enqueued). Keyed on `updated_at` (set at claim) so a task actively being
        worked isn't yanked out from under a live worker. Attempts are reset: the
        interrupted attempt never completed, so it shouldn't count against the budget."""
        cutoff = _now() - timedelta(minutes=older_than_minutes)
        revived = 0
        async with session_scope() as db:
            stmt = select(Task).where(
                Task.status == TaskStatus.IN_PROGRESS,
                Task.updated_at < cutoff,
            )
            res = await db.execute(stmt)
            for t in res.scalars().all():
                t.status = TaskStatus.PENDING
                t.attempts = 0
                t.scheduled_for = None
                t.updated_at = _now()
                db.add(t)
                revived += 1
        return revived

    async def claim_pending_tasks(self, *, limit: int) -> list[Task]:
        now = _now()
        async with session_scope() as db:
            stmt = (
                select(Task)
                .where(
                    Task.status == TaskStatus.PENDING,
                    or_(Task.scheduled_for.is_(None), Task.scheduled_for <= now),
                )
                .order_by(Task.created_at.asc())
                .limit(limit)
            )
            res = await db.execute(stmt)
            tasks = list(res.scalars().all())
            for t in tasks:
                t.status = TaskStatus.IN_PROGRESS
                t.attempts += 1
                t.updated_at = now
                db.add(t)
        return tasks

    async def complete_task(self, task_id: str) -> None:
        async with session_scope() as db:
            t = await db.get(Task, task_id)
            if t is None:
                return
            t.status = TaskStatus.DONE
            t.updated_at = _now()
            db.add(t)

    async def fail_task(
        self, task_id: str, *, error: str, retry_backoff_seconds: int = 120
    ) -> None:
        """Mark a task failed. If attempts remain, requeue it (PENDING) with a
        backoff; otherwise leave it FAILED for inspection."""
        async with session_scope() as db:
            t = await db.get(Task, task_id)
            if t is None:
                return
            t.last_error = (error or "")[:500]
            if t.attempts < t.max_attempts:
                t.status = TaskStatus.PENDING
                t.scheduled_for = _now() + timedelta(seconds=retry_backoff_seconds)
            else:
                t.status = TaskStatus.FAILED
            t.updated_at = _now()
            db.add(t)

    async def schedule_followup(
        self, *, lead_id: str, in_minutes: int, status: str = LeadStatus.FOLLOWUP
    ) -> None:
        async with session_scope() as db:
            lead = await db.get(Lead, lead_id)
            if lead is None:
                return
            from business.domain.lifecycle import is_terminal

            if is_terminal(lead.status):
                return
            lead.status = status
            lead.next_action_at = _now() + timedelta(minutes=in_minutes)
            lead.updated_at = _now()
            db.add(lead)

    async def mark_delegated(self, lead_id: str) -> None:
        """Hand the lead to a human counsellor: status DELEGATED, dial queue
        cleared (next_action_at=None) — the counsellor owns the next touch.
        Terminal leads are left unchanged. Idempotent."""
        lock = await self._lead_lock(lead_id)
        async with lock:
            async with session_scope() as db:
                lead = await db.get(Lead, lead_id)
                if lead is None:
                    return
                from business.domain.lifecycle import is_terminal

                if is_terminal(lead.status):
                    return
                lead.status = LeadStatus.DELEGATED
                lead.next_action_at = None
                lead.version += 1
                lead.updated_at = _now()
                db.add(lead)
        log.info("lead marked delegated", lead_id=lead_id)

    async def advance_stage(
        self, *, lead_id: str, funnel_stage: str, send_next_steps: bool | None = None
    ) -> Lead | None:
        """The SINGLE entry point for moving a lead through the admissions
        LIFECYCLE (Module F). Any source — a portal/payment webhook, a counsellor
        clicking a stage, or the AI inferring it — calls this to set the lead's
        `funnel_stage` (lead / application_started / fees_pending /
        application_submitted). is_lead is kept in sync (raw ⇒ False, else True).

        When `send_next_steps` is True (the payment is CONFIRMED), it enqueues the
        `send_admission_next_steps` task, which sends the course-appropriate
        message: entrance-exam phases + slot booking, OR the token-amount link.
        If `send_next_steps` is None, it defaults to True for the payment-done
        stages (fees_pending once paid, or application_submitted).

        Terminal leads (status) are left unchanged. Returns the updated lead."""
        funnel_stage = (funnel_stage or "").strip().lower()
        lock = await self._lead_lock(lead_id)
        async with lock:
            async with session_scope() as db:
                lead = await db.get(Lead, lead_id)
                if lead is None:
                    return None
                from business.domain.lifecycle import is_terminal
                from business.models import FunnelStage, LeadStatus, is_lead_for_stage

                if is_terminal(lead.status):
                    return lead
                lead.funnel_stage = funnel_stage
                # is_lead is derived from the funnel stage — reaching any non-raw
                # stage makes it a real lead.
                lead.is_lead = is_lead_for_stage(funnel_stage)
                # Reaching an application stage means they've been engaged → bump a
                # first-contact NEW status to CALLED so the brain treats them as
                # returning. Don't disturb a richer status (cold/warm/hot/followup).
                if (
                    funnel_stage in FunnelStage.APPLICATION_STAGES
                    and lead.status == LeadStatus.NEW
                ):
                    lead.status = LeadStatus.CALLED
                lead.version += 1
                lead.updated_at = _now()
                db.add(lead)

        if send_next_steps is None:
            send_next_steps = funnel_stage in (
                FunnelStage.FEES_PENDING,
                FunnelStage.APPLICATION_SUBMITTED,
            )
        if send_next_steps:
            # Dedupe per lead so re-advancing doesn't double-send the message.
            await self.enqueue_task(
                lead_id=lead_id,
                type="send_admission_next_steps",
                payload={},
                dedupe_key=f"{lead_id}:send_admission_next_steps",
                channel="whatsapp",
            )
            log.info("admission next-steps task enqueued", lead_id=lead_id,
                     funnel_stage=funnel_stage)
        log.info("lead stage advanced", lead_id=lead_id, funnel_stage=funnel_stage,
                 next_steps=send_next_steps)
        return await self.get_lead(lead_id)

    # ======================================================================
    # Knowledge candidates (captured from director video calls)
    # ======================================================================
    async def upsert_knowledge_candidate(self, rec: dict[str, Any]) -> KnowledgeCandidate:
        """Create or update a candidate from AegisBackend's snapshot. AegisBackend
        owns the state machine + ingest; this is the durable record + audit. Maps
        the in-memory rec (see channels/avatar_video/knowledge.py) onto columns and
        appends an audit event each write."""
        # Use truthy fallback, NOT `in`: the upsert endpoint's Pydantic model
        # declares `candidate_id` (default None), so it's always a KEY in rec with
        # value None — `"candidate_id" in rec` would pick None and null the PK.
        cid = rec.get("candidate_id") or rec.get("id")
        async with session_scope() as db:
            row = await db.get(KnowledgeCandidate, cid)
            if row is None:
                row = KnowledgeCandidate(candidate_id=cid, created_at=_now())
            row.conversation_id = rec.get("conversation_id", row.conversation_id or "")
            row.tenant_id = rec.get("tenant_id", row.tenant_id or "")
            row.lead_id = rec.get("lead_id", row.lead_id)
            row.status = rec.get("status", row.status)
            row.text = rec.get("text", row.text or "")
            row.heading = rec.get("heading", row.heading or "")
            row.topic = rec.get("topic", row.topic or "")
            row.kb = rec.get("kb", row.kb or "university")
            row.source_span = rec.get("source_span", row.source_span)
            row.trigger = rec.get("trigger", row.trigger or "explicit")
            if "confidence" in rec:
                c = float(rec["confidence"])
                row.confidence = int(round(c * 100)) if c <= 1.0 else int(round(c))
            conflict = rec.get("conflict") or {}
            if conflict:
                row.conflict_score = int(conflict.get("score", row.conflict_score))
                row.blocking = bool(conflict.get("blocking", row.blocking))
                row.conflict_items = conflict.get("items", row.conflict_items)
            pids = rec.get("ingested_point_ids") or []
            if pids:
                row.ingested = True
                row.ingested_point_id = pids[0]
            if rec.get("ingest_error"):
                row.ingest_error = rec["ingest_error"]
            if rec.get("resolved_by"):
                row.resolved_by = rec["resolved_by"]
            if rec.get("supersedes"):
                row.supersedes = rec["supersedes"]
            if rec.get("patched"):
                # Patch audit: the chunks rewritten in place on approval, with
                # their pre-edit text (the undo trail). Lives in the meta JSON.
                row.meta = {**(row.meta or {}), "patched": rec["patched"]}
            if rec.get("status") in ("approved", "superseded", "rejected", "error"):
                row.resolved_at = _now()
                row.resolution = {
                    "status": rec.get("status"),
                    "kb": rec.get("kb"),
                    "collection": rec.get("ingested_collection"),
                    "point_ids": pids,
                }
            row.version = int(rec.get("version", row.version))
            events = list(row.events or [])
            events.append({"event": rec.get("_event", "update"), "at": _now().isoformat(), "status": row.status})
            row.events = events
            row.updated_at = _now()
            db.add(row)
            await db.flush()
            await db.refresh(row)
            return row

    async def get_knowledge_candidate(self, candidate_id: str) -> KnowledgeCandidate | None:
        async with session_scope() as db:
            return await db.get(KnowledgeCandidate, candidate_id)

    async def list_knowledge_candidates(
        self, *, status: str | None = None, tenant_id: str | None = None,
        limit: int = 100, offset: int = 0,
    ) -> list[KnowledgeCandidate]:
        async with session_scope() as db:
            stmt = select(KnowledgeCandidate).order_by(
                KnowledgeCandidate.created_at.desc()
            ).offset(offset).limit(limit)
            if status:
                stmt = stmt.where(KnowledgeCandidate.status == status)
            if tenant_id:
                stmt = stmt.where(KnowledgeCandidate.tenant_id == tenant_id)
            res = await db.execute(stmt)
            return list(res.scalars().all())


def _norm_phone(p: str) -> str:
    """Canonicalise to '+<digits>' E.164 (mirrors AegisBackend's _norm_phone) so
    inbound numbers and stored numbers key consistently regardless of '+'."""
    p = (p or "").strip().replace(" ", "").replace("-", "").lstrip("+")
    return ("+" + p) if p.isdigit() else (p or "")


def _ts() -> float:
    import time

    return time.time()


# Process-wide singleton.
_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store
