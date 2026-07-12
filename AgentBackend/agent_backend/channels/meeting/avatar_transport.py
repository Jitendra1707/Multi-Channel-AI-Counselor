"""LiveKit transport that can PUBLISH an avatar video track.

Why this exists
---------------
pipecat 0.0.89's `LiveKitOutputTransport` implements `write_audio_frame` but NOT
`write_video_frame` — its base returns False, so any `OutputImageRawFrame` the
pipeline produces (e.g. the Simli avatar's video) is silently dropped. The agent
therefore joins audio-only and the candidate sees a blank tile, even with
`video_out_enabled=True` (that param is inherited from TransportParams but the
LiveKit output never acts on it).

This module subclasses the transport to add the missing piece:

  * `_AvatarLiveKitOutputTransport.write_video_frame()` lazily creates a LiveKit
    `rtc.VideoSource` + `LocalVideoTrack`, publishes it as the agent's CAMERA
    source, and pushes each (already-resized-to-output-size, RGB24) frame the
    base video pacer hands us via `source.capture_frame()`.
  * Audio is GATED on video readiness: `write_audio_frame` returns early until the
    first video frame has been published, so the candidate never hears the agent
    talk over a blank tile — the voice and the face appear together. Simli emits
    idle video immediately on connect, so the gate opens within ~1-2 s.

`AvatarLiveKitTransport` just overrides `output()` to return the avatar-capable
output transport; everything else (input, client, room mgmt, events) is the stock
LiveKit transport unchanged.
"""

from __future__ import annotations

import asyncio

from livekit import rtc
from livekit.rtc._proto import video_frame_pb2 as proto_video_frame

from pipecat.frames.frames import OutputAudioRawFrame, OutputImageRawFrame
from pipecat.transports.livekit.transport import (
    LiveKitOutputTransport,
    LiveKitParams,
    LiveKitTransport,
    LiveKitTransportClient,
)

from agent_backend.config import get_settings
from agent_backend.infra import get_logger

log = get_logger(__name__)

# Simli emits RGB24; LiveKit's VideoFrame takes a buffer-type enum. RGB24 = 4.
_RGB24 = proto_video_frame.VideoBufferType.RGB24


class _AvatarLiveKitOutputTransport(LiveKitOutputTransport):
    """LiveKit output transport that publishes a video track for the avatar."""

    def __init__(self, transport, client: LiveKitTransportClient, params: LiveKitParams, **kwargs):
        super().__init__(transport, client, params, **kwargs)
        self._video_source: rtc.VideoSource | None = None
        self._video_track: rtc.LocalVideoTrack | None = None
        self._video_live = False  # flips True once the first frame is published
        # If video output isn't enabled we behave exactly like the stock transport
        # (no gating, no track) — keeps audio-only meetings unaffected.
        self._avatar_enabled = bool(params.video_out_enabled)
        # NOTE: per-frame video pacing is handled by the base output transport's
        # live-video handler (video_out_is_live=True paces every frame to
        # video_out_framerate on its own task). We deliberately do NOT add a second
        # per-frame pacer here — an earlier version did and it added event-loop
        # contention that starved the audio output queue (TTS stutter).
        # ── DROP-LATE backlog control (parity with avatar_video's max_backlog) ──
        # The base pacer drains its video queue at exactly `fps` and sleeps between
        # frames, so if SoulX BURSTS frames (slow GPU start, post-barge-in stale
        # frames, a >real-time catch-up) they pile up in that queue and the
        # animation tails SECONDS behind the audio (audio is sample-clocked and
        # never waits). avatar_video bounds this on its aiortc track; LiveKit has
        # no such track, but the same backlog lives on the base transport's
        # per-destination MediaSender video queue. A tiny watcher trims that queue
        # to `max_backlog` so video stays within ~max_backlog frames of real-time —
        # a brief visual catch-up instead of a multi-second lag. 0 = disabled.
        try:
            self._max_backlog = int(get_settings().meeting_video_max_backlog)
        except Exception:  # noqa: BLE001
            self._max_backlog = 2
        self._backlog_task: asyncio.Task | None = None
        self._dropped_total = 0
        self._dropped_logged = 0

    def _video_queue(self):
        """The base transport's live-video backlog queue (default destination),
        or None if not created yet. The queue lives on the per-destination
        MediaSender (`_media_senders[None]._video_queue`); we read it defensively
        so a pipecat layout change degrades to "no drop-late", never a crash."""
        try:
            sender = self._media_senders.get(None)  # type: ignore[attr-defined]
            return getattr(sender, "_video_queue", None)
        except Exception:  # noqa: BLE001
            return None

    def _ensure_backlog_watcher(self) -> None:
        """Start the drop-late watcher once the video queue exists. Idempotent."""
        if self._max_backlog <= 0 or self._backlog_task is not None:
            return
        if self._video_queue() is None:
            return
        self._backlog_task = self.create_task(self._drop_late_loop())

    async def _drop_late_loop(self) -> None:
        """Trim the base video queue to `max_backlog`, dropping the OLDEST frames.

        Runs every ~half-frame at 25fps (20ms). When SoulX over-delivers, the base
        pacer can't drain fast enough (it's fps-locked), so the queue grows and
        video lags; dropping the stale head keeps the pacer working on near-fresh
        frames. Audio is untouched (the master clock)."""
        try:
            while True:
                q = self._video_queue()
                if q is not None:
                    while q.qsize() > self._max_backlog:
                        try:
                            q.get_nowait()
                            q.task_done()
                            self._dropped_total += 1
                        except asyncio.QueueEmpty:
                            break
                    if self._dropped_total - self._dropped_logged >= 25:
                        log.debug(
                            "[meeting] dropped backlog avatar video frames to stay "
                            "real-time (audio is master clock)",
                            dropped=self._dropped_total, max_backlog=self._max_backlog,
                        )
                        self._dropped_logged = self._dropped_total
                await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            return
        except Exception as e:  # noqa: BLE001
            log.debug("[meeting] backlog watcher stopped", err=str(e))

    async def cleanup(self) -> None:
        """Cancel the drop-late watcher on teardown, then defer to the base."""
        if self._backlog_task is not None:
            try:
                await self.cancel_task(self._backlog_task)
            except Exception:  # noqa: BLE001
                pass
            self._backlog_task = None
        await super().cleanup()

    async def _ensure_video_track(self, width: int, height: int) -> bool:
        """Create + publish the agent's video track once. Idempotent.

        Returns True when the track is ready to receive frames."""
        if self._video_source is not None:
            return True
        # NOTE: client.room is a PROPERTY that raises if the room isn't created
        # yet — read the optional backing field (_room) directly so a pre-connect
        # frame just no-ops and the next frame retries, rather than throwing.
        room = getattr(self._client, "_room", None)
        if room is None or getattr(room, "local_participant", None) is None:
            return False  # not connected yet; the next frame retries
        try:
            self._video_source = rtc.VideoSource(width, height)
            self._video_track = rtc.LocalVideoTrack.create_video_track(
                "pipecat-avatar", self._video_source
            )
            options = rtc.TrackPublishOptions()
            options.source = rtc.TrackSource.SOURCE_CAMERA
            await room.local_participant.publish_track(self._video_track, options)
            log.info("[meeting] avatar video track published", size=f"{width}x{height}")
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("[meeting] avatar video track publish failed", err=str(e))
            self._video_source = None
            self._video_track = None
            return False

    async def write_video_frame(self, frame: OutputImageRawFrame) -> bool:
        """Publish one avatar frame to LiveKit. NO custom pacing here.

        The base output transport's live-video handler already paces every frame
        to `video_out_framerate` on its own `_video_task` BEFORE calling this
        (because `video_out_is_live=True`). An earlier version added a SECOND
        wall-clock `asyncio.sleep` pacer on top — a redundant pass that piled
        extra event-loop wakeups/contention on the loop and helped drain the
        audio output queue (contributing to TTS stutter). Removed: we now just
        publish the (already-paced, already-resized RGB24) frame, exactly like the
        avatar_video channel, which paces purely in its aiortc track — never on
        the pipecat output path."""
        if not self._avatar_enabled:
            return False
        # Start the drop-late watcher once (the base video queue now exists).
        self._ensure_backlog_watcher()
        w, h = frame.size
        if not await self._ensure_video_track(w, h):
            return False
        try:
            lk_frame = rtc.VideoFrame(width=w, height=h, type=_RGB24, data=frame.image)
            self._video_source.capture_frame(lk_frame)  # type: ignore[union-attr]
            if not self._video_live:
                self._video_live = True
                log.info("[meeting] avatar video is LIVE — audio gate opened")
            return True
        except Exception as e:  # noqa: BLE001
            log.debug("[meeting] capture_frame failed", err=str(e))
            return False

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        """Hold the agent's audio until the avatar video is actually streaming, so
        the candidate never hears a voice over a blank tile. Once the first video
        frame is published the gate opens and audio flows normally.

        When the avatar is disabled this is a straight pass-through (no gating)."""
        if self._avatar_enabled and not self._video_live:
            # Drop pre-video audio. Simli streams idle video on connect, so the
            # gate opens within ~1-2 s; the agent's opener is spoken right after.
            return True
        return await super().write_audio_frame(frame)


class AvatarLiveKitTransport(LiveKitTransport):
    """LiveKit transport whose output can publish the avatar's video track."""

    def output(self) -> LiveKitOutputTransport:
        if self._output is None:
            self._output = _AvatarLiveKitOutputTransport(
                self, self._client, self._params, name=self._output_name
            )
        return self._output


__all__ = ["AvatarLiveKitTransport"]
