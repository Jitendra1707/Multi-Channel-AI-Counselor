"""TurnDetector — multi-signal turn-state FSM with confidence scores
(avatar-video channel, fully isolated).

ISOLATION NOTE
--------------
Self-contained copy for avatar_video. Imports only its sibling `events` module
and config — nothing from `channels.voice` / `channels.pipecat`.

States published as TurnEvent on the EventBus:
  speaking        ── user audio + interim transcripts flowing
  brief_pause     ── short silence (< turn_brief_pause_ms after speaking)
  thinking        ── filler word detected OR semantic incompletion
  turn_complete   ── long silence (> turn_complete_ms) → brain may speak
  abandoned       ── very long silence after a partial

Signal sources fused:
  VAD                   — Silero start/stop frames already on the transport
  STT interim cadence   — fresh interim = user still talking
  STT endpointing       — final TranscriptionFrame = strongest "finished" signal
  Background watchdog    — guarantees state never gets stuck on 'speaking' when
                          STT silently never finalises a short utterance.

Public surface
--------------
- Insert this processor BETWEEN STT (after InputAudioSink) and BargeInManager.
- It DOES NOT swallow frames — it observes and publishes to the bus.
- BargeInManager / SilenceMonitor subscribe to TurnEvents.
"""
from __future__ import annotations

import asyncio
import time

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterimTranscriptionFrame,
    StartFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from agent_backend.channels.avatar_video.events import EventBus, TurnEvent, TurnState
from agent_backend.config import get_settings
from agent_backend.infra import get_logger

log = get_logger(__name__)


# Fillers / hedges that indicate "still thinking". Conservative list.
_THINKING_WORDS = (
    "uh", "uhh", "um", "umm", "hmm", "hmmm", "er", "errr",
    "let me think", "actually", "wait", "one sec", "one second",
    "give me a sec", "give me a moment",
)


class TurnDetector(FrameProcessor):
    """Per-session FSM over multi-source signals."""

    def __init__(self, *, bus: EventBus) -> None:
        super().__init__()
        self._bus = bus
        s = get_settings()
        # Avatar-prefixed tunables so they're independent of the voice channel.
        self._brief_pause_s = s.avatar_turn_brief_pause_ms / 1000.0
        self._complete_s    = s.avatar_turn_complete_ms / 1000.0
        self._abandoned_s   = s.avatar_turn_abandoned_s
        self._conf_floor    = s.avatar_turn_confidence_floor

        self._user_speaking: bool = False
        self._t_last_speech: float | None = None
        self._t_last_interim: float | None = None
        self._last_state: TurnState | None = None
        self._last_published_at: float = 0.0
        self._latest_interim_text: str = ""
        self._watchdog_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Pipecat hook — observe + forward.
    # ------------------------------------------------------------------
    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        now = time.monotonic()

        if isinstance(frame, StartFrame) and self._watchdog_task is None:
            self._watchdog_task = asyncio.create_task(
                self._watchdog(), name="avatar-turn-watchdog",
            )

        if isinstance(frame, (CancelFrame, EndFrame)) and self._watchdog_task:
            if not self._watchdog_task.done():
                self._watchdog_task.cancel()

        if isinstance(frame, UserStartedSpeakingFrame):
            self._user_speaking = True
            self._t_last_speech = now
            self._publish("speaking", confidence=0.95, source="vad", now=now)

        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._user_speaking = False
            self._t_last_speech = now
            self._maybe_publish_pause(now)

        elif isinstance(frame, InterimTranscriptionFrame):
            text = (frame.text or "").lower()
            self._latest_interim_text = text
            self._t_last_interim = now
            if self._last_state in ("brief_pause", "thinking", "turn_complete"):
                self._publish("speaking", confidence=0.85, source="stt-interim", now=now)

        elif isinstance(frame, TranscriptionFrame):
            text_lc = (frame.text or "").lower().strip()
            ends_thinking = any(text_lc.endswith(w) for w in _THINKING_WORDS)
            if ends_thinking:
                self._publish("thinking", confidence=0.80, source="stt-final-filler", now=now)
            else:
                self._publish("turn_complete", confidence=0.92, source="stt-final", now=now)

        # Always forward — observation-only processor.
        await self.push_frame(frame, direction)

    # ------------------------------------------------------------------
    # Pause-state decision after VAD stop.
    # ------------------------------------------------------------------
    def _maybe_publish_pause(self, now: float) -> None:
        if self._t_last_speech is None:
            return
        silence_s = now - self._t_last_speech
        contains_filler = any(w in self._latest_interim_text for w in _THINKING_WORDS)

        if silence_s < self._brief_pause_s:
            return

        if silence_s < self._complete_s:
            state: TurnState = "thinking" if contains_filler else "brief_pause"
            conf = 0.65 if contains_filler else 0.55
            self._publish(state, confidence=conf, source="fusion", now=now)
            return

        if silence_s < self._abandoned_s:
            self._publish("turn_complete", confidence=0.85, source="fusion", now=now)
            return

        self._publish("abandoned", confidence=0.95, source="fusion", now=now)

    # ------------------------------------------------------------------
    # Background watchdog — never let state stick on 'speaking' when STT
    # silently never finalises (short utterance / noise swallow).
    # ------------------------------------------------------------------
    async def _watchdog(self) -> None:
        try:
            while True:
                await asyncio.sleep(0.25)
                if self._last_state not in ("speaking", "brief_pause", "thinking"):
                    continue
                if self._user_speaking:
                    continue
                if self._t_last_speech is None:
                    continue
                silence_s = time.monotonic() - self._t_last_speech
                if silence_s >= self._complete_s:
                    self._publish(
                        "turn_complete",
                        confidence=0.75,
                        source="watchdog",
                        now=time.monotonic(),
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("[avatar-turn] watchdog crashed: %s", e)

    # ------------------------------------------------------------------
    # Publish with hysteresis so we don't flap.
    # ------------------------------------------------------------------
    def _publish(self, state: TurnState, *, confidence: float, source: str, now: float) -> None:
        if confidence < self._conf_floor and state == self._last_state:
            return
        if state == self._last_state and (now - self._last_published_at) < 0.1:
            return
        self._bus.publish(TurnEvent(state=state, confidence=confidence, source=source))
        if state != self._last_state:
            log.debug("[avatar-turn] %s -> %s (conf=%.2f src=%s)", self._last_state, state, confidence, source)
        self._last_state = state
        self._last_published_at = now


__all__ = ["TurnDetector"]
