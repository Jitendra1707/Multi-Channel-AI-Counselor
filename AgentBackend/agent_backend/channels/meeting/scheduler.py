"""Meeting scheduler — LiveKit room creation + JWT minting.

This is the thin control-plane for the meeting channel. It does NOT touch media
(that's the runner/pipeline); it just:

  1. Creates a LiveKit room (idempotent — LiveKit returns the existing room if
     the name is reused).
  2. Mints a join JWT per HUMAN participant (counsellor + candidate), each with
     a stable `identity` and a `name` + `metadata` so the agent can attribute
     transcripts to the right speaker (see channels/meeting/bridge.py M4).
  3. Builds the browser join URLs (<web-app>/meeting/<room>?token=…&role=…).

Everything is best-effort and flag-gated on LIVEKIT_URL / key / secret: with any
of them unset the functions raise `MeetingConfigError`, which the route turns
into a clean 503 — same graceful-degrade contract as the other outbound channels
(email / whatsapp / voice boot without their secrets).

The agent's OWN token (it joins server-side) is minted in the runner, not here,
because the runner owns the agent's lifecycle. Identity for the agent is
`settings.livekit_agent_identity`, kept distinct from both human identities so
per-speaker attribution never mistakes the agent's published TTS for a human.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import quote

from livekit import api

from agent_backend.config import get_settings
from agent_backend.infra import get_logger

log = get_logger(__name__)

MeetingRole = Literal["counsellor", "candidate"]


class MeetingConfigError(RuntimeError):
    """LiveKit isn't configured (URL / API key / secret missing)."""


@dataclass(frozen=True)
class ParticipantInvite:
    """One human's join credentials for the room."""

    role: MeetingRole
    identity: str
    display_name: str
    token: str
    join_url: str


@dataclass(frozen=True)
class ScheduledMeeting:
    """The result of scheduling — everything the caller needs to invite humans
    and dispatch the agent."""

    room: str
    candidate: ParticipantInvite
    counsellor: ParticipantInvite
    candidate_lead_id: str | None = None
    created_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Config guard
# ---------------------------------------------------------------------------
def _require_config() -> tuple[str, str, str]:
    """Return (url, api_key, api_secret) or raise MeetingConfigError."""
    s = get_settings()
    if not (s.livekit_url and s.livekit_api_key and s.livekit_api_secret):
        raise MeetingConfigError(
            "LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set "
            "to host meetings."
        )
    return s.livekit_url, s.livekit_api_key, s.livekit_api_secret


def _join_base_url() -> str:
    """Public web-app base for join links. Prefers MEETING_JOIN_BASE_URL; falls
    back to the first FRONTEND_URLS origin; finally localhost for dev."""
    s = get_settings()
    if s.meeting_join_base_url.strip():
        return s.meeting_join_base_url.rstrip("/")
    first_origin = next(
        (o.strip() for o in s.frontend_urls.split(",") if o.strip()),
        "http://localhost:3000",
    )
    return first_origin.rstrip("/")


# ---------------------------------------------------------------------------
# Token minting
#
# Two modes, picked per call by `livekit_service.enabled()`:
#   - SERVICE mode (LIVEKIT_SERVICE_URL set): ask the live-kit/ service. This is
#     the clean seam — Cloud↔self-hosted is decided there, not here.
#   - DIRECT mode (blank): mint locally from LIVEKIT_URL/KEY/SECRET (the original
#     behaviour — kept so existing deployments are unaffected until they opt in).
# `mint_token` stays SYNC for direct callers; `mint_token_async` is the
# service-aware path used by the async scheduler/runner.
# ---------------------------------------------------------------------------
def mint_token(
    *,
    room: str,
    identity: str,
    display_name: str,
    role: str,
    can_publish: bool = True,
    can_subscribe: bool = True,
) -> str:
    """Mint a LiveKit join JWT locally (DIRECT mode).

    `role` is stamped into the participant metadata (JSON-free, a bare string)
    so the agent can read it off the participant and attribute each transcript
    line to counsellor vs candidate. `identity` MUST be unique per participant
    within the room — LiveKit rejects a duplicate identity join.
    """
    _, api_key, api_secret = _require_config()
    grants = api.VideoGrants(
        room_join=True,
        room=room,
        can_publish=can_publish,
        can_subscribe=can_subscribe,
        can_publish_data=True,
    )
    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name(display_name)
        # metadata is read by the agent (participant.metadata) for speaker
        # attribution. Keep it a simple "role" string; richer JSON can go here
        # later (lead_id, language) without changing the read side.
        .with_metadata(role)
        .with_grants(grants)
        .with_ttl(_ttl())
    )
    return token.to_jwt()


async def mint_token_async(
    *,
    room: str,
    identity: str,
    display_name: str,
    role: str,
    can_publish: bool = True,
    can_subscribe: bool = True,
) -> str:
    """Service-aware token mint. Routes through the live-kit/ service when
    LIVEKIT_SERVICE_URL is set, else mints locally. Returns the JWT only (the
    SFU URL is fetched separately via `sfu_url()` when needed)."""
    from agent_backend.integrations import livekit_service as svc

    if svc.enabled():
        try:
            data = await svc.mint_token(
                room=room,
                identity=identity,
                display_name=display_name,
                role=role,
                can_publish=can_publish,
                can_subscribe=can_subscribe,
            )
            return data["token"]
        except svc.LiveKitServiceError as e:
            raise MeetingConfigError(str(e)) from e
    return mint_token(
        room=room,
        identity=identity,
        display_name=display_name,
        role=role,
        can_publish=can_publish,
        can_subscribe=can_subscribe,
    )


async def sfu_url() -> str:
    """The wss:// SFU URL the agent's transport + the web-app connect to.

    SERVICE mode: ask the service (single source of truth). DIRECT mode: the
    local LIVEKIT_URL. Used by the runner to build the agent's LiveKitTransport."""
    from agent_backend.integrations import livekit_service as svc

    if svc.enabled():
        try:
            url = await svc.get_url()
            if url:
                return url
        except svc.LiveKitServiceError as e:
            log.warning("[meeting] live-kit service /config failed; using local LIVEKIT_URL", err=str(e))
    return get_settings().livekit_url


def _ttl():
    import datetime

    return datetime.timedelta(seconds=get_settings().livekit_token_ttl_s)


def _build_join_url(room: str, token: str, role: str) -> str:
    base = _join_base_url()
    return f"{base}/meeting/{quote(room)}?token={quote(token)}&role={quote(role)}"


# ---------------------------------------------------------------------------
# Room creation + scheduling
# ---------------------------------------------------------------------------
async def create_room(room: str | None = None) -> str:
    """Create (or reuse) a LiveKit room and return its name.

    SERVICE mode (LIVEKIT_SERVICE_URL set): delegate to the live-kit/ service.
    DIRECT mode: create via livekit-api locally (original behaviour).

    LiveKit `create_room` is idempotent on name — calling it for an existing
    room returns that room. A fresh name is generated when none is given.
    """
    from agent_backend.integrations import livekit_service as svc

    if svc.enabled():
        try:
            return await svc.create_room(room)
        except svc.LiveKitServiceError as e:
            raise MeetingConfigError(str(e)) from e

    url, api_key, api_secret = _require_config()
    s = get_settings()
    room_name = room or f"meet-{uuid.uuid4().hex[:12]}"

    lk = api.LiveKitAPI(url, api_key, api_secret)
    try:
        await lk.room.create_room(
            api.CreateRoomRequest(
                name=room_name,
                empty_timeout=s.livekit_room_empty_timeout_s,
                max_participants=s.livekit_room_max_participants,
            )
        )
        log.info("[meeting] room created", room=room_name)
    finally:
        await lk.aclose()
    return room_name


async def start_candidate_meeting(
    *,
    candidate_name: str = "Candidate",
    candidate_lead_id: str | None = None,
    room: str | None = None,
) -> tuple[str, ParticipantInvite]:
    """Create (or reuse) a room and mint a SINGLE candidate join invite.

    Powers the candidate-initiated 'call the counsellor now' flow (Flow A) and
    the API-trigger flow (Flow B): no human counsellor is involved, so we don't
    mint a counsellor token — just the candidate's URL. The agent is dispatched
    separately by the caller (route) via the meeting manager.

    Returns (room_name, candidate_invite)."""
    room_name = await create_room(room)
    cand_identity = f"candidate-{uuid.uuid4().hex[:8]}"
    cand_token = await mint_token_async(
        room=room_name,
        identity=cand_identity,
        display_name=candidate_name,
        role="candidate",
    )
    invite = ParticipantInvite(
        role="candidate",
        identity=cand_identity,
        display_name=candidate_name,
        token=cand_token,
        join_url=_build_join_url(room_name, cand_token, "candidate"),
    )
    log.info(
        "[meeting] candidate meeting started",
        room=room_name, candidate=candidate_name, lead_id=candidate_lead_id,
    )
    return room_name, invite


async def schedule_meeting(
    *,
    candidate_name: str,
    counsellor_name: str = "Counsellor",
    candidate_lead_id: str | None = None,
    room: str | None = None,
) -> ScheduledMeeting:
    """Create the room and mint both human join invites.

    Returns a `ScheduledMeeting` with each participant's token + browser join
    URL. The caller (route) optionally emails/WhatsApps the links and dispatches
    the agent. Identities are deterministic-but-unique per role so the two humans
    never collide and the agent can map identity → role for attribution.
    """
    room_name = await create_room(room)

    # Unique identities per role (suffix keeps them distinct if a name repeats).
    cand_identity = f"candidate-{uuid.uuid4().hex[:8]}"
    coun_identity = f"counsellor-{uuid.uuid4().hex[:8]}"

    cand_token = await mint_token_async(
        room=room_name,
        identity=cand_identity,
        display_name=candidate_name,
        role="candidate",
    )
    coun_token = await mint_token_async(
        room=room_name,
        identity=coun_identity,
        display_name=counsellor_name,
        role="counsellor",
    )

    meeting = ScheduledMeeting(
        room=room_name,
        candidate=ParticipantInvite(
            role="candidate",
            identity=cand_identity,
            display_name=candidate_name,
            token=cand_token,
            join_url=_build_join_url(room_name, cand_token, "candidate"),
        ),
        counsellor=ParticipantInvite(
            role="counsellor",
            identity=coun_identity,
            display_name=counsellor_name,
            token=coun_token,
            join_url=_build_join_url(room_name, coun_token, "counsellor"),
        ),
        candidate_lead_id=candidate_lead_id,
    )
    log.info(
        "[meeting] scheduled",
        room=room_name,
        candidate=candidate_name,
        counsellor=counsellor_name,
        lead_id=candidate_lead_id,
    )
    return meeting


__all__ = [
    "MeetingConfigError",
    "MeetingRole",
    "ParticipantInvite",
    "ScheduledMeeting",
    "create_room",
    "mint_token",
    "mint_token_async",
    "sfu_url",
    "schedule_meeting",
    "start_candidate_meeting",
]
