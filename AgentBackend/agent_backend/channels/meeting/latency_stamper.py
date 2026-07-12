"""LatencyStamper — observe Pipecat frames flow by and emit LatencyEvents
(meeting channel, fully self-contained).

ISOLATION NOTE
--------------
Self-contained copy for the meeting channel (ported from avatar_video).
Imports only its sibling `events` module.

Insert two instances (pipeline.py wires them):
  INBOUND  side: stamps stt_first_interim, stt_final — after the STT stage
  OUTBOUND side: stamps brain_first_token, brain_total, tts_first_audio —
                 between the bridge and TTS

Per-turn timing state resets on TranscriptionFrame (final) — the new-turn
marker. Barge-in cancels in-flight turns; the next final starts a fresh turn
cleanly without leaked state.

LATENCY NOTE: this is observe-only and adds a single isinstance dispatch per
frame. It is gated OFF by default in production (MEETING_ENABLE_METRICS) — flip
on only when profiling the STT→brain→TTS→Simli round-trip.
"""
from __future__ import annotations

import time

from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from agent_backend.channels.meeting.events import EventBus, LatencyEvent
from agent_backend.infra import get_logger

log = get_logger(__name__)


class LatencyStamper(FrameProcessor):
    """One processor handles inbound + outbound timing for one meeting."""

    def __init__(self, *, bus: EventBus) -> None:
        super().__init__()
        self._bus = bus
        self._t_turn_start: float | None = None
        self._t_turn_final: float | None = None
        self._t_brain_first: float | None = None
        self._t_brain_start: float | None = None
        self._t_tts_first: float | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        now = time.monotonic()

        if isinstance(frame, InterimTranscriptionFrame):
            if self._t_turn_start is None:
                self._t_turn_start = now
                self._bus.publish(LatencyEvent(stage="stt_first_interim", ms=0.0))

        elif isinstance(frame, TranscriptionFrame):
            if self._t_turn_start is not None:
                self._bus.publish(LatencyEvent(
                    stage="stt_final",
                    ms=(now - self._t_turn_start) * 1000.0,
                ))
            self._t_turn_final = now
            self._t_brain_first = None
            self._t_brain_start = None
            self._t_tts_first = None

        elif isinstance(frame, LLMFullResponseStartFrame):
            self._t_brain_start = now

        elif isinstance(frame, TextFrame):
            if self._t_brain_first is None and self._t_brain_start is not None:
                self._t_brain_first = now
                if self._t_turn_final is not None:
                    self._bus.publish(LatencyEvent(
                        stage="brain_first_token",
                        ms=(now - self._t_turn_final) * 1000.0,
                    ))

        elif isinstance(frame, LLMFullResponseEndFrame):
            if self._t_brain_start is not None:
                self._bus.publish(LatencyEvent(
                    stage="brain_total",
                    ms=(now - self._t_brain_start) * 1000.0,
                ))

        elif isinstance(frame, TTSAudioRawFrame):
            if self._t_tts_first is None and self._t_brain_start is not None:
                self._t_tts_first = now
                self._bus.publish(LatencyEvent(
                    stage="tts_first_audio",
                    ms=(now - self._t_brain_start) * 1000.0,
                ))
                if self._t_turn_final is not None:
                    self._bus.publish(LatencyEvent(
                        stage="round_trip",
                        ms=(now - self._t_turn_final) * 1000.0,
                    ))

        await self.push_frame(frame, direction)


__all__ = ["LatencyStamper"]
