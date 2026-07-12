"""In-call knowledge capture orchestration (Phase 2).

Glue between the live avatar session and the pure `rag.knowledge_capture` core:
keeps a per-conversation pending-candidate store + a data-channel sink, and runs
the capture flow OFF the conversation hot path (every LLM / Qdrant call goes
through `asyncio.to_thread`).

In-call flow is ARM-FIRST: the director clicks Capture (runner routes
`arm_knowledge_capture` → `arm()`), then SPEAKS the fact — `agent_bridge` calls
`take_armed()` on the next finalized utterance and, when armed, diverts it here
(`capture_armed`) instead of the brain, speaking a status acknowledgment. The
in-call card is DISPLAY-ONLY; all actions (approve / supersede / keep_both /
reject / edit) happen on the Knowledge Review screen via the REST surface
(`knowledge_routes.py` → the by-id functions below).

Lifecycle of a candidate (id "kc_<uuid>"):
  pending → (edit → re-check → pending)* → approved | superseded | rejected
A blocking conflict (real contradiction with a still-valid fact) cannot be
one-click approved — the resolve must be 'supersede' or 'keep_both'.

Phase 3 adds durable persistence to the BusinessLayer; `_persist` is the single
seam for that and is a best-effort no-op until then.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Callable

from agent_backend.config import get_settings
from agent_backend.llm_agent.conversation import get_conversation
from agent_backend.llm_agent.session import Session

try:
    from agent_backend.infra import get_logger

    log = get_logger(__name__)
except Exception:  # noqa: BLE001
    import logging

    log = logging.getLogger("agent_backend.channels.avatar_video.knowledge")


# Per-conversation state (keyed by conversation_id, mirrors the _MUTED pattern).
_SINKS: dict[str, Callable[[dict], None]] = {}          # data-channel emitters
_CANDIDATES: dict[str, dict[str, dict[str, Any]]] = {}  # cid → {kid → record}
_ARMED: dict[str, float] = {}                           # cid → armed-at (monotonic)
_INFLIGHT: dict[str, int] = {}                          # cid → captures being processed
_ARM_TTL_S = 60.0  # an arm not followed by speech expires after this


# --- registry -------------------------------------------------------------
def register_knowledge_sink(conversation_id: str, sink: Callable[[dict], None]) -> None:
    _SINKS[conversation_id] = sink


def clear_knowledge(conversation_id: str) -> None:
    _SINKS.pop(conversation_id, None)
    _CANDIDATES.pop(conversation_id, None)
    _ARMED.pop(conversation_id, None)
    _INFLIGHT.pop(conversation_id, None)


# --- arming ----------------------------------------------------------------
def arm(conversation_id: str) -> None:
    """Director clicked Capture: the NEXT finalized utterance is the knowledge
    statement. One utterance per arm; expires after _ARM_TTL_S."""
    if not get_settings().knowledge_capture_enabled:
        return
    _ARMED[conversation_id] = time.monotonic()
    log.info("[kcapture] armed", conv=conversation_id[:12])
    _emit(conversation_id, {"type": "knowledge_capture_status", "state": "armed"})


def disarm(conversation_id: str, reason: str = "cancelled") -> None:
    """Director clicked again (or the FE timed out): cancel the pending arm."""
    if _ARMED.pop(conversation_id, None) is not None:
        log.info("[kcapture] disarmed", conv=conversation_id[:12], reason=reason)
    _emit(conversation_id, {"type": "knowledge_capture_status", "state": "disarmed", "reason": reason})


def take_armed(conversation_id: str) -> bool:
    """Atomically consume the arm for this utterance. Expired arms are dropped."""
    at = _ARMED.pop(conversation_id, None)
    if at is None:
        return False
    if (time.monotonic() - at) > _ARM_TTL_S:
        log.info("[kcapture] arm expired", conv=conversation_id[:12])
        _emit(conversation_id, {"type": "knowledge_capture_status", "state": "disarmed", "reason": "timeout"})
        return False
    return True


def has_pending(conversation_id: str) -> bool:
    """True while a capture is in progress — armed (director about to speak the
    fact) or the pipeline is processing one. The silence monitor checks this to
    PAUSE auto-hangup/re-engagement during that window. Displayed result cards
    are informational only and must NOT hold the call open."""
    at = _ARMED.get(conversation_id)
    if at is not None and (time.monotonic() - at) <= _ARM_TTL_S:
        return True
    return _INFLIGHT.get(conversation_id, 0) > 0


def _emit(conversation_id: str, envelope: dict) -> None:
    sink = _SINKS.get(conversation_id)
    if sink is None:
        return
    try:
        sink(envelope)
    except Exception as e:  # noqa: BLE001
        log.debug("[kcapture] emit failed", err=str(e)[:160])


def _history(conversation_id: str) -> list[str]:
    try:
        msgs = get_conversation(conversation_id).recent(n=10)
        return [f"{getattr(m,'type','?')}: {getattr(m,'content','')}" for m in msgs]
    except Exception:  # noqa: BLE001
        return []


def _envelope(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "knowledge_candidate",
        "id": rec["id"],
        "conversation_id": rec["conversation_id"],
        "text": rec["text"],
        "heading": rec["heading"],
        "topic": rec["topic"],
        "suggested_kb": rec["kb"],
        "confidence": rec["confidence"],
        "status": rec["status"],
        "version": rec["version"],
        "source_span": rec.get("source_span"),
        "conflict": rec["conflict"],
    }


# --- triggers --------------------------------------------------------------
async def capture_armed(conversation_id: str, session: Session, text: str) -> bool:
    """The utterance an ARMED capture consumed. Emits processing/failed status
    over the data channel; returns True when a candidate card was produced (the
    bridge speaks the failure line when False)."""
    if not get_settings().knowledge_capture_enabled:
        return False
    raw = (text or "").strip()
    if not raw:
        _emit(conversation_id, {"type": "knowledge_capture_status", "state": "failed", "reason": "no_fact"})
        return False
    _emit(conversation_id, {"type": "knowledge_capture_status", "state": "processing", "text": raw})
    _INFLIGHT[conversation_id] = _INFLIGHT.get(conversation_id, 0) + 1
    try:
        outcome = await _propose(conversation_id, session, raw, trigger="armed")
    finally:
        n = _INFLIGHT.get(conversation_id, 1) - 1
        if n <= 0:
            _INFLIGHT.pop(conversation_id, None)
        else:
            _INFLIGHT[conversation_id] = n
    if outcome != "ok":
        _emit(conversation_id, {"type": "knowledge_capture_status", "state": "failed", "reason": outcome})
        return False
    return True


def maybe_autodetect(conversation_id: str, session: Session, text: str) -> None:
    """Flag-gated automatic detection on a director utterance. Fire-and-forget —
    NEVER awaited on the transcript path, so it adds zero conversation latency.
    Disabled by default (KNOWLEDGE_AUTODETECT=0)."""
    s = get_settings()
    if not (s.knowledge_capture_enabled and s.knowledge_autodetect):
        return
    asyncio.create_task(_propose(conversation_id, session, text, trigger="auto"))


async def _propose(conversation_id: str, session: Session, raw: str, *, trigger: str) -> str:
    """Extract → conflict-check → store/emit/persist. Returns 'ok', 'no_fact',
    or 'error' so an armed capture can report the outcome."""
    from agent_backend.rag.knowledge_capture import analyze_conflict, extract_candidate

    try:
        # An armed capture is a deliberate director intent → trust it (lenient);
        # the auto detector stays strict.
        cand = await asyncio.to_thread(
            extract_candidate, raw,
            history=_history(conversation_id),
            lenient=(trigger != "auto"),
        )
        if cand is None:
            log.info("[kcapture] no durable fact in utterance", trigger=trigger)
            return "no_fact"
        conflict = await asyncio.to_thread(
            analyze_conflict, cand.text, topic=cand.topic, kb=cand.suggested_kb
        )
    except Exception as e:  # noqa: BLE001
        log.warning("[kcapture] propose failed", err=str(e)[:200])
        return "error"

    kid = f"kc_{uuid.uuid4().hex[:12]}"
    rec: dict[str, Any] = {
        "id": kid,
        "conversation_id": conversation_id,
        "text": cand.text,
        "heading": cand.heading,
        "topic": cand.topic,
        "kb": cand.suggested_kb,
        "confidence": round(cand.confidence, 3),
        "trigger": trigger,
        "source_span": raw,
        "conflict": conflict.to_dict(),
        "status": "pending",
        "version": 0,
        "ingested_point_ids": [],
    }
    _CANDIDATES.setdefault(conversation_id, {})[kid] = rec
    log.info("[kcapture] candidate proposed", id=kid, blocking=conflict.blocking, score=conflict.score)
    _emit(conversation_id, _envelope(rec))
    await _persist(rec, "create")
    return "ok"


# --- edit / resolve --------------------------------------------------------
async def edit(conversation_id: str, session: Session, kid: str, fields: dict[str, Any]) -> None:
    """Apply an edit → re-run the conflict check → reset to pending. Any edit
    invalidates the prior approval (enforced here, server-side)."""
    from agent_backend.rag.knowledge_capture import analyze_conflict

    rec = _CANDIDATES.get(conversation_id, {}).get(kid)
    if rec is None:
        return
    for f in ("text", "heading", "topic", "kb"):
        if f in fields and isinstance(fields[f], str) and fields[f].strip():
            rec[f] = fields[f].strip()
    # A revision of an already-ingested fact must not conflict with its own point.
    exclude = set(rec.get("ingested_point_ids") or [])
    try:
        conflict = await asyncio.to_thread(
            analyze_conflict, rec["text"], topic=rec["topic"], kb=rec["kb"], exclude_point_ids=exclude
        )
    except Exception as e:  # noqa: BLE001
        log.warning("[kcapture] edit re-check failed", err=str(e)[:200])
        return
    rec["conflict"] = conflict.to_dict()
    rec["status"] = "pending"
    rec["version"] = int(rec.get("version", 0)) + 1
    log.info("[kcapture] candidate edited + re-checked", id=kid, blocking=conflict.blocking)
    _emit(conversation_id, _envelope(rec))
    await _persist(rec, "edit")


async def resolve(
    conversation_id: str,
    session: Session,
    kid: str,
    *,
    action: str,
    kb: str | None = None,
    supersede_point_ids: list[str] | None = None,
) -> None:
    """Director decision. approve/keep_both/supersede → patch stale chunks +
    sweep + ingest (see rag.knowledge_capture.apply_resolution); reject → mark.
    A blocking candidate cannot be plain 'approve'd."""
    from agent_backend.rag.knowledge_capture import apply_resolution

    rec = _CANDIDATES.get(conversation_id, {}).get(kid)
    if rec is None:
        return

    if action == "reject":
        rec["status"] = "rejected"
        await _persist(rec, "resolve")
        _emit(conversation_id, {"type": "knowledge_resolved", "id": kid, "status": "rejected"})
        return

    blocking = bool(rec.get("conflict", {}).get("blocking"))
    if blocking and action not in ("supersede", "keep_both"):
        # Block-until-resolved: a real contradiction can't be 1-click approved.
        log.info("[kcapture] blocking candidate needs supersede/keep_both", id=kid)
        _emit(conversation_id, _envelope(rec))  # re-assert the blocked state
        return

    target_kb = kb or rec["kb"]
    items = rec.get("conflict", {}).get("items", [])
    if supersede_point_ids is not None:
        # Caller pinned exact points: patch those and only those.
        items = [
            {"point_id": pid, "relation": "contradicts"} for pid in supersede_point_ids
        ]

    try:
        res = await asyncio.to_thread(
            apply_resolution,
            text=rec["text"],
            heading=rec["heading"],
            topic=rec["topic"],
            kb=target_kb,
            conflict_items=items,
            action=action,
            candidate_id=kid,
            exclude_point_ids=set(rec.get("ingested_point_ids") or []),
        )
    except Exception as e:  # noqa: BLE001
        rec["ingest_error"] = str(e)[:300]
        log.warning("[kcapture] ingest failed", id=kid, err=rec["ingest_error"])
        await _persist(rec, "resolve")
        _emit(conversation_id, {"type": "knowledge_resolved", "id": kid, "status": "error", "error": rec["ingest_error"]})
        return

    rec["status"] = "superseded" if action == "supersede" else "approved"
    rec["kb"] = target_kb
    rec["ingested_point_ids"] = res["point_ids"]
    rec["ingested_collection"] = res["collection"]
    rec["patched"] = res.get("patched") or []

    # LIVE USE: the KB itself is now consistent (stale chunks patched in place,
    # new fact searchable immediately). Drop a history line as a soft signal for
    # the current conversation.
    try:
        get_conversation(conversation_id).append_bot(f"[APPROVED FACT] {rec['text']}")
    except Exception as e:  # noqa: BLE001
        log.debug("[kcapture] append_bot failed", err=str(e)[:160])

    log.info("[kcapture] candidate ingested", id=kid, status=rec["status"],
             collection=res["collection"], patched=len(rec["patched"]))
    await _persist(rec, "resolve")
    # Emitted even when the resolve came via REST: a live in-call card flips its
    # status chip (e.g. Approved ✓). `patched` = chunks rewritten in place.
    _emit(conversation_id, {
        "type": "knowledge_resolved", "id": kid, "status": rec["status"],
        "patched": len(rec["patched"]),
    })


# --- by-id helpers (post-call review path, no live session) ----------------
def _find_rec(kid: str) -> tuple[str | None, dict[str, Any] | None]:
    """Locate a candidate in the in-memory store across all conversations."""
    for cid, bucket in _CANDIDATES.items():
        if kid in bucket:
            return cid, bucket[kid]
    return None, None


async def _load_rec_from_bl(kid: str) -> tuple[str | None, dict[str, Any] | None]:
    """Reconstruct the in-memory rec shape from the BusinessLayer record so the
    post-call review screen can edit/resolve a candidate after the live session
    (and its in-memory record) are gone."""
    try:
        from agent_backend.integrations.business import get_knowledge_candidate

        v = await get_knowledge_candidate(kid)
    except Exception:  # noqa: BLE001
        v = None
    if not v:
        return None, None
    cid = v.get("conversation_id") or ""
    rec: dict[str, Any] = {
        "id": v["candidate_id"],
        "conversation_id": cid,
        "tenant_id": v.get("tenant_id", ""),
        "lead_id": v.get("lead_id"),
        "text": v.get("text", ""),
        "heading": v.get("heading", ""),
        "topic": v.get("topic", ""),
        "kb": v.get("kb", "university"),
        "confidence": (v.get("confidence", 0) or 0) / 100.0,
        "trigger": v.get("trigger", "explicit"),
        "source_span": v.get("source_span"),
        "conflict": {
            "score": v.get("conflict_score", 0),
            "blocking": bool(v.get("blocking")),
            "items": v.get("conflict_items", []),
        },
        "status": v.get("status", "pending"),
        "version": int(v.get("version", 0)),
        "ingested_point_ids": [v["ingested_point_id"]] if v.get("ingested_point_id") else [],
        "supersedes": v.get("supersedes"),
    }
    _CANDIDATES.setdefault(cid, {})[kid] = rec  # cache for the operation
    return cid, rec


async def list_candidates(*, status: str | None = "pending", tenant_id: str | None = None) -> list[dict]:
    """Review queue — read from the BusinessLayer (durable, survives the call)."""
    from agent_backend.integrations.business import list_knowledge_candidates

    return await list_knowledge_candidates(status=status, tenant_id=tenant_id)


async def edit_by_id(kid: str, fields: dict[str, Any], *, expected_version: int | None = None) -> dict[str, Any]:
    """Post-call edit → re-check → re-pending. Returns the updated envelope (or
    {'error': ...})."""
    cid, rec = _find_rec(kid)
    if rec is None:
        cid, rec = await _load_rec_from_bl(kid)
    if rec is None:
        return {"error": "not_found"}
    if expected_version is not None and int(rec.get("version", 0)) != int(expected_version):
        return {"error": "version_conflict", "current": _envelope(rec)}
    await edit(cid or rec.get("conversation_id", ""), None, kid, fields)  # type: ignore[arg-type]
    return _envelope(_CANDIDATES.get(cid or rec.get("conversation_id", ""), {}).get(kid, rec))


async def resolve_by_id(
    kid: str, *, action: str, kb: str | None = None,
    supersede_point_ids: list[str] | None = None, resolved_by: str | None = None,
    expected_version: int | None = None,
) -> dict[str, Any]:
    """Post-call resolve. Operates on the BL-loaded record; live-use append is a
    no-op when there's no live conversation."""
    cid, rec = _find_rec(kid)
    if rec is None:
        cid, rec = await _load_rec_from_bl(kid)
    if rec is None:
        return {"error": "not_found"}
    if expected_version is not None and int(rec.get("version", 0)) != int(expected_version):
        return {"error": "version_conflict", "current": _envelope(rec)}
    if resolved_by:
        rec["resolved_by"] = resolved_by
    await resolve(
        cid or rec.get("conversation_id", ""), None, kid,  # type: ignore[arg-type]
        action=action, kb=kb, supersede_point_ids=supersede_point_ids,
    )
    return {
        "id": kid, "status": rec.get("status"),
        "patched": len(rec.get("patched") or []),
        "ingest_error": rec.get("ingest_error"),
    }


# --- persistence seam (Phase 3 wires the BusinessLayer) --------------------
async def _persist(rec: dict[str, Any], event: str) -> None:
    """Best-effort durable persistence of the candidate + decision. Phase 3 wires
    this to the BusinessLayer knowledge_candidates table; until then it's a
    debug-logged no-op so the in-call loop is fully functional standalone."""
    try:
        from agent_backend.integrations.business import persist_knowledge_candidate

        await persist_knowledge_candidate(rec, event)
    except Exception as e:  # noqa: BLE001 — BL optional/not-yet-wired
        log.debug("[kcapture] persist skipped", event=event, err=str(e)[:160])
