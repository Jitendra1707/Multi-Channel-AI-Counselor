"""BargeInManager — turns BargeClassifier decisions into pipeline actions.

Where it sits
-------------
UPSTREAM of AgentBridge (between TurnDetector and AgentBridge). That position
lets the manager SUPPRESS frames before the brain ever sees them when the
classifier decides the user's input is an ack / answer / ambiguous (and the
bot should keep speaking).

Human-like interruption model
------------------------------
The hard rule learned from live calls: **never answer a half-sentence.** STT
emits growing interims ("you can" → "you can tell me the" → "you can tell me
the fees"). If we cancel the bot AND run the brain on an interim, the brain
answers a fragment and says "could you clarify?". So we split the two actions:

  - STOP fast (cancel TTS + cancel the in-flight brain) the moment the
    classifier is confident the user is really interrupting.
  - ANSWER only on the FULL final transcript — we hold in `awaiting_final`
    after stopping, and forward the FINAL (the complete question) to the brain.

State machine
-------------
    idle ── BotStartedSpeaking ──► bot_speaking
                                      │ UserStartedSpeaking + acoustic gate
                                      ▼
                                   holding ──(classify each transcript)──►
                                      ├─ ACK / ANSWER          → suppress, keep speaking
                                      ├─ AMBIGUOUS (interim)   → keep speaking, wait for more
                                      ├─ INTERRUPT/CONFUSED on FINAL → stop + forward THIS final
                                      └─ INTERRUPT/CONFUSED on INTERIM → stop now, → awaiting_final
                                   awaiting_final ──► forward the FINAL question to the brain
                                                      (fallback: best interim after a timeout)

Key invariants
--------------
- ACK / ANSWER / AMBIGUOUS: bot's TTS is NEVER cancelled; no fragment reaches
  the brain.
- INTERRUPT / CONFUSED: TTS cancelled immediately; the brain runs on the
  COMPLETE final utterance (never a partial). No "[CONFUSED]" tag is injected —
  that made the bot apologise in loops; the brain just answers the real
  question / re-explains from context.
- No async LLM on the critical path — the heuristic decides on the full final.
"""
from __future__ import annotations

import asyncio
import time

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from agent_backend.channels.voice.events import BargeInEvent, EventBus
from agent_backend.channels.voice.processors.barge_classifier import (
    BargeClassifier,
    BargeIntent,
)
from agent_backend.infra import get_logger

log = get_logger(__name__)


# In 'holding', fall back to ACK only after this much QUIET (no transcript
# activity) — measured from the last interim, so a long question that keeps
# producing interims is never cut off to ACK mid-sentence.
_HOLDING_TIMEOUT_S: float = 2.0

# After we STOP the bot on an interim interrupt, wait at most this long for the
# FINAL transcript before answering with the best interim we saw (safety net so
# we never hang silently if STT doesn't deliver a distinct final).
_AWAITING_FINAL_TIMEOUT_S: float = 3.0


class BargeInManager(FrameProcessor):
    """Per-call barge-in state machine driven by `BargeClassifier`."""

    def __init__(self, *, session, bus: EventBus) -> None:
        super().__init__()
        self._session = session
        self._bus = bus
        self._classifier = BargeClassifier(
            session=session, conversation_id=session.conversation_id
        )

        self._state: str = "idle"  # idle | bot_speaking | holding | awaiting_final | cancelling
        self._t_user_started: float | None = None
        self._last_activity: float = 0.0
        self._tts_rms_peak: float = 0.0
        self._user_rms_at_start: float = 0.0
        self._held_user_frame: UserStartedSpeakingFrame | None = None
        self._held_user_stopped_frame: UserStoppedSpeakingFrame | None = None
        self._decided: bool = False
        # Best (longest / latest) transcript text seen for the current utterance.
        # Used as the fallback question if STT never delivers a distinct final.
        self._best_interim: str = ""
        # Background timer for the awaiting_final fallback.
        self._awaiting_task: asyncio.Task | None = None
        # Per-utterance idempotency: once we've classified the utterance, later
        # fragments (interim OR final) for the SAME utterance are ignored.
        # Cleared only by a fresh UserStartedSpeakingFrame.
        self._utterance_intent: BargeIntent | None = None

    # ------------------------------------------------------------------
    # Pipecat hook
    # ------------------------------------------------------------------
    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        # --- bot speaking lifecycle ------------------------------------
        if isinstance(frame, BotStartedSpeakingFrame):
            self._state = "bot_speaking"
            self._tts_rms_peak = 0.0
            self._decided = False
            self._cancel_awaiting()
            self._best_interim = ""
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, BotStoppedSpeakingFrame):
            # Bot finished naturally — nothing to interrupt. Discard holding.
            self._state = "idle"
            self._held_user_frame = None
            self._held_user_stopped_frame = None
            self._cancel_awaiting()
            self._best_interim = ""
            await self.push_frame(frame, direction)
            return

        # --- track TTS RMS peak (for the acoustic echo check) ----------
        if isinstance(frame, TTSAudioRawFrame):
            rms = _rms(frame.audio)
            if rms > self._tts_rms_peak:
                self._tts_rms_peak = rms
            await self.push_frame(frame, direction)
            return

        # --- snapshot user RMS just before VAD trips -------------------
        if isinstance(frame, InputAudioRawFrame) and self._state == "bot_speaking":
            self._user_rms_at_start = _rms(frame.audio)
            await self.push_frame(frame, direction)
            return

        # --- Pipecat auto-InterruptionFrame suppression ----------------
        # With allow_interruptions=True, the input transport AUTO-EMITS an
        # InterruptionFrame on every VAD trip. While we're still evaluating a
        # barge (bot_speaking / holding / awaiting_final), swallow it — we push
        # our OWN InterruptionFrame in `_fire_stop()` when we confirm a barge.
        if isinstance(frame, InterruptionFrame):
            if self._state in ("bot_speaking", "holding", "awaiting_final"):
                return
            await self.push_frame(frame, direction)
            return

        # --- VAD trip while bot speaking -------------------------------
        if isinstance(frame, UserStartedSpeakingFrame):
            # New utterance — reset per-utterance state.
            self._utterance_intent = None
            # If we were waiting for a final from a previous interim-interrupt,
            # this fresh speech supersedes it.
            if self._state == "awaiting_final":
                self._cancel_awaiting()

            if self._state != "bot_speaking":
                await self.push_frame(frame, direction)
                return

            # Acoustic gate — reject obvious noise / echo / glitches.
            gate_pass = self._classifier.acoustic_gate_passed(
                user_rms=self._user_rms_at_start,
                tts_peak_rms=self._tts_rms_peak,
                user_duration_ms=500,  # provisional; transcript phase reconsiders
            )
            if not gate_pass:
                self._bus.publish(BargeInEvent(phase="rejected"))
                log.info(
                    "[barge] acoustic-gate rejected",
                    user_rms=self._user_rms_at_start,
                    tts_peak=self._tts_rms_peak,
                )
                return  # SUPPRESS — bot keeps speaking

            # Enter holding. Hold the frame; the transcript decides whether to
            # release it (INTERRUPT) or discard it (ACK).
            self._state = "holding"
            self._t_user_started = time.monotonic()
            self._last_activity = self._t_user_started
            self._held_user_frame = frame
            self._held_user_stopped_frame = None
            self._decided = False
            self._best_interim = ""
            self._bus.publish(BargeInEvent(phase="detected"))
            return

        # --- already classified this utterance → suppress dupes --------
        if (
            isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame))
            and self._utterance_intent is not None
        ):
            log.debug(
                "[barge] suppressing redundant transcript "
                "(utterance already classified as %s): %r",
                self._utterance_intent.value, (frame.text or "")[:80],
            )
            return

        # --- awaiting_final: we already STOPPED on an interim; now wait
        #     for the FULL final question and forward THAT to the brain ----
        if self._state == "awaiting_final" and isinstance(
            frame, (InterimTranscriptionFrame, TranscriptionFrame)
        ):
            txt = (frame.text or "").strip()
            if txt:
                self._best_interim = txt
            if isinstance(frame, TranscriptionFrame):
                # The complete question landed — answer on the FULL text.
                await self._forward_question(direction, txt or self._best_interim)
            # interims while awaiting are absorbed (AgentBridge ignores them
            # anyway, and we don't want to answer a fragment).
            return

        # --- VAD stop --------------------------------------------------
        if isinstance(frame, UserStoppedSpeakingFrame):
            if self._state == "holding":
                self._held_user_stopped_frame = frame
                return
            if self._state == "awaiting_final":
                # absorb — the final transcript will follow / the fallback fires
                return

        # --- transcript while holding → classify -----------------------
        if (
            isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame))
            and self._state == "holding"
            and not self._decided
        ):
            transcript = (frame.text or "").strip()
            is_final = isinstance(frame, TranscriptionFrame)
            if transcript:
                self._best_interim = transcript
            self._last_activity = time.monotonic()
            duration_ms = int(
                (time.monotonic() - (self._t_user_started or time.monotonic())) * 1000
            )

            intent = self._classifier.classify(
                transcript=transcript, user_duration_ms=duration_ms,
            )
            log.info(
                "[barge] heuristic intent=%s final=%s transcript=%r duration_ms=%d",
                intent.value, is_final, transcript, duration_ms,
            )

            if intent == BargeIntent.ACK:
                self._handle_ack(transcript)
                return
            if intent == BargeIntent.ANSWER:
                self._handle_answer(transcript)
                return
            if intent in (BargeIntent.INTERRUPT, BargeIntent.CONFUSED):
                if is_final:
                    # We already have the complete utterance — stop + answer it.
                    await self._fire_and_answer(direction, transcript)
                else:
                    # Interim interrupt — STOP the bot now (responsive), then
                    # wait for the FULL final before answering.
                    await self._fire_stop(direction)
                    self._state = "awaiting_final"
                    self._schedule_awaiting_fallback(direction)
                return
            # AMBIGUOUS:
            if is_final:
                # A complete sentence we couldn't bucket. If it carries content
                # (2+ words), treat it as a real turn and answer it; otherwise
                # it's listener noise → keep speaking.
                if len(transcript.split()) >= 2:
                    await self._fire_and_answer(direction, transcript)
                else:
                    self._handle_ack(transcript)
                return
            # AMBIGUOUS interim → keep speaking, stay holding, wait for the final.
            return

        # --- holding QUIET timeout → ACK (no transcript activity) ------
        if (
            self._state == "holding"
            and not self._decided
            and self._t_user_started is not None
            and (time.monotonic() - self._last_activity) > _HOLDING_TIMEOUT_S
        ):
            log.info(
                "[barge] holding quiet timeout — assuming ACK (no transcript in "
                "%.1fs; bot keeps speaking)",
                _HOLDING_TIMEOUT_S,
            )
            self._handle_ack("")
            # fall through to forward the current frame

        # --- everything else passes through unchanged ------------------
        await self.push_frame(frame, direction)

    # ------------------------------------------------------------------
    # Intent handlers
    # ------------------------------------------------------------------
    def _handle_ack(self, transcript: str) -> None:
        """ACK — bot keeps speaking. Record transcript so the brain knows the
        user is engaged on the next real turn."""
        self._record_user_message(transcript)
        self._utterance_intent = BargeIntent.ACK
        self._bus.publish(BargeInEvent(phase="rejected"))
        self._reset_holding(to_state="bot_speaking")

    def _handle_answer(self, transcript: str) -> None:
        """ANSWER — bot asked a question; user answered "yes/no". Don't cancel
        mid-question; record the answer; brain sees it next turn."""
        self._record_user_message(transcript)
        self._utterance_intent = BargeIntent.ANSWER
        self._bus.publish(BargeInEvent(phase="rejected"))
        self._reset_holding(to_state="bot_speaking")

    async def _fire_stop(self, direction: FrameDirection) -> None:
        """STOP the bot: cancel TTS + release the held UserStartedSpeakingFrame
        so AgentBridge cancels its in-flight brain task. Does NOT answer."""
        self._decided = True
        self._bus.publish(BargeInEvent(phase="confirmed", bot_said_partial=None))
        # 1. Cancel currently-playing bot audio.
        await self.push_frame(InterruptionFrame(), direction)
        # 2. Release held user-speaking frames so AgentBridge cancels the brain.
        if self._held_user_frame is not None:
            await self.push_frame(self._held_user_frame, direction)
            self._held_user_frame = None
        if self._held_user_stopped_frame is not None:
            await self.push_frame(self._held_user_stopped_frame, direction)
            self._held_user_stopped_frame = None

    async def _fire_and_answer(self, direction: FrameDirection, transcript: str) -> None:
        """Full barge on a FINAL transcript: stop, then forward the complete
        question so the brain answers it."""
        await self._fire_stop(direction)
        self._utterance_intent = BargeIntent.INTERRUPT
        self._state = "cancelling"
        if transcript:
            await self.push_frame(
                TranscriptionFrame(text=transcript, user_id="user", timestamp=""),
                direction,
            )

    async def _forward_question(self, direction: FrameDirection, transcript: str) -> None:
        """From awaiting_final: forward the FULL final question to the brain."""
        self._cancel_awaiting()
        self._utterance_intent = BargeIntent.INTERRUPT
        self._state = "cancelling"
        if transcript:
            await self.push_frame(
                TranscriptionFrame(text=transcript, user_id="user", timestamp=""),
                direction,
            )

    # ------------------------------------------------------------------
    # awaiting_final fallback timer
    # ------------------------------------------------------------------
    def _schedule_awaiting_fallback(self, direction: FrameDirection) -> None:
        self._cancel_awaiting()
        self._awaiting_task = asyncio.create_task(
            self._awaiting_fallback(direction),
            name=f"barge-awaitfinal-{self._session.short()}",
        )

    async def _awaiting_fallback(self, direction: FrameDirection) -> None:
        try:
            await asyncio.sleep(_AWAITING_FINAL_TIMEOUT_S)
        except asyncio.CancelledError:
            return
        if self._state == "awaiting_final" and self._best_interim:
            log.info(
                "[barge] no final after stop — answering best interim %r",
                self._best_interim[:80],
            )
            await self._forward_question(direction, self._best_interim)

    def _cancel_awaiting(self) -> None:
        if self._awaiting_task and not self._awaiting_task.done():
            self._awaiting_task.cancel()
        self._awaiting_task = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _reset_holding(self, *, to_state: str) -> None:
        self._state = to_state
        self._t_user_started = None
        self._held_user_frame = None
        self._held_user_stopped_frame = None
        self._decided = False

    def _record_user_message(self, text: str) -> None:
        if not text:
            return
        try:
            from agent_backend.llm_agent.conversation import get_conversation
            get_conversation(self._session.conversation_id).append_user(text)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Tiny RMS helper — same as before; kept local to avoid a public API.
# ---------------------------------------------------------------------------
def _rms(audio: bytes) -> float:
    """Root-mean-square of int16 PCM little-endian audio."""
    if not audio:
        return 0.0
    try:
        import audioop
        return float(audioop.rms(audio, 2))
    except Exception:  # noqa: BLE001
        return 0.0


__all__ = ["BargeInManager"]
