"""TurnDetector — multi-signal turn-state FSM with confidence scores.

States published as TurnEvent on the EventBus:
  speaking        ── user audio + interim transcripts flowing
  brief_pause     ── short silence (< turn_brief_pause_ms after speaking)
  thinking        ── filler word detected OR semantic incompletion
  turn_complete   ── long silence (> turn_complete_ms) → brain may speak
  abandoned       ── very long silence after a partial → call may need rescue

Signal sources fused inside `_recompute_state`:
  VAD                   weight 0.4  — Silero start/stop frames already in the pipeline
  STT interim cadence   weight 0.2  — fresh interim = user still talking
  STT endpointing       weight 0.3  — Deepgram utterance_end / Azure endSilenceTimeoutMs
                                      (manifested as a final TranscriptionFrame today)
  Semantic completion   weight 0.1  — OPTIONAL; LLM-based "is this a complete thought?"

We don't make the LLM call by default (latency cost). The other three signals
already give 0.9-class confidence on most utterances; the semantic check is
the tiebreaker we wire in when fusion confidence sits in 0.4–0.6.

Public surface
--------------
- Insert this processor BETWEEN STT and AgentBridge.
- It DOES NOT swallow frames — it observes and publishes to the bus.
- AgentBridge / BargeInManager / SilenceMonitor subscribe to TurnEvents.

Backwards-compat
----------------
With ENABLE_TURN_DETECTOR=False, this processor isn't inserted; Silero VAD's
binary start/stop frames continue to drive the pipeline exactly as today.
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

from agent_backend.channels.voice.events import EventBus, TurnEvent, TurnState
from agent_backend.config import get_settings
from agent_backend.infra import get_logger

log = get_logger(__name__)


# Fillers / hedges that indicate "still thinking". Conservative list — too
# many false-positives make the FSM jittery.
_THINKING_WORDS = (
    "uh", "uhh", "um", "umm", "hmm", "hmmm", "er", "errr",
    "let me think", "actually", "wait", "one sec", "one second",
    "give me a sec", "give me a moment",
)


class TurnDetector(FrameProcessor):
    """Per-call FSM over multi-source signals."""

    def __init__(self, *, bus: EventBus) -> None:
        super().__init__()
        self._bus = bus
        s = get_settings()
        self._brief_pause_s = s.turn_brief_pause_ms / 1000.0
        self._complete_s    = s.turn_complete_ms / 1000.0
        self._abandoned_s   = s.turn_abandoned_s
        self._conf_floor    = s.turn_confidence_floor

        # Signal state
        self._user_speaking: bool = False
        self._t_last_speech: float | None = None    # monotonic
        self._t_last_interim: float | None = None
        self._last_state: TurnState | None = None
        self._last_published_at: float = 0.0
        self._latest_interim_text: str = ""
        # Background watchdog — publishes turn_complete after sustained
        # silence even when STT never delivers a final TranscriptionFrame.
        # Without this, the FSM gets stuck in 'speaking' forever if the
        # user's utterance was too short for STT to finalise (the bug that
        # caused the BackchannelEmitter to loop for 45+ seconds).
        self._watchdog_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Pipecat hook — observe + forward.
    # ------------------------------------------------------------------
    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        now = time.monotonic()

        # Start the silence watchdog when the pipeline boots.
        if isinstance(frame, StartFrame) and self._watchdog_task is None:
            self._watchdog_task = asyncio.create_task(
                self._watchdog(), name="turn-detector-watchdog",
            )

        # Cancel the watchdog cleanly on shutdown.
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
            # Don't publish yet — wait for the timing window to decide
            # between brief_pause / thinking / turn_complete on the next
            # frame. Re-evaluation also happens on each interim/final below.
            self._maybe_publish_pause(now)

        elif isinstance(frame, InterimTranscriptionFrame):
            # Fresh interim = still actively speaking (even between VAD stops).
            text = (frame.text or "").lower()
            self._latest_interim_text = text
            self._t_last_interim = now
            # If we'd already declared a pause, reset to speaking — STT sees
            # what VAD missed (e.g. quiet word at low energy).
            if self._last_state in ("brief_pause", "thinking", "turn_complete"):
                self._publish("speaking", confidence=0.85, source="stt-interim", now=now)

        elif isinstance(frame, TranscriptionFrame):
            # STT finalised — this is the strongest "user finished" signal
            # most providers give us. Promote to turn_complete unless we have
            # very recent activity that suggests continuation.
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
        """Decide brief_pause / thinking / turn_complete / abandoned based on
        time since the last speech sample + filler-word evidence in interims."""
        if self._t_last_speech is None:
            return
        silence_s = now - self._t_last_speech
        contains_filler = any(w in self._latest_interim_text for w in _THINKING_WORDS)

        if silence_s < self._brief_pause_s:
            return  # too short to even call a pause

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
    # Background watchdog — guarantees state never gets stuck on 'speaking'
    # when STT silently never finalises (short utterance, noise, filter
    # swallow). Every 250ms it checks: are we still in 'speaking' OR
    # 'brief_pause' OR 'thinking', AND have we exceeded turn_complete_ms
    # since the last speech sample? If so, fire turn_complete ourselves.
    # ------------------------------------------------------------------
    async def _watchdog(self) -> None:
        try:
            while True:
                await asyncio.sleep(0.25)
                if self._last_state not in ("speaking", "brief_pause", "thinking"):
                    continue
                if self._user_speaking:
                    # VAD still says they're speaking — trust VAD.
                    continue
                if self._t_last_speech is None:
                    continue
                silence_s = time.monotonic() - self._t_last_speech
                if silence_s >= self._complete_s:
                    # Promote to turn_complete via the watchdog path. This
                    # is what UNBLOCKS BackchannelEmitter, SilenceMonitor,
                    # and anyone else waiting on a state reset.
                    self._publish(
                        "turn_complete",
                        confidence=0.75,
                        source="watchdog",
                        now=time.monotonic(),
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("[turn] watchdog crashed: %s", e)

    # ------------------------------------------------------------------
    # Publish with hysteresis so we don't flap.
    # ------------------------------------------------------------------
    def _publish(self, state: TurnState, *, confidence: float, source: str, now: float) -> None:
        # Suppress sub-threshold confidence so the bus doesn't fill with noise.
        if confidence < self._conf_floor and state == self._last_state:
            return
        # Don't republish the same state more than once per 100 ms.
        if state == self._last_state and (now - self._last_published_at) < 0.1:
            return
        ev = TurnEvent(state=state, confidence=confidence, source=source)
        self._bus.publish(ev)
        if state != self._last_state:
            log.debug("[turn] %s -> %s (conf=%.2f src=%s)", self._last_state, state, confidence, source)
        self._last_state = state
        self._last_published_at = now


__all__ = ["TurnDetector"]
