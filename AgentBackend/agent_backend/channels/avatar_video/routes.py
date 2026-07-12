"""Avatar video channel HTTP surface — SmallWebRTC signalling.

Endpoints
---------
POST   /api/avatar_video/offer            — WebRTC SDP offer → SDP answer
DELETE /api/avatar_video/session/{pc_id}  — end a session
GET    /api/avatar_video/sessions         — list active sessions (debug)

The browser performs one POST /offer with its SDP offer; the backend creates
the SmallWebRTC peer + Simli pipeline and returns the SDP answer. All media
(mic up, avatar video+audio down) then flows over that single WebRTC peer.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from agent_backend.channels.avatar_video.runner import get_avatar_manager
from agent_backend.infra import get_logger

log = get_logger(__name__)

router = APIRouter(tags=["avatar_video"])


# ---------------------------------------------------------------------------
# Models.
# ---------------------------------------------------------------------------


class OfferRequest(BaseModel):
    sdp: str
    type: str
    pc_id: str | None = None
    lead_id: str | None = None


class OfferResponse(BaseModel):
    sdp: str
    type: str
    pc_id: str


# ---------------------------------------------------------------------------
# Routes.
# ---------------------------------------------------------------------------


@router.post("/offer", response_model=OfferResponse)
async def offer(body: OfferRequest) -> OfferResponse:
    """Accept a browser WebRTC SDP offer and return the SDP answer.

    On a new connection this also spins up the Simli avatar pipeline bound to
    the peer. On an existing pc_id it renegotiates the connection.
    """
    try:
        answer = await get_avatar_manager().handle_offer(
            sdp=body.sdp,
            type_=body.type,
            pc_id=body.pc_id,
            lead_id=body.lead_id,
        )
        return OfferResponse(**answer)
    except RuntimeError as e:
        log.warning("[avatar-video] offer configuration error", err=str(e))
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        log.warning("[avatar-video] offer failed", err=str(e))
        raise HTTPException(status_code=502, detail=f"WebRTC negotiation failed: {e}") from e


@router.delete("/session/{pc_id}")
async def end_session(pc_id: str) -> Response:
    """End an avatar video session and tear down its pipeline + WebRTC peer."""
    await get_avatar_manager().end_session(pc_id)
    return Response(status_code=204)


@router.get("/sessions")
async def list_sessions() -> dict[str, object]:
    """Debug: list all active WebRTC sessions and their pipeline state."""
    sessions = get_avatar_manager().active_sessions()
    return {"sessions": sessions, "count": len(sessions)}
