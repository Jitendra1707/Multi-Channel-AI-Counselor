"""BusinessLayer client — session lifecycle push + memory fetch.

Every function here is **best-effort and flag-gated**: if `BUSINESS_LAYER_URL`
is empty the function is a no-op; on any HTTP/timeout error it logs a warning
and returns. Nothing here may ever raise into the call path or add latency to a
live conversation beyond the short `business_timeout_s`.

Wiring:
  - voice (ACS + Plivo): `open_session` at connect, `hydrate_memory` before the
    pipeline runs, `close_session` (flushes the transcript) in the WS finally.
  - whatsapp: `hydrate_memory` + `open_session` before the brain runs, then
    `append_turn` for each user/bot turn (the BusinessLayer reaper analyzes the
    thread after it goes idle).
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from agent_backend.config import get_settings
from agent_backend.infra import get_logger
from agent_backend.llm_agent.session import Session

log = get_logger(__name__)

_client: Any = None


def _enabled() -> bool:
    return bool(get_settings().business_layer_url)


async def _get_client() -> Any | None:
    """Lazily build a shared httpx.AsyncClient. Returns None when disabled or if
    httpx isn't importable (degrade silently)."""
    global _client
    if not _enabled():
        return None
    if _client is not None:
        return _client
    try:
        import httpx
    except ModuleNotFoundError:
        log.warning("[business] httpx not installed — integration disabled")
        return None
    s = get_settings()
    _client = httpx.AsyncClient(
        base_url=s.business_layer_url.rstrip("/"), timeout=s.business_timeout_s
    )
    log.info("[business] integration enabled", url=s.business_layer_url)
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:  # noqa: BLE001
            pass
        _client = None


async def _post(path: str, json: dict) -> dict | None:
    client = await _get_client()
    if client is None:
        return None
    try:
        resp = await client.post(path, json=json)
        if resp.status_code >= 300:
            log.debug("[business] non-2xx", path=path, status=resp.status_code)
            return None
        return resp.json()
    except Exception as e:  # noqa: BLE001
        log.debug("[business] POST failed", path=path, err=str(e)[:160])
        return None


async def _get(path: str) -> dict | None:
    client = await _get_client()
    if client is None:
        return None
    try:
        resp = await client.get(path)
        if resp.status_code >= 300:
            return None
        return resp.json()
    except Exception as e:  # noqa: BLE001
        log.debug("[business] GET failed", path=path, err=str(e)[:160])
        return None


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------
async def open_session(
    session: Session,
    *,
    direction: str,
    provider_call_id: str | None = None,
    contact_phone: str | None = None,
) -> None:
    if not _enabled() or not session.lead_id:
        return
    await _post(
        "/sessions",
        {
            "session_id": session.conversation_id,
            "lead_id": session.lead_id,
            "channel": session.channel,
            "direction": direction,
            "provider_call_id": provider_call_id,
            # Caller phone so the analyzer can persist an unknown inbound lead.
            "contact_phone": contact_phone,
        },
    )


async def append_turn(session: Session, *, role: str, text: str) -> None:
    if not _enabled() or not session.lead_id or not (text or "").strip():
        return
    await _post(
        f"/sessions/{session.conversation_id}/turns",
        {"role": role, "text": text},
    )


async def find_lead_by_phone(phone: str) -> dict | None:
    """Look the caller up by phone in the BusinessLayer (the system of record),
    used on an INBOUND call to identify them before answering. Returns the lead
    view (lead_id, full_name, language_preference, ...) or None if unknown / the
    integration is off. Phone is sent digits-only (no '+') to keep it path-safe;
    the BusinessLayer matches either form."""
    if not _enabled() or not (phone or "").strip():
        return None
    digits = phone.strip().lstrip("+").replace(" ", "").replace("-", "")
    return await _get(f"/leads/by-phone/{digits}")


async def ensure_lead_by_phone(phone: str, *, source: str) -> Any:
    """Resolve an inbound contact by phone for a TEXT channel (WhatsApp), then
    return a usable in-memory Lead with memory hydrated:

      1. Look the candidate up by phone in the BusinessLayer (system of record).
         If found, ensure an in-memory Lead exists with the real lead_id + name
         (so the prompt's LEAD PROFILE renders and memory hydrates by lead_id).
      2. Otherwise (or if BusinessLayer is off/unreachable) fall back to a local
         in-memory lead — a genuinely unknown caller.

    Then overlay facts/summary via `hydrate_memory`. This replaces the old
    leads.json-only lookup so candidates are recognised even after leads.json is
    removed. Always returns a Lead.
    """
    from agent_backend.data.leads import Lead, LeadRepo

    phone_e164 = phone if phone.startswith("+") else f"+{phone}"
    repo = LeadRepo.get()

    biz = await find_lead_by_phone(phone_e164)
    if biz and biz.get("lead_id"):
        lead = repo.get_by_id(str(biz["lead_id"]))
        if lead is None:
            lead = Lead(
                lead_id=str(biz["lead_id"]),
                full_name=biz.get("full_name") or "Unknown",
                phone_e164=phone_e164,
                language_preference=biz.get("language_preference") or "en",
                source=source,
            )
            repo.upsert(lead)  # in-memory only (_persist is a no-op)
    else:
        lead = repo.find_or_create_by_phone(phone_e164, source=source)

    await hydrate_memory(lead)  # overlay facts/summary/open_concerns/sent_items
    return lead


async def schedule_followup(*, lead_id: str, in_minutes: int) -> None:
    """Put the lead back in the BusinessLayer dial queue after `in_minutes`
    (status FOLLOWUP + next_action_at). Used by the missed-call handler so an
    unanswered outbound call is retried the next day. Best-effort."""
    if not _enabled() or not lead_id:
        return
    await _post(f"/leads/{lead_id}/schedule-followup", {"in_minutes": int(in_minutes)})


async def record_delivery(*, lead_id: str | None, item: str, channel: str) -> None:
    """Tell the BusinessLayer something was delivered to the candidate (e.g. a
    document sent live on WhatsApp). Keeps `sent_items` consistent across the
    live-tool path (Scenario 2) and the post-call action-worker path (Scenario
    1), so the next conversation follows up instead of re-offering. Best-effort."""
    if not _enabled() or not lead_id or not (item or "").strip():
        return
    await _post(f"/leads/{lead_id}/deliveries", {"item": item, "channel": channel})


async def close_session(session: Session, *, end_reason: str | None = None) -> None:
    """Flush the conversation transcript (from the in-RAM ConversationStore) and
    mark the session ended → the BusinessLayer queues it for post-call analysis."""
    if not _enabled() or not session.lead_id:
        return
    transcript = _transcript(session.conversation_id)
    await _post(
        f"/sessions/{session.conversation_id}/close",
        {
            "end_reason": end_reason,
            "transcript": transcript,
            "lead_id": session.lead_id,
            "channel": session.channel,
        },
    )


# ---------------------------------------------------------------------------
# Knowledge capture — durable record of a fact captured in a director call.
# AgentBackend owns the capture/contradiction/ingest logic; the BusinessLayer is
# the system-of-record + audit trail + the post-call /knowledge-review queue.
# Best-effort + flag-gated like everything else here.
# ---------------------------------------------------------------------------
async def persist_knowledge_candidate(rec: dict, event: str) -> None:
    """Upsert a candidate snapshot to the BusinessLayer. `rec` is the in-memory
    record from channels/avatar_video/knowledge.py; `event` labels the audit
    timeline entry (create | edit | resolve). No-op when the integration is off."""
    if not _enabled():
        return
    payload = dict(rec)
    payload["_event"] = event
    await _post("/knowledge-candidates", payload)


async def get_knowledge_candidate(candidate_id: str) -> dict | None:
    """Load a persisted candidate (the post-call review path operates on these
    after the live session — and its in-memory record — are gone)."""
    if not _enabled() or not candidate_id:
        return None
    return await _get(f"/knowledge-candidates/{candidate_id}")


async def list_knowledge_candidates(
    *, status: str | None = None, tenant_id: str | None = None,
) -> list[dict]:
    """The review queue (defaults to pending). Returns [] when the integration is
    off or the call fails — the screen just shows an empty queue."""
    if not _enabled():
        return []
    q = []
    if status:
        q.append(f"status={status}")
    if tenant_id:
        q.append(f"tenant_id={tenant_id}")
    path = "/knowledge-candidates" + ("?" + "&".join(q) if q else "")
    res = await _get(path)
    return (res or {}).get("items", []) if isinstance(res, dict) else []


# ---------------------------------------------------------------------------
# Memory — overlay BusinessLayer's view onto the in-memory Lead so the EXISTING
# LEAD PROFILE prompt slot renders it. We DON'T persist back to leads.json — the
# BusinessLayer is the source of truth for these derived fields.
# ---------------------------------------------------------------------------
async def hydrate_memory(lead: Any) -> None:
    if not _enabled() or lead is None or not getattr(lead, "lead_id", None):
        return
    mem = await _get(f"/leads/{lead.lead_id}/memory")
    if not mem:
        return
    try:
        # Name from the BusinessLayer is authoritative (e.g. the analyzer learned
        # it on a prior call). Overlay it so a returning candidate is recognised
        # by name — but only when it's a REAL name, never downgrade to "Unknown".
        mem_name = (mem.get("full_name") or "").strip()
        if mem_name and mem_name.lower() != "unknown":
            lead.full_name = mem_name
        # Status + funnel_stage from the system of record are authoritative — the
        # in-memory lead defaults to status NEW / funnel_stage "lead", which makes
        # the brain open like a first-contact even for a candidate who has already
        # paid (funnel_stage=fees_pending). Overlay both so the LEAD PROFILE +
        # opening hint reflect WHERE the candidate actually is in the journey.
        mem_status = (mem.get("status") or "").strip()
        if mem_status:
            try:
                from agent_backend.data.leads import LeadStatus

                lead.status = LeadStatus(mem_status)
            except ValueError:
                pass  # unknown status string — keep the existing one
        mem_stage = (mem.get("funnel_stage") or "").strip()
        if mem_stage:
            # funnel_stage is a plain str on the in-memory Lead (mirrors the DB).
            lead.funnel_stage = mem_stage
        # Lead temperature (hot/warm/cold) — authoritative from the analyzer.
        # Overlaid for the LEAD PROFILE display; blank until first analyzed.
        mem_priority = (mem.get("lead_priority") or "").strip()
        if mem_priority:
            lead.lead_priority = mem_priority
        if mem.get("summary"):
            lead.last_session_summary = mem["summary"]
        if isinstance(mem.get("open_concerns"), list) and mem["open_concerns"]:
            lead.open_concerns = list(mem["open_concerns"])
        if isinstance(mem.get("facts"), dict) and mem["facts"]:
            lead.facts = dict(mem["facts"])
        if isinstance(mem.get("sent_items"), list) and mem["sent_items"]:
            lead.sent_items = list(mem["sent_items"])
        log.info(
            "[business] memory hydrated",
            lead_id=lead.lead_id,
            status=mem.get("status"),
            funnel_stage=mem.get("funnel_stage"),
            lead_priority=mem.get("lead_priority"),
            facts=list((mem.get("facts") or {}).keys()),
            has_summary=bool(mem.get("summary")),
            sent_items=len(mem.get("sent_items") or []),
        )
    except Exception as e:  # noqa: BLE001
        log.debug("[business] memory overlay failed", err=str(e)[:160])


async def setup_session(
    session: Session,
    *,
    lead: Any | None,
    direction: str,
    provider_call_id: str | None = None,
) -> None:
    """Hydrate memory + open the session — meant to run OFF the call's hot path.

    The voice media handlers fire this as a background task so neither HTTP
    round-trip delays the opener (each call can wait up to `business_timeout_s`).
    `hydrate_memory` mutates the process-cached `Lead` in place, so the overlay
    is visible when the first turn builds its prompt (seconds later, after the
    opener plays). Fully self-contained: never raises into the caller."""
    if not _enabled():
        return
    try:
        if lead is not None:
            await hydrate_memory(lead)
        await open_session(
            session,
            direction=direction,
            provider_call_id=provider_call_id,
            contact_phone=getattr(lead, "phone_e164", None),
        )
    except Exception as e:  # noqa: BLE001
        log.debug("[business] setup_session failed (continuing)", err=str(e)[:160])


# ---------------------------------------------------------------------------
def _transcript(conversation_id: str) -> list[dict]:
    """Convert the in-RAM conversation buffer into [{role, text}] turns."""
    try:
        from agent_backend.llm_agent.conversation import get_conversation

        msgs = get_conversation(conversation_id).recent(n=500)
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    for m in msgs:
        content = m.content if isinstance(m.content, str) else str(m.content)
        if not content.strip():
            continue
        if isinstance(m, HumanMessage):
            role = "user"
        elif isinstance(m, AIMessage):
            role = "bot"
        else:
            role = "system"
        out.append({"role": role, "text": content})
    return out
