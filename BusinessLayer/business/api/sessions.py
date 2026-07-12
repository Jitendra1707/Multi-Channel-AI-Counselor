"""Session lifecycle endpoints — AegisBackend pushes conversation events here.

  POST /sessions                      open a session (call/chat started)
  POST /sessions/{id}/turns           append one turn (optional, durable)
  POST /sessions/{id}/close           end the session → queues it for analysis
  GET  /sessions/{id}                 inspect a session
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from business.logging import get_logger
from business.schemas import Ack, CloseSessionRequest, OpenSessionRequest, TurnRequest
from business.store import get_store

log = get_logger(__name__)
router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("")
async def open_session(body: OpenSessionRequest) -> dict:
    sess = await get_store().open_session(
        session_id=body.session_id,
        lead_id=body.lead_id,
        channel=body.channel,
        direction=body.direction,
        provider_call_id=body.provider_call_id,
        contact_phone=body.contact_phone,
    )
    log.info("session opened", session_id=sess.session_id, lead_id=sess.lead_id, channel=sess.channel)
    return {"ok": True, "session_id": sess.session_id, "status": sess.status}


@router.post("/{session_id}/turns")
async def append_turn(session_id: str, body: TurnRequest) -> Ack:
    ok = await get_store().append_turn(
        session_id=session_id, role=body.role, text=body.text, ts=body.ts
    )
    return Ack(ok=ok, detail=None if ok else "session not found or empty turn")


@router.post("/{session_id}/close")
async def close_session(session_id: str, body: CloseSessionRequest) -> dict:
    transcript = (
        [t.model_dump() for t in body.transcript] if body.transcript is not None else None
    )
    sess = await get_store().close_session(
        session_id=session_id,
        end_reason=body.end_reason,
        transcript=transcript,
        lead_id=body.lead_id,
        channel=body.channel,
        direction=body.direction,
    )
    if sess is None:
        raise HTTPException(404, f"unknown session_id={session_id!r} and no lead_id to create it")
    log.info(
        "session closed",
        session_id=sess.session_id,
        lead_id=sess.lead_id,
        turns=len(sess.transcript or []),
        end_reason=sess.end_reason,
        will_analyze=not sess.analyzed,
    )
    return {"ok": True, "session_id": sess.session_id, "status": sess.status, "queued_for_analysis": not sess.analyzed}


@router.get("/{session_id}")
async def get_session(session_id: str) -> dict:
    sess = await get_store().get_session(session_id)
    if sess is None:
        raise HTTPException(404, f"unknown session_id={session_id!r}")
    return {
        "session_id": sess.session_id,
        "lead_id": sess.lead_id,
        "channel": sess.channel,
        "direction": sess.direction,
        "status": sess.status,
        "analyzed": sess.analyzed,
        "turns": len(sess.transcript or []),
        "analysis": sess.analysis,
        "started_at": sess.started_at,
        "ended_at": sess.ended_at,
    }
