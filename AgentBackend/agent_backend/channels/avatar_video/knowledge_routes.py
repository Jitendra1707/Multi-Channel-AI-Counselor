"""Knowledge-review HTTP surface (post-call governance).

The in-call path runs over the WebRTC data channel (see runner._on_app_message).
This REST surface is for the `/knowledge-review` screen, which acts on candidates
AFTER the call — when the live session and its in-memory record are gone. Ingest
lives here in AgentBackend (it owns Qdrant), so resolve/edit must hit this service
(not the BusinessLayer, which is storage-only).

  GET  /api/knowledge/candidates            list the review queue (from BusinessLayer)
  POST /api/knowledge/candidates/{id}/edit  apply edit → re-check → re-pending
  POST /api/knowledge/candidates/{id}/resolve  approve | supersede | keep_both | reject
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from agent_backend.channels.avatar_video import knowledge as kc
from agent_backend.infra import get_logger

log = get_logger(__name__)
router = APIRouter(tags=["knowledge"])


class EditRequest(BaseModel):
    text: Optional[str] = None
    heading: Optional[str] = None
    topic: Optional[str] = None
    kb: Optional[str] = None
    expected_version: Optional[int] = None


class ResolveRequest(BaseModel):
    action: str  # approve | supersede | keep_both | reject
    kb: Optional[str] = None
    supersede_point_ids: Optional[list[str]] = None
    resolved_by: Optional[str] = None
    expected_version: Optional[int] = None


@router.get("/candidates")
async def list_candidates(status: str = "pending", tenant_id: str | None = None) -> dict[str, Any]:
    items = await kc.list_candidates(status=status, tenant_id=tenant_id)
    return {"items": items, "count": len(items)}


@router.post("/candidates/{candidate_id}/edit")
async def edit_candidate(candidate_id: str, body: EditRequest) -> dict[str, Any]:
    fields = {k: v for k, v in body.model_dump().items() if k != "expected_version" and v is not None}
    return await kc.edit_by_id(candidate_id, fields, expected_version=body.expected_version)


@router.post("/candidates/{candidate_id}/resolve")
async def resolve_candidate(candidate_id: str, body: ResolveRequest) -> dict[str, Any]:
    return await kc.resolve_by_id(
        candidate_id,
        action=body.action,
        kb=body.kb,
        supersede_point_ids=body.supersede_point_ids,
        resolved_by=body.resolved_by,
        expected_version=body.expected_version,
    )
