"""live-kit service HTTP surface — the control-plane seam.

AegisBackend calls THESE endpoints instead of using livekit-api directly, so the
Cloud↔self-hosted choice lives in one place. The media plane (the agent's WebRTC
pipeline) stays in AegisBackend — WebRTC is a persistent peer connection and
cannot pass through a request/response endpoint — but even the agent fetches its
server URL + join token from here, so all LiveKit *coordinates* flow through this
one seam.

Endpoints
---------
GET  /health                  — provider + configured status (+ wss url)
GET  /config                  — { provider, url } the web-app fetches for its SFU URL
POST /rooms                   — create (or reuse) a room → { room }
POST /token                   — mint ONE join JWT → { room, identity, token, url }
POST /rooms/{room}/delete     — delete a room (best-effort)
POST /webhook                 — LiveKit → here; verify signature, forward to AegisBackend

All provider errors degrade cleanly: not-configured → 503, bad input → 4xx,
upstream LiveKit failure → 502. Never a bare 500.
"""

from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from livekit_svc.config import get_settings
from livekit_svc.logging import get_logger
from livekit_svc.providers import MeetingConfigError, get_provider

log = get_logger(__name__)
router = APIRouter(tags=["livekit"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class CreateRoomRequest(BaseModel):
    room: str | None = Field(default=None, description="Fixed room name; generated when omitted.")


class CreateRoomResponse(BaseModel):
    room: str


class TokenRequest(BaseModel):
    room: str
    display_name: str
    # identity is auto-generated (unique) when omitted, so a "join with your name"
    # flow can request a token with just {room, display_name} — every joiner gets
    # a distinct participant without the caller tracking identities.
    identity: str | None = Field(default=None)
    role: str = Field(default="guest", description="candidate | counsellor | guest | observer")
    can_publish: bool = True
    can_subscribe: bool = True


class TokenResponse(BaseModel):
    room: str
    identity: str
    token: str
    url: str = Field(description="The wss:// URL the holder connects to (one source of truth).")


class ConfigResponse(BaseModel):
    provider: str
    url: str
    configured: bool


class ScheduleRequest(BaseModel):
    """Create a HUMAN↔HUMAN meeting — no agent involved. Mints a join link for
    the candidate and the counsellor. The AI agent (if wanted) is added SEPARATELY
    by the frontend calling AegisBackend's /api/meeting/agent/join with this room
    name — this service never touches the agent."""

    candidate_name: str = Field(default="Candidate")
    counsellor_name: str = Field(default="Counsellor")
    room: str | None = Field(default=None, description="Fixed room name; generated when omitted.")


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
    url: str = Field(description="The SFU wss:// URL both humans connect to.")


# ---------------------------------------------------------------------------
# Health / config
# ---------------------------------------------------------------------------
@router.get("/health")
async def health() -> dict[str, object]:
    info = get_provider().info()
    return {
        "ok": True,
        "service": "livekit-svc",
        "provider": info.provider,
        "url": info.url,
        "configured": info.configured,
    }


@router.get("/config", response_model=ConfigResponse)
async def config() -> ConfigResponse:
    """The SFU URL + active provider. The web-app fetches this so the wss URL has
    a single source of truth (instead of duplicating it in NEXT_PUBLIC_*)."""
    info = get_provider().info()
    return ConfigResponse(provider=info.provider, url=info.url, configured=info.configured)


# ---------------------------------------------------------------------------
# Rooms + tokens
# ---------------------------------------------------------------------------
@router.post("/rooms", response_model=CreateRoomResponse)
async def create_room(body: CreateRoomRequest) -> CreateRoomResponse:
    try:
        room = await get_provider().create_room(body.room)
        return CreateRoomResponse(room=room)
    except MeetingConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        log.warning("create_room failed", err=str(e))
        raise HTTPException(status_code=502, detail=f"LiveKit room creation failed: {e}") from e


@router.post("/token", response_model=TokenResponse)
async def mint_token(body: TokenRequest) -> TokenResponse:
    provider = get_provider()
    # Auto-assign a unique identity when the caller didn't supply one (the
    # "join with your name" flow). Derive a readable prefix from the role so logs
    # stay legible; the uuid suffix guarantees uniqueness within the room.
    identity = body.identity or f"{(body.role or 'guest').strip().lower()}-{uuid.uuid4().hex[:8]}"
    try:
        token = provider.mint_token(
            room=body.room,
            identity=identity,
            display_name=body.display_name,
            role=body.role,
            can_publish=body.can_publish,
            can_subscribe=body.can_subscribe,
        )
    except MeetingConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return TokenResponse(
        room=body.room,
        identity=identity,
        token=token,
        url=provider.info().url,
    )


def _build_join_url(room: str, token: str, role: str) -> str:
    base = get_settings().meeting_join_base_url.rstrip("/")
    return f"{base}/meeting/{quote(room)}?token={quote(token)}&role={quote(role)}"


def _share_url(room: str) -> str:
    """A TOKENLESS shareable link (Google-Meet style). Whoever opens it enters
    their own name; the app mints their token via /token. One link for everyone."""
    base = get_settings().meeting_join_base_url.rstrip("/")
    return f"{base}/meeting/{quote(room)}"


class CreateMeetingRequest(BaseModel):
    """One-click meeting — no names. Creates a room and returns ONE shareable
    link anyone can open. The AI agent is optional and added separately via
    AegisBackend (the frontend calls /api/meeting/agent/join when wanted)."""

    room: str | None = Field(default=None, description="Fixed room name; generated when omitted.")


class CreateMeetingResponse(BaseModel):
    room: str
    share_url: str = Field(description="One public link to share with anyone (no token; they enter their name).")
    url: str = Field(description="The SFU wss:// URL clients connect to.")


@router.post("/meeting", response_model=CreateMeetingResponse)
async def create_meeting(body: CreateMeetingRequest) -> CreateMeetingResponse:
    """Create a single meeting room and return ONE shareable, tokenless link —
    the Google-Meet model. No participant names needed at creation; each person
    opens the link, types their name, and joins."""
    provider = get_provider()
    try:
        room = await provider.create_room(body.room)
    except MeetingConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        log.warning("create_meeting failed", err=str(e))
        raise HTTPException(status_code=502, detail=f"LiveKit room creation failed: {e}") from e
    log.info("meeting created (one-link)", room=room)
    return CreateMeetingResponse(room=room, share_url=_share_url(room), url=provider.info().url)


@router.post("/schedule", response_model=ScheduleResponse)
async def schedule(body: ScheduleRequest) -> ScheduleResponse:
    """Create a human↔human meeting: room + a join link for each human.

    Fully self-contained — knows nothing about the AI agent. The frontend talks
    to THIS service for ordinary human-to-human meetings (so they work even when
    AegisBackend is down) and only calls AegisBackend when 'Include/Add AI' is
    chosen. Identities are unique per role so the two humans never collide."""
    provider = get_provider()
    try:
        room = await provider.create_room(body.room)
        cand_identity = f"candidate-{uuid.uuid4().hex[:8]}"
        coun_identity = f"counsellor-{uuid.uuid4().hex[:8]}"
        cand_token = provider.mint_token(
            room=room, identity=cand_identity,
            display_name=body.candidate_name, role="candidate",
        )
        coun_token = provider.mint_token(
            room=room, identity=coun_identity,
            display_name=body.counsellor_name, role="counsellor",
        )
    except MeetingConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        log.warning("schedule failed", err=str(e))
        raise HTTPException(status_code=502, detail=f"LiveKit scheduling failed: {e}") from e

    log.info("meeting scheduled (human↔human)", room=room,
             candidate=body.candidate_name, counsellor=body.counsellor_name)

    # AUTO-START the transcriber (STT-only, no agent) the moment the meeting is
    # created, if enabled. It joins as a hidden listener and writes one transcript
    # file when the meeting ends. Best-effort — a transcriber failure must never
    # block the humans from getting their join links.
    if get_settings().transcribe_auto_start:
        from livekit_svc.transcriber import get_transcriber_manager

        try:
            await get_transcriber_manager().start_for_room(room)
        except Exception as e:  # noqa: BLE001
            log.warning("transcriber auto-start failed (meeting still works)", room=room, err=str(e))

    return ScheduleResponse(
        room=room,
        candidate=ParticipantInviteOut(
            role="candidate", identity=cand_identity, display_name=body.candidate_name,
            token=cand_token, join_url=_build_join_url(room, cand_token, "candidate"),
        ),
        counsellor=ParticipantInviteOut(
            role="counsellor", identity=coun_identity, display_name=body.counsellor_name,
            token=coun_token, join_url=_build_join_url(room, coun_token, "counsellor"),
        ),
        url=provider.info().url,
    )


@router.post("/rooms/{room}/delete")
async def delete_room(room: str) -> dict[str, object]:
    try:
        await get_provider().delete_room(room)
    except MeetingConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        # delete is best-effort; report but don't fail the caller's teardown.
        log.debug("delete_room non-fatal", room=room, err=str(e))
    return {"ok": True, "room": room}


# ---------------------------------------------------------------------------
# Webhook ingest (LiveKit → here → AegisBackend)
# ---------------------------------------------------------------------------
@router.post("/webhook")
async def webhook(request: Request) -> Response:
    """Receive a LiveKit webhook, VERIFY its signature, then (optionally) forward
    the verified event to AegisBackend.

    LiveKit signs the request with a JWT in the Authorization header whose body
    hash must match the raw body — so we verify against the RAW bytes, not a
    re-serialised model. An invalid signature → 401."""
    s = get_settings()
    raw = await request.body()
    auth = request.headers.get("Authorization", "")
    try:
        event = get_provider().verify_webhook(raw.decode("utf-8"), auth)
    except MeetingConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        log.warning("webhook signature verification failed", err=str(e))
        raise HTTPException(status_code=401, detail="invalid webhook signature") from e

    evt_type = event.get("event")
    room = (event.get("room") or {}).get("name")
    log.info("webhook received", event=evt_type, room=room)

    # Forward to AegisBackend if configured (best-effort — never fail LiveKit's
    # delivery on a downstream hiccup; LiveKit would otherwise retry).
    if s.aegis_webhook_url:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=s.forward_timeout_s) as client:
                await client.post(s.aegis_webhook_url, json=event)
            log.info("webhook forwarded", event=evt_type, room=room, to=s.aegis_webhook_url)
        except Exception as e:  # noqa: BLE001
            log.warning("webhook forward failed (acking LiveKit anyway)", err=str(e))

    return Response(status_code=200)


# ---------------------------------------------------------------------------
# Transcriber (STT-only, no agent) — join a room, diarize by participant track,
# write one transcript file on meeting end. Auto-starts from /schedule; these
# endpoints are for manual control + fetching.
# ---------------------------------------------------------------------------
class TranscribeStartRequest(BaseModel):
    room: str


@router.post("/transcribe/start")
async def transcribe_start(body: TranscribeStartRequest) -> dict[str, object]:
    """Manually start a transcriber for a room (idempotent). Normally not needed
    — /schedule auto-starts one when TRANSCRIBE_AUTO_START is on."""
    from livekit_svc.transcriber import get_transcriber_manager

    started = await get_transcriber_manager().start_for_room(body.room)
    return {"ok": True, "room": body.room, "started": started}


@router.post("/transcribe/{room}/stop")
async def transcribe_stop(room: str) -> dict[str, object]:
    """Stop a transcriber and write its transcript file now. Returns the paths."""
    from livekit_svc.transcriber import get_transcriber_manager

    result = await get_transcriber_manager().stop_for_room(room)
    if result is None:
        raise HTTPException(status_code=404, detail=f"no transcriber for room {room!r}")
    return {"ok": True, "room": room, "files": result}


@router.get("/transcribe/{room}")
async def transcribe_get(room: str) -> dict[str, object]:
    """Fetch the in-progress (or just-finished) transcript for a room."""
    from livekit_svc.transcriber import get_transcriber_manager

    data = get_transcriber_manager().get_transcript(room)
    if data is None:
        raise HTTPException(status_code=404, detail=f"no transcript for room {room!r}")
    return data


@router.get("/transcribe")
async def transcribe_status() -> dict[str, object]:
    """List active transcribers (debug)."""
    from livekit_svc.transcriber import get_transcriber_manager

    return {"transcribers": get_transcriber_manager().status()}
