"""Meeting channel HTTP surface.

Endpoints
---------
POST   /api/meeting/schedule          — create a room + mint candidate/counsellor
                                         join links; optionally email them and
                                         dispatch the listening agent.
POST   /api/meeting/token             — mint a single join token (re-join / the
                                         web-app's own token fetch).
POST   /api/meeting/agent/join        — dispatch the listening agent into a room
                                         (called by /schedule, or manually).
DELETE /api/meeting/session/{room}    — make the agent leave + finalise analysis.
GET    /api/meeting/sessions          — debug: list rooms the agent is sitting in.

The control-plane (rooms + tokens) lives in `scheduler.py`; the media-plane (the
agent actually joining and listening) lives in `runner.py`. This module is the
thin HTTP glue between them, plus the optional email-out of join links (reusing
the existing email channel — no new transport).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from agent_backend.channels.meeting.runner import get_meeting_manager
from agent_backend.channels.meeting.scheduler import (
    MeetingConfigError,
    mint_token_async,
    schedule_meeting,
    start_candidate_meeting,
)
from agent_backend.infra import get_logger

log = get_logger(__name__)

router = APIRouter(tags=["meeting"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class ScheduleRequest(BaseModel):
    candidate_name: str = Field(..., description="Candidate display name in the room.")
    counsellor_name: str = Field(default="Counsellor")
    candidate_lead_id: str | None = Field(
        default=None,
        description="Lead id so the agent loads LEAD PROFILE + post-meeting analysis attaches to the lead.",
    )
    room: str | None = Field(
        default=None, description="Optional fixed room name; generated when omitted."
    )
    # Meeting mode. "solo" = 1:1 (agent greets + answers all); "panel" = agent is
    # a silent co-pilot alongside a human counsellor. None → server default
    # (MEETING_MODE).
    mode: str | None = Field(
        default=None, description="'solo' (1:1 agent+candidate) | 'panel' (multi-party co-pilot)."
    )
    # Email-out (optional). When both addresses are present and the email channel
    # is configured, the join links are emailed to each party.
    candidate_email: str | None = None
    counsellor_email: str | None = None
    send_emails: bool = Field(
        default=False, description="If true, email each party their join link."
    )
    # Agent dispatch. Default true — scheduling a meeting normally means the
    # listening agent should be in it. Set false to schedule humans only.
    dispatch_agent: bool = Field(default=True)


class ParticipantInviteOut(BaseModel):
    role: str
    identity: str
    display_name: str
    token: str
    join_url: str


class ScheduleResponse(BaseModel):
    room: str
    candidate: ParticipantInviteOut
    counsellor: ParticipantInviteOut
    agent_dispatched: bool


class TokenRequest(BaseModel):
    room: str
    identity: str
    display_name: str
    role: str = Field(default="candidate", description="'candidate' | 'counsellor' | observer label.")


class TokenResponse(BaseModel):
    room: str
    identity: str
    token: str


class AgentJoinRequest(BaseModel):
    room: str
    candidate_lead_id: str | None = None
    mode: str | None = None


class StartRequest(BaseModel):
    """Flow A — candidate-initiated 'call the counsellor now'. One call creates
    the room, dispatches the agent (solo by default), and returns the candidate's
    join URL so the caller can drop the candidate straight in. Also covers Flow B
    (API trigger): the agent waits in the room until the candidate opens the URL."""

    candidate_name: str = Field(default="Candidate")
    candidate_lead_id: str | None = None
    room: str | None = None
    mode: str | None = Field(default="solo", description="'solo' (default) | 'panel'.")


class StartResponse(BaseModel):
    room: str
    join_url: str
    token: str
    identity: str
    mode: str
    agent_dispatched: bool


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post("/schedule", response_model=ScheduleResponse)
async def schedule(body: ScheduleRequest) -> ScheduleResponse:
    """Create a meeting room, mint both join links, optionally email them and
    dispatch the listening agent."""
    try:
        meeting = await schedule_meeting(
            candidate_name=body.candidate_name,
            counsellor_name=body.counsellor_name,
            candidate_lead_id=body.candidate_lead_id,
            room=body.room,
        )
    except MeetingConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        log.warning("[meeting] schedule failed", err=str(e))
        raise HTTPException(status_code=502, detail=f"Scheduling failed: {e}") from e

    # Optional: email each party their personal join link (best-effort).
    if body.send_emails:
        await _email_invites(
            meeting,
            candidate_email=body.candidate_email,
            counsellor_email=body.counsellor_email,
        )

    # Dispatch the listening agent into the room (best-effort; a failure here
    # doesn't invalidate the human join links already minted).
    agent_dispatched = False
    if body.dispatch_agent:
        try:
            await get_meeting_manager().join_room(
                room=meeting.room,
                candidate_lead_id=meeting.candidate_lead_id,
                mode=body.mode,
            )
            agent_dispatched = True
        except MeetingConfigError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except Exception as e:  # noqa: BLE001
            log.warning("[meeting] agent dispatch failed (humans can still join)", err=str(e))

    return ScheduleResponse(
        room=meeting.room,
        candidate=ParticipantInviteOut(**meeting.candidate.__dict__),
        counsellor=ParticipantInviteOut(**meeting.counsellor.__dict__),
        agent_dispatched=agent_dispatched,
    )


@router.post("/start", response_model=StartResponse)
async def start(body: StartRequest) -> StartResponse:
    """Flow A / Flow B — create a room, dispatch the agent (default solo), and
    return the candidate's join URL in one call.

    - Flow A (candidate calls now): the caller opens `join_url` immediately; the
      agent is already in the room and greets on join.
    - Flow B (API trigger): hand `join_url` to the candidate; the agent waits up
      to MEETING_AGENT_WAIT_S for them, then greets when they arrive.
    """
    mode = (body.mode or "solo").strip().lower()
    try:
        room_name, invite = await start_candidate_meeting(
            candidate_name=body.candidate_name,
            candidate_lead_id=body.candidate_lead_id,
            room=body.room,
        )
    except MeetingConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        log.warning("[meeting] start failed", err=str(e))
        raise HTTPException(status_code=502, detail=f"Start failed: {e}") from e

    # Dispatch the agent into the room. A failure here doesn't invalidate the
    # candidate's join link (they can still join; the agent just isn't present).
    agent_dispatched = False
    try:
        await get_meeting_manager().join_room(
            room=room_name,
            candidate_lead_id=body.candidate_lead_id,
            mode=mode,
        )
        agent_dispatched = True
    except MeetingConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        log.warning("[meeting] start agent dispatch failed (candidate can still join)", err=str(e))

    return StartResponse(
        room=room_name,
        join_url=invite.join_url,
        token=invite.token,
        identity=invite.identity,
        mode=mode,
        agent_dispatched=agent_dispatched,
    )


@router.post("/token", response_model=TokenResponse)
async def token(body: TokenRequest) -> TokenResponse:
    """Mint a single join token — used by the web-app to (re)join a known room.
    Service-aware: routes through the live-kit/ service when configured."""
    try:
        jwt = await mint_token_async(
            room=body.room,
            identity=body.identity,
            display_name=body.display_name,
            role=body.role,
        )
    except MeetingConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return TokenResponse(room=body.room, identity=body.identity, token=jwt)


@router.post("/agent/join")
async def agent_join(body: AgentJoinRequest) -> dict[str, object]:
    """Dispatch the listening agent into an existing room (idempotent — re-join
    of a room the agent is already in is a no-op)."""
    try:
        await get_meeting_manager().join_room(
            room=body.room, candidate_lead_id=body.candidate_lead_id, mode=body.mode
        )
    except MeetingConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        log.warning("[meeting] agent join failed", room=body.room, err=str(e))
        raise HTTPException(status_code=502, detail=f"Agent join failed: {e}") from e
    return {"ok": True, "room": body.room}


@router.delete("/session/{room}")
async def end_session(room: str) -> Response:
    """Make the agent leave the room and finalise post-meeting analysis."""
    await get_meeting_manager().leave_room(room)
    return Response(status_code=204)


@router.post("/webhook")
async def webhook(event: dict) -> dict[str, object]:
    """Receive a LiveKit room event FORWARDED (already signature-verified) by the
    live-kit/ service. The robust production signal for 'meeting ended' — if the
    agent process ever dies, this still drives teardown + dual analysis.

    We act on `room_finished` (LiveKit reaped the room) by tearing down our local
    meeting state (which finalises the analysis). Other events are logged only.
    The live-kit/ service verified the signature, so this endpoint trusts its
    caller — keep it on the internal network / behind the service."""
    evt = (event.get("event") or "").strip()
    room = (event.get("room") or {}).get("name")
    log.info("[meeting] webhook event", event=evt, room=room)
    if evt == "room_finished" and room:
        # leave_room is idempotent — no-op if we already tore down on last-human-left.
        await get_meeting_manager().leave_room(room)
    return {"ok": True}


@router.get("/sessions")
async def list_sessions() -> dict[str, object]:
    """Debug: rooms the agent is currently sitting in."""
    sessions = get_meeting_manager().active_sessions()
    return {"sessions": sessions, "count": len(sessions)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _email_invites(
    meeting,  # ScheduledMeeting
    *,
    candidate_email: str | None,
    counsellor_email: str | None,
) -> None:
    """Email each party their personal join link. Best-effort; reuses the
    existing email channel so there's no new transport and it degrades cleanly
    when EMAIL_SMTP_HOST is unset."""
    from agent_backend.channels.email.client import send_email

    async def _one(to: str | None, invite, who: str) -> None:
        if not to:
            return
        try:
            await send_email(
                to=to,
                subject="Your counselling meeting link",
                body_text=(
                    f"Hi {invite.display_name},\n\n"
                    f"Your counselling meeting is ready. Join here:\n{invite.join_url}\n\n"
                    "See you there."
                ),
                body_html=(
                    f"<p>Hi {invite.display_name},</p>"
                    f"<p>Your counselling meeting is ready.</p>"
                    f'<p><a href="{invite.join_url}">Click here to join</a></p>'
                ),
            )
            log.info("[meeting] invite emailed", who=who, room=meeting.room)
        except Exception as e:  # noqa: BLE001
            log.debug("[meeting] invite email failed", who=who, err=str(e))

    await _one(candidate_email, meeting.candidate, "candidate")
    await _one(counsellor_email, meeting.counsellor, "counsellor")
