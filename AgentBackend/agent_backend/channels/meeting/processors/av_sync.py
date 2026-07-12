"""A/V sync via SOURCE-TIMESTAMP PASSTHROUGH (the correct, drift-free approach).

The problem with fabricated FPS
-------------------------------
aiortc's `VideoStreamTrack.next_timestamp()` does TWO things, both wrong for a
sourced avatar stream:

    self._timestamp += int(VIDEO_PTIME * VIDEO_CLOCK_RATE)   # +3000/frame == fake 30fps
    await asyncio.sleep(...)                                  # pace to that fake rate
    return self._timestamp, VIDEO_TIME_BASE                   # FABRICATED pts

Pipecat's `RawVideoTrack.recv()` calls it and OVERWRITES the frame's real pts.
aiortc then derives the RTP video timestamp from `frame.pts`. So the video RTP
clock advances at a fabricated FPS while the AUDIO RTP clock advances at the real
sample-count rate. Any mismatch between the fake FPS and Simli's true rate
accumulates into SECONDS of drift over an utterance. Forcing 30 → video ahead;
forcing 25 → video behind. No constant ever fixes it — it's the wrong axis.

The correct fix: passthrough
----------------------------
Simli's frames already carry their REAL presentation timestamps (the P2P transport
yields aiortc VideoFrames with RTP-derived pts at 90 kHz). We forward those
unchanged. Audio already rides real sample-count pts (pipecat's RawAudioTrack).
Once BOTH audio and video carry timestamps from the SAME source timeline, WebRTC's
built-in RTCP lip-sync keeps them aligned for the whole utterance — zero drift,
no FPS to tune. This is exactly what Simli's own LiveKit transport and aiortc's
RemoteStreamTrack passthrough do.

Implementation: we override the live track's `recv()` so it stamps each outgoing
VideoFrame with the REAL pts/time_base carried on the queued OutputImageRawFrame
(set by _consume_video from the Simli frame), and paces emission against that pts
rather than a fabricated FPS. No `next_timestamp()`, no FPS constant.
"""

from __future__ import annotations

import asyncio
import fractions
import time
from typing import Any

import numpy as np
from av.video.frame import VideoFrame

from agent_backend.infra import get_logger

log = get_logger(__name__)

# Default RTP video clock if a frame somehow lacks a time_base.
_DEFAULT_VIDEO_CLOCK_RATE = 90000
_DEFAULT_TIME_BASE = fractions.Fraction(1, _DEFAULT_VIDEO_CLOCK_RATE)
# metadata key used by _consume_video to carry the source time_base alongside pts.
TIME_BASE_META_KEY = "simli_time_base"


def enable_timestamp_passthrough(track: Any) -> bool:
    """Patch a pipecat RawVideoTrack instance to PRESERVE source timestamps.

    Replaces recv() so the outgoing VideoFrame carries the queued frame's real
    pts/time_base (from Simli) instead of aiortc's fabricated 30fps clock, and
    paces emission against that pts. Returns True if patched, False if the track
    shape is unexpected (caller then falls back to aiortc's stock behaviour).
    """
    if track is None or not hasattr(track, "_video_buffer") or not hasattr(track, "_width"):
        log.warning("[avatar-video] cannot enable timestamp passthrough — unexpected track")
        return False

    state: dict[str, float | None] = {"wall_start": None, "pts0": None}

    async def _recv() -> VideoFrame:
        raw = await track._video_buffer.get()  # OutputImageRawFrame

        arr = np.frombuffer(raw.image, dtype=np.uint8).reshape(
            (track._height, track._width, 3)
        )
        frame = VideoFrame.from_ndarray(arr, format="rgb24")

        # SOURCE TIMESTAMP PASSTHROUGH: use Simli's real pts/time_base if present.
        pts = getattr(raw, "pts", None)
        tb = None
        meta = getattr(raw, "metadata", None)
        if isinstance(meta, dict):
            tb = meta.get(TIME_BASE_META_KEY)
        if pts is not None and tb is not None:
            frame.pts = int(pts)
            frame.time_base = tb
            # Pace emission to the SOURCE clock so we neither burst nor starve —
            # but the SYNC comes from the pts itself, not this sleep.
            t = float(pts * tb)  # seconds since the source's first frame
            if state["wall_start"] is None:
                state["wall_start"] = time.time()
                state["pts0"] = t
            target = float(state["wall_start"]) + (t - float(state["pts0"]))  # type: ignore[arg-type]
            wait = target - time.time()
            if 0 < wait < 1.0:  # cap so a bad pts can never stall the track
                await asyncio.sleep(wait)
        else:
            # Fallback: no source timing on this frame — let aiortc clock it.
            frame.pts, frame.time_base = await track.next_timestamp()

        return frame

    track.recv = _recv  # type: ignore[attr-defined]
    log.info("[avatar-video] video timestamp passthrough enabled (no fabricated fps)")
    return True


# ---------------------------------------------------------------------------
# SoulX A/V sync — wall-clock RTP timestamps.
#
# SoulX frames arrive as JPEGs over a WebSocket and carry NO source pts, so the
# passthrough approach above has nothing to forward. Instead, derive the video
# RTP timestamp from actual elapsed wall-clock time since session start. The
# RTCP SR then accurately reflects the NTP→RTP mapping and Chrome's stream
# synchronizer applies zero correction → no drift. (See the SoulX renderer.)
# ---------------------------------------------------------------------------

_VIDEO_CLOCK_RATE = 90000
_VIDEO_TIME_BASE  = fractions.Fraction(1, _VIDEO_CLOCK_RATE)


def pace_and_resync_video_track(track: Any, fps: int) -> tuple[bool, None]:
    """Patch a pipecat RawVideoTrack to use wall-clock-derived RTP timestamps.

    Timestamps advance proportionally to actual elapsed time (not sequential
    integers), so the RTCP SR NTP→RTP mapping is always accurate.  Chrome's
    stream synchronizer sees no mismatch and adds zero video playout delay.

    Used for the SoulX renderer (pts-less frames); Simli uses
    enable_timestamp_passthrough() instead. Returns (True, None) on success.
    """
    if track is None or not hasattr(track, "recv"):
        log.warning("[avatar-soulx] cannot pace video track — unexpected track", fps=fps)
        return False, None

    state: dict = {"start_time": None, "last_ts": 0}

    async def _next_timestamp() -> tuple[int, fractions.Fraction]:
        now = asyncio.get_event_loop().time()
        if state["start_time"] is None:
            # Anchor wall-clock origin on the very first frame.
            state["start_time"] = now
            state["last_ts"] = 0
            return 0, _VIDEO_TIME_BASE
        elapsed_s = now - state["start_time"]
        ts = int(elapsed_s * _VIDEO_CLOCK_RATE)
        # Guarantee strict monotonicity (guards against sub-ms rapid calls).
        ts = max(ts, state["last_ts"] + 1)
        state["last_ts"] = ts
        return ts, _VIDEO_TIME_BASE

    track.next_timestamp = _next_timestamp  # type: ignore[attr-defined]
    log.info("[avatar-video] video track using wall-clock RTP timestamps", fps=fps)
    return True, None
