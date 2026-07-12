"""The transcriber participant — joins a room, transcribes every track.

A subscribe-only (hidden, non-publishing) LiveKit participant. On connect it
auto-subscribes to all tracks; for each AUDIO track it opens a per-track Deepgram
socket and pumps that track's PCM frames in. Because each track belongs to exactly
one participant, every transcript line is attributed to that speaker — diarization
with no model.

Lifecycle:
  start()  → connect, wire events, transcribe until the room empties or stop().
  stop()   → close all sockets, disconnect, write the single transcript file.

Speaker label resolution: we read each participant's identity + metadata. The
meeting tokens set metadata to a role ("candidate"/"counsellor"/"agent"); we map
identity→that label (falling back to the display name, then identity). The
transcriber's own identity and any "agent" participant are NOT transcribed as a
human speaker — well, the agent IS a speaker (it talks), so we DO transcribe it
too and label it "agent"; we only skip the transcriber itself.
"""

from __future__ import annotations

import asyncio
import time

from livekit import rtc

from livekit_svc.config import get_settings
from livekit_svc.logging import get_logger
from livekit_svc.transcriber.stt_azure import TrackSTT
from livekit_svc.transcriber.transcript import Transcript

log = get_logger(__name__)


class RoomTranscriber:
    """Transcribes one LiveKit room end-to-end."""

    def __init__(
        self,
        *,
        room_name: str,
        url: str,
        token: str,
        self_identity: str,
        on_finalized=None,  # Optional[Callable[[str], None]] — called with room on stop
    ) -> None:
        self._room_name = room_name
        self._url = url
        self._token = token
        self._self_identity = self_identity
        self._on_finalized = on_finalized
        self._room: rtc.Room | None = None
        self._transcript = Transcript(room_name, started_at=time.time())
        self._track_tasks: dict[str, asyncio.Task] = {}
        self._stts: dict[str, TrackSTT] = {}
        self._closed = asyncio.Event()
        self._finalized = False

    # ------------------------------------------------------------------
    async def start(self) -> None:
        self._room = rtc.Room()

        @self._room.on("track_subscribed")
        def _on_track(track, publication, participant) -> None:  # noqa: ANN001
            # Only audio; ignore video/data. Never transcribe ourselves.
            if participant.identity == self._self_identity:
                return
            if getattr(track, "kind", None) != rtc.TrackKind.KIND_AUDIO:
                return
            self._record_speaker(participant)
            t = asyncio.create_task(
                self._transcribe_track(track, participant.identity),
                name=f"stt-{participant.identity}",
            )
            self._track_tasks[participant.identity] = t

        @self._room.on("participant_connected")
        def _on_join(participant) -> None:  # noqa: ANN001
            self._record_speaker(participant)

        @self._room.on("participant_disconnected")
        def _on_leave(participant) -> None:  # noqa: ANN001
            log.info("[transcriber] participant left", room=self._room_name, who=participant.identity)
            # If no humans remain, end + finalize.
            if self._human_count() <= 0:
                asyncio.create_task(self.stop(reason="all-participants-left"))

        @self._room.on("disconnected")
        def _on_disc(*_args) -> None:  # noqa: ANN001
            asyncio.create_task(self.stop(reason="room-disconnected"))

        await self._room.connect(
            self._url,
            self._token,
            options=rtc.RoomOptions(auto_subscribe=True),
        )
        log.info("[transcriber] joined room", room=self._room_name, identity=self._self_identity)

        # Map any participants already present.
        for p in self._room.remote_participants.values():
            self._record_speaker(p)

    # ------------------------------------------------------------------
    def _record_speaker(self, participant) -> None:  # noqa: ANN001
        if participant.identity == self._self_identity:
            return
        # Label preference: the participant's DISPLAY NAME (the actual name typed
        # when the meeting was created — set via token .with_name()), so the
        # transcript reads "Jitendra:" / "Ramesh:" rather than the generic role.
        # Fall back to the role metadata (candidate/counsellor/agent) only when no
        # name was given, then to the raw identity. This naturally supports ANY
        # number of named participants, not just a fixed counsellor/candidate pair.
        name = (getattr(participant, "name", "") or "").strip()
        role = (getattr(participant, "metadata", "") or "").strip()
        label = name or role or participant.identity
        self._transcript.set_speaker_label(participant.identity, label)

    def _human_count(self) -> int:
        if self._room is None:
            return 0
        # remote_participants excludes us (the local transcriber). Count everyone
        # that isn't an "agent" as a human; an agent-only room should still end.
        return sum(
            1
            for p in self._room.remote_participants.values()
            if (getattr(p, "metadata", "") or "").strip().lower() != "agent"
        )

    async def _transcribe_track(self, track, identity: str) -> None:  # noqa: ANN001
        """Pump one participant's audio into a dedicated Deepgram socket."""
        stream = rtc.AudioStream(track)
        stt: TrackSTT | None = None
        loop = asyncio.get_running_loop()
        try:
            async for ev in stream:
                frame = ev.frame
                if stt is None:
                    # First frame tells us the real sample rate / channels.
                    async def _on_final(text: str, start: float, end: float, _id=identity) -> None:
                        self._transcript.add(speaker=_id, text=text, start=start, end=end)
                        log.info("[transcriber] segment", room=self._room_name, speaker=_id, text=text[:80])

                    stt = TrackSTT(
                        sample_rate=frame.sample_rate,
                        num_channels=frame.num_channels,
                        on_final=_on_final,
                        speaker=identity,
                        loop=loop,
                    )
                    self._stts[identity] = stt
                    await stt.start()
                await stt.send(bytes(frame.data))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("[transcriber] track task error", room=self._room_name, who=identity, err=str(e))
        finally:
            if stt is not None:
                await stt.finish()

    # ------------------------------------------------------------------
    async def stop(self, *, reason: str = "manual") -> dict:
        """Close everything + write the one transcript file. Idempotent."""
        if self._finalized:
            return self._last_result or {}
        self._finalized = True
        log.info("[transcriber] stopping", room=self._room_name, reason=reason)

        # Cancel + await track tasks. Each task's `finally` calls stt.finish(),
        # so we do NOT finish the STTs again here (a double-finish triggers the
        # "azure recognizer canceled" warnings). finish() is also idempotent now.
        for t in self._track_tasks.values():
            if not t.done():
                t.cancel()
        for t in list(self._track_tasks.values()):
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        if self._room is not None:
            try:
                await self._room.disconnect()
            except Exception as e:  # noqa: BLE001
                log.debug("[transcriber] disconnect error", err=str(e))

        # Write the transcript — but NEVER let a write failure leave the
        # transcriber half-finalized (which would strand it in the manager's
        # registry). Always mark closed; return empty paths on failure.
        result: dict = {}
        try:
            result = self._transcript.write_files(ended_at=time.time())
        except Exception as e:  # noqa: BLE001
            log.warning("[transcriber] transcript write failed", room=self._room_name, err=str(e))
        self._last_result = result
        self._closed.set()
        # Let the manager drop us from its registry (we may have self-finalized on
        # room-empty, which the manager's stop_for_room path never saw).
        if self._on_finalized is not None:
            try:
                self._on_finalized(self._room_name)
            except Exception:  # noqa: BLE001
                pass
        return result

    _last_result: dict | None = None

    @property
    def transcript(self) -> Transcript:
        return self._transcript

    @property
    def finalized(self) -> bool:
        return self._finalized


__all__ = ["RoomTranscriber"]
