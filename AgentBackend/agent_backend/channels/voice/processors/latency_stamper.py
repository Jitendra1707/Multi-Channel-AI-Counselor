"""LatencyStamper — observe Pipecat frames flow by and emit LatencyEvents.

Where to insert
---------------
Two instances per pipeline (composer wires them):

  INBOUND  side:  stamps `t_stt_first_interim`, `t_stt_final` — placed after STT
  OUTBOUND side:  stamps `t_brain_first_token`, `t_brain_total`, `t_tts_first_audio`
                   — placed BETWEEN AgentBridge and TTS, and again AFTER TTS

We don't try to write one giant cross-cutting wrapper around the pipeline;
two FrameProcessor instances at the right insertion points give us all the
timing we need with zero ambiguity about which frame triggered which stamp.

Cancellation
------------
Per-turn timing state is reset on TranscriptionFrame (final) — that's the
"start of a new turn" marker. Barge-in cancels in-flight turns; the next
TranscriptionFrame starts a fresh turn cleanly without leaked state.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

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

from agent_backend.channels.voice.events import EventBus, LatencyEvent
from agent_backend.infra import get_logger

if TYPE_CHECKING:
    pass

log = get_logger(__name__)


class LatencyStamper(FrameProcessor):
    """One processor handles inbound + outbound timing for one call.

    Tracks per-turn timestamps in a small dict, publishes `LatencyEvent`s to
    the bus when each stage's "first" frame is observed. Resets on each new
    TranscriptionFrame (final) so per-turn measurements don't leak.
    """

    def __init__(self, *, bus: EventBus) -> None:
        super().__init__()
        self._bus = bus
        # Per-turn timestamps. Reset on each final transcription.
        self._t_turn_start: float | None = None        # first interim
        self._t_turn_final: float | None = None        # final
        self._t_brain_first: float | None = None       # first text frame after brain begins
        self._t_brain_start: float | None = None       # LLMFullResponseStartFrame
        self._t_tts_first: float | None = None         # first TTSAudioRawFrame after brain start

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        now = time.monotonic()

        # STT interim — first one in a new turn marks "user spoke first"
        if isinstance(frame, InterimTranscriptionFrame):
            if self._t_turn_start is None:
                self._t_turn_start = now
                self._bus.publish(LatencyEvent(stage="stt_first_interim", ms=0.0))

        # STT final — end-of-user-turn; start the brain-side clocks
        elif isinstance(frame, TranscriptionFrame):
            if self._t_turn_start is not None:
                self._bus.publish(LatencyEvent(
                    stage="stt_final",
                    ms=(now - self._t_turn_start) * 1000.0,
                ))
            self._t_turn_final = now
            # Reset the brain/TTS stamps for the new turn.
            self._t_brain_first = None
            self._t_brain_start = None
            self._t_tts_first = None

        # Brain started (AgentBridge pushes this before yielding tokens)
        elif isinstance(frame, LLMFullResponseStartFrame):
            self._t_brain_start = now

        # Brain produced first token (TextFrame from the bridge)
        elif isinstance(frame, TextFrame):
            if self._t_brain_first is None and self._t_brain_start is not None:
                self._t_brain_first = now
                if self._t_turn_final is not None:
                    self._bus.publish(LatencyEvent(
                        stage="brain_first_token",
                        ms=(now - self._t_turn_final) * 1000.0,
                    ))

        # Brain stream ended
        elif isinstance(frame, LLMFullResponseEndFrame):
            if self._t_brain_start is not None:
                self._bus.publish(LatencyEvent(
                    stage="brain_total",
                    ms=(now - self._t_brain_start) * 1000.0,
                ))

        # First TTS audio out — closes the loop on round-trip
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

        # Always forward — this processor is observe-only.
        await self.push_frame(frame, direction)


__all__ = ["LatencyStamper"]
