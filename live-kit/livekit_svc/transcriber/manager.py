"""Transcriber manager — one RoomTranscriber per room, plus lifecycle.

Mints a hidden subscribe-only token for the transcriber (via the active provider,
so it works on Cloud or self-hosted), starts a RoomTranscriber, and tracks it by
room. `start_for_room` is called automatically by POST /schedule when
TRANSCRIBE_AUTO_START is on, or manually via POST /transcribe/start.

The transcriber token: can_subscribe=true, can_publish=false (it never speaks),
identity prefixed so it's excluded from speaker attribution. Uses the same
LiveKit URL the provider advertises — for self-hosted-in-Docker that's the
internal ws://livekit:7880, which is exactly where the SFU is reachable from
inside the compose network.
"""

from __future__ import annotations

import uuid

from livekit_svc.config import get_settings
from livekit_svc.logging import get_logger
from livekit_svc.providers import MeetingConfigError, get_provider
from livekit_svc.transcriber.participant import RoomTranscriber

log = get_logger(__name__)


class TranscriberManager:
    def __init__(self) -> None:
        self._by_room: dict[str, RoomTranscriber] = {}

    async def start_for_room(self, room: str) -> bool:
        """Join `room` with a transcriber. Idempotent — returns False if one is
        already running for the room. Best-effort: logs + returns False on
        config/connect errors rather than raising into the scheduler."""
        if room in self._by_room and not self._by_room[room].finalized:
            log.info("[transcriber] already running", room=room)
            return False

        s = get_settings()
        provider = get_provider()
        info = provider.info()
        if not info.configured:
            log.warning("[transcriber] LiveKit not configured — cannot start", room=room)
            return False
        if not s.azure_speech_key:
            log.warning("[transcriber] AZURE_SPEECH_KEY not set — transcription disabled", room=room)
            return False

        identity = f"{s.transcriber_identity_prefix}-{uuid.uuid4().hex[:8]}"
        try:
            token = provider.mint_token(
                room=room,
                identity=identity,
                display_name="Transcriber",
                role="transcriber",         # metadata; never counted as a speaker
                can_publish=False,          # hidden, listen-only
                can_subscribe=True,
            )
        except MeetingConfigError as e:
            log.warning("[transcriber] token mint failed", room=room, err=str(e))
            return False

        rt = RoomTranscriber(
            room_name=room, url=info.url, token=token, self_identity=identity,
            # When the transcriber self-finalizes (room emptied), drop it from the
            # registry so /transcribe doesn't accumulate stale finished entries.
            on_finalized=lambda r: self._by_room.pop(r, None),
        )
        try:
            await rt.start()
        except Exception as e:  # noqa: BLE001
            log.warning("[transcriber] join failed", room=room, err=str(e))
            return False

        self._by_room[room] = rt
        log.info("[transcriber] started", room=room, identity=identity)
        return True

    async def stop_for_room(self, room: str) -> dict | None:
        rt = self._by_room.get(room)
        if rt is None:
            return None
        result = await rt.stop(reason="manual-stop")
        self._by_room.pop(room, None)
        return result

    def status(self) -> list[dict]:
        return [
            {
                "room": r,
                "segments": rt.transcript.segment_count(),
                "finalized": rt.finalized,
            }
            for r, rt in self._by_room.items()
        ]

    def get_transcript(self, room: str) -> dict | None:
        rt = self._by_room.get(room)
        return rt.transcript.to_dict() if rt is not None else None

    async def shutdown(self) -> None:
        for room in list(self._by_room.keys()):
            try:
                await self._by_room[room].stop(reason="service-shutdown")
            except Exception:  # noqa: BLE001
                pass
        self._by_room.clear()


_manager: TranscriberManager | None = None


def get_transcriber_manager() -> TranscriberManager:
    global _manager
    if _manager is None:
        _manager = TranscriberManager()
    return _manager
