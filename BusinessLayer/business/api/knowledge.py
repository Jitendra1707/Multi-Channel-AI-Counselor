"""Knowledge-candidate endpoints — the durable record + post-call review queue.

AegisBackend owns the capture / contradiction-check / ingest logic (it alone
touches Qdrant); this service is the system-of-record + audit trail. AegisBackend
upserts a snapshot here on every state change; the `/knowledge-review` screen
reads the pending queue from here.

  POST /knowledge-candidates            upsert a snapshot (called by AegisBackend)
  GET  /knowledge-candidates            list (filter by status / tenant) — the queue
  GET  /knowledge-candidates/{id}       inspect one
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from business.logging import get_logger
from business.store import get_store

log = get_logger(__name__)
router = APIRouter(prefix="/knowledge-candidates", tags=["knowledge"])


class KnowledgeCandidateUpsert(BaseModel):
    # Accept the AegisBackend in-memory rec verbatim (id OR candidate_id), plus an
    # optional `_event` label for the audit timeline. Extra keys are ignored.
    model_config = {"extra": "allow"}

    id: Optional[str] = None
    candidate_id: Optional[str] = None


def _view(row: Any) -> dict[str, Any]:
    return {
        "candidate_id": row.candidate_id,
        "conversation_id": row.conversation_id,
        "tenant_id": row.tenant_id,
        "lead_id": row.lead_id,
        "status": row.status,
        "text": row.text,
        "heading": row.heading,
        "topic": row.topic,
        "kb": row.kb,
        "source_span": row.source_span,
        "trigger": row.trigger,
        "confidence": row.confidence,
        "conflict_score": row.conflict_score,
        "blocking": row.blocking,
        "conflict_items": row.conflict_items,
        "ingested": row.ingested,
        "ingested_point_id": row.ingested_point_id,
        "ingest_error": row.ingest_error,
        "resolved_by": row.resolved_by,
        "supersedes": row.supersedes,
        "resolution": row.resolution,
        "meta": row.meta,
        "events": row.events,
        "version": row.version,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "resolved_at": row.resolved_at,
    }


@router.post("")
async def upsert_candidate(body: KnowledgeCandidateUpsert) -> dict:
    rec = body.model_dump(exclude_none=False)
    cid = rec.get("candidate_id") or rec.get("id")
    if not cid:
        raise HTTPException(422, "candidate_id (or id) required")
    row = await get_store().upsert_knowledge_candidate(rec)
    return {"ok": True, "candidate_id": row.candidate_id, "status": row.status, "version": row.version}


@router.get("")
async def list_candidates(
    status: str | None = None, tenant_id: str | None = None,
    limit: int = 100, offset: int = 0,
) -> dict:
    rows = await get_store().list_knowledge_candidates(
        status=status, tenant_id=tenant_id, limit=limit, offset=offset
    )
    return {"items": [_view(r) for r in rows], "count": len(rows)}


@router.get("/{candidate_id}")
async def get_candidate(candidate_id: str) -> dict:
    row = await get_store().get_knowledge_candidate(candidate_id)
    if row is None:
        raise HTTPException(404, f"unknown candidate_id={candidate_id!r}")
    return _view(row)
