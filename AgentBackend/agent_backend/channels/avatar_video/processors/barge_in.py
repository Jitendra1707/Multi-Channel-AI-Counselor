"""BargeInManager — turns BargeClassifier decisions into pipeline actions
(avatar-video channel, fully isolated).

ISOLATION NOTE
--------------
Self-contained copy for avatar_video. Imports only its sibling `events` +
`barge_classifier` modules. Nothing from `channels.voice` / `channels.pipecat`.

Where it sits
-------------
UPSTREAM of AgentBridge (between TurnDetector and AgentBridge). That position
lets the manager SUPPRESS frames before the brain ever sees them when the
classifier decides the user's input is an ack / answer / ambiguous (avatar
keeps speaking).

Human-like interruption model
------------------------------
The hard rule from live calls: **never answer a half-sentence.** STT emits
growing interims ("you can" → "you can tell me the fees"). So we split actions:

  - STOP fast (cancel TTS + cancel the in-flight brain) the moment the
    classifier is confident the user is really interrupting.
  - ANSWER only on the FULL final transcript — we hold in `awaiting_final`
    after stopping, then forward the FINAL (complete question) to the brain.

AVATAR-SPECIFIC: the InterruptionFrame this manager emits flows downstream
THROUGH the AgentBridge and TTS and reaches AgentSimliVideoService, whose
handler calls Simli `clearBuffer()` — so a confirmed barge actually stops the
avatar's lip-synced speech. Because the avatar pipeline drops raw input audio
(InputAudioSink) and the browser does AEC, the acoustic echo-ratio check is
usually a no-op (RMS unavailable); the bot-speaking grace window below + the
transcript heuristic carry the load instead.

State machine
-------------
    idle ── BotStartedSpeaking ──► bot_speaking
                                      │ UserStartedSpeaking + acoustic gate
                                      ▼
                                   holding ──(classify each transcript)──►
                                      ├─ ACK / ANSWER          → suppress, keep speaking
                                      ├─ AMBIGUOUS (interim)   → keep speaking, wait
                                      ├─ INTERRUPT/CONFUSED FINAL  → stop + forward final
                                      └─ INTERRUPT/CONFUSED INTERIM → stop now → awaiting_final
                                   awaiting_final ──► forward FINAL to brain
                                                      (fallback: best interim after timeout)
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

from agent_backend.channels.avatar_video.events import BargeInEvent, EventBus
from agent_backend.channels.avatar_video.processors.barge_classifier import (
    BargeClassifier,
    BargeIntent,
)
from agent_backend.infra import get_logger

log = get_logger(__name__)


# Fall back to ACK after this much QUIET (no transcript activity) in 'holding',
# measured from the last interim — a long question that keeps producing interims
# is never cut to ACK mid-sentence.
_HOLDING_TIMEOUT_S: float = 2.0

# After we STOP on an interim interrupt, wait at most this long for the FINAL
# before answering with the best interim (safety net so we never hang silently).
_AWAITING_FINAL_TIMEOUT_S: float = 3.0

# When an ACK/ANSWER arrives as a *final* (e.g. tight STT segmentation splits
# "okay, what about fees" into TWO finals: "okay" then "what about fees"), we do
# NOT commit the ACK immediately. We hold this brief grace window for a
# continuation final; if one arrives we re-classify the COMBINED text (which
# flips to INTERRUPT for a real question). If nothing comes, THEN we commit ACK.
# This is the "wait a beat before assuming a bare 'okay' was just listening"
# behaviour a human has. It adds ZERO latency to real interrupts (those still
# fire immediately) and none to the bot's speech (it keeps talking during grace).
_ACK_GRACE_S: float = 0.6


class BargeInManager(FrameProcessor):
    """Per-session barge-in state machine driven by `BargeClassifier`."""

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
        self._best_interim: str = ""
        self._awaiting_task: asyncio.Task | None = None
        self._utterance_intent: BargeIntent | None = None
        # ACK-grace: text of a pending (uncommitted) ack-final + its timer.
        # While set, we are holding to see if a continuation final arrives so we
        # can reclassify the combined utterance instead of committing the ACK.
        self._pending_ack_text: str = ""
        self._ack_grace_task: asyncio.Task | None = None

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
            self._cancel_ack_grace()
            self._pending_ack_text = ""
            self._best_interim = ""
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, BotStoppedSpeakingFrame):
            self._state = "idle"
            self._held_user_frame = None
            self._held_user_stopped_frame = None
            self._cancel_awaiting()
            self._cancel_ack_grace()
            self._pending_ack_text = ""
            self._best_interim = ""
            await self.push_frame(frame, direction)
            return

        # --- track TTS RMS peak (for the acoustic echo check, when available)
        if isinstance(frame, TTSAudioRawFrame):
            rms = _rms(frame.audio)
            if rms > self._tts_rms_peak:
                self._tts_rms_peak = rms
            await self.push_frame(frame, direction)
            return

        # --- snapshot user RMS just before VAD trips -------------------
        # In the avatar pipeline raw input audio is usually dropped upstream
        # (InputAudioSink), so this rarely fires — the gate degrades gracefully.
        if isinstance(frame, InputAudioRawFrame) and self._state == "bot_speaking":
            self._user_rms_at_start = _rms(frame.audio)
            await self.push_frame(frame, direction)
            return

        # --- Pipecat auto-InterruptionFrame suppression ----------------
        # While we're still evaluating a barge, swallow the framework's auto
        # InterruptionFrame — we push our OWN in `_fire_stop()` on a confirmed
        # barge (which is what reaches Simli's clearBuffer()).
        if isinstance(frame, InterruptionFrame):
            if self._state in ("bot_speaking", "holding", "awaiting_final"):
                return
            await self.push_frame(frame, direction)
            return

        # --- VAD trip while bot speaking -------------------------------
        if isinstance(frame, UserStartedSpeakingFrame):
            self._utterance_intent = None
            if self._state == "awaiting_final":
                self._cancel_awaiting()

            if self._state != "bot_speaking":
                await self.push_frame(frame, direction)
                return

            gate_pass = self._classifier.acoustic_gate_passed(
                user_rms=self._user_rms_at_start,
                tts_peak_rms=self._tts_rms_peak,
                user_duration_ms=500,  # provisional; transcript phase reconsiders
            )
            if not gate_pass:
                self._bus.publish(BargeInEvent(phase="rejected"))
                log.info(
                    "[avatar-barge] acoustic-gate rejected",
                    user_rms=self._user_rms_at_start,
                    tts_peak=self._tts_rms_peak,
                )
                return  # SUPPRESS — avatar keeps speaking

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
                "[avatar-barge] suppressing redundant transcript "
                "(utterance already classified as %s): %r",
                self._utterance_intent.value, (frame.text or "")[:80],
            )
            return

        # --- awaiting_final: forward the FULL final to the brain -------
        if self._state == "awaiting_final" and isinstance(
            frame, (InterimTranscriptionFrame, TranscriptionFrame)
        ):
            txt = (frame.text or "").strip()
            if txt:
                self._best_interim = txt
            if isinstance(frame, TranscriptionFrame):
                await self._forward_question(direction, txt or self._best_interim)
            return

        # --- VAD stop --------------------------------------------------
        if isinstance(frame, UserStoppedSpeakingFrame):
            if self._state == "holding":
                self._held_user_stopped_frame = frame
                return
            if self._state == "awaiting_final":
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

            # If we're inside the ACK grace window (a bare ack-final like "okay"
            # just landed and we're waiting a beat), CONCATENATE this new text
            # with the pending ack and classify the COMBINED utterance. That's
            # how "okay" + "what about the fees" becomes a single INTERRUPT
            # instead of an ACK that swallows the question. The grace timer is
            # cancelled now that a continuation arrived.
            if self._pending_ack_text:
                self._cancel_ack_grace()
                combined = f"{self._pending_ack_text} {transcript}".strip()
                log.info(
                    "[avatar-barge] ack-grace continuation: %r + %r → %r",
                    self._pending_ack_text, transcript, combined,
                )
                transcript = combined
                if transcript:
                    self._best_interim = transcript

            intent = self._classifier.classify(
                transcript=transcript, user_duration_ms=duration_ms,
            )
            log.info(
                "[avatar-barge] heuristic intent=%s final=%s transcript=%r duration_ms=%d",
                intent.value, is_final, transcript, duration_ms,
            )

            if intent in (BargeIntent.ACK, BargeIntent.ANSWER):
                # CRITICAL human-feel fix: an ACK/ANSWER verdict on an INTERIM is
                # PROVISIONAL, not committed. STT (esp. Azure) emits a single
                # ack word as the FIRST interim of a longer phrase — "yeah" then
                # "yeah explain that in detail". If we commit ACK on that first
                # interim we (a) lock `_utterance_intent` so the dedup guard
                # suppresses every later interim/final, and (b) the real request
                # is silently dropped. So on an INTERIM we keep the avatar
                # speaking but STAY in 'holding' WITHOUT locking the utterance —
                # the growing interims get re-classified, and a continuation like
                # "...explain that in detail" flips to INTERRUPT. Only a FINAL
                # ack (or the quiet-timeout below) is a genuine, committed
                # acknowledgement. This mirrors the AMBIGUOUS-interim handling.
                if is_final:
                    # An ACK/ANSWER FINAL is NOT committed immediately. Tight STT
                    # segmentation can finalize a bare "okay" as its own transcript
                    # right before the real question ("what about the fees") lands
                    # as a SECOND final. Committing here would lock the utterance
                    # and the dedup guard would swallow the question. Instead we
                    # hold a short grace window: if a continuation final arrives
                    # it's concatenated + reclassified (→ INTERRUPT) above; if the
                    # grace expires with silence, we commit the ACK then. The
                    # avatar keeps speaking throughout, so this adds no latency.
                    self._pending_ack_text = transcript
                    self._start_ack_grace(direction, intent)
                else:
                    # Provisional ACK on an INTERIM: record nothing, stay holding
                    # so growing interims can override (handled above).
                    log.debug(
                        "[avatar-barge] provisional %s on interim %r — keep "
                        "speaking, stay holding (await continuation/final)",
                        intent.value, transcript,
                    )
                return
            if intent in (BargeIntent.INTERRUPT, BargeIntent.CONFUSED):
                if is_final:
                    await self._fire_and_answer(direction, transcript)
                else:
                    await self._fire_stop(direction)
                    self._state = "awaiting_final"
                    self._schedule_awaiting_fallback(direction)
                return
            # AMBIGUOUS:
            if is_final:
                if len(transcript.split()) >= 2:
                    await self._fire_and_answer(direction, transcript)
                else:
                    self._handle_ack(transcript)
                return
            # AMBIGUOUS interim → keep speaking, stay holding.
            return

        # --- holding QUIET timeout → ACK -------------------------------
        if (
            self._state == "holding"
            and not self._decided
            and self._t_user_started is not None
            and (time.monotonic() - self._last_activity) > _HOLDING_TIMEOUT_S
        ):
            log.info(
                "[avatar-barge] holding quiet timeout — assuming ACK (no transcript "
                "in %.1fs; avatar keeps speaking)",
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
        self._record_user_message(transcript)
        self._utterance_intent = BargeIntent.ACK
        self._bus.publish(BargeInEvent(phase="rejected"))
        self._reset_holding(to_state="bot_speaking")

    def _handle_answer(self, transcript: str) -> None:
        self._record_user_message(transcript)
        self._utterance_intent = BargeIntent.ANSWER
        self._bus.publish(BargeInEvent(phase="rejected"))
        self._reset_holding(to_state="bot_speaking")

    async def _fire_stop(self, direction: FrameDirection) -> None:
        """STOP the avatar: cancel TTS+Simli (InterruptionFrame) + release the
        held UserStartedSpeakingFrame so AgentBridge cancels its brain task."""
        self._decided = True
        self._bus.publish(BargeInEvent(phase="confirmed", bot_said_partial=None))
        await self.push_frame(InterruptionFrame(), direction)
        if self._held_user_frame is not None:
            await self.push_frame(self._held_user_frame, direction)
            self._held_user_frame = None
        if self._held_user_stopped_frame is not None:
            await self.push_frame(self._held_user_stopped_frame, direction)
            self._held_user_stopped_frame = None

    async def _fire_and_answer(self, direction: FrameDirection, transcript: str) -> None:
        await self._fire_stop(direction)
        self._utterance_intent = BargeIntent.INTERRUPT
        self._state = "cancelling"
        if transcript:
            await self.push_frame(
                TranscriptionFrame(text=transcript, user_id="user", timestamp=""),
                direction,
            )

    async def _forward_question(self, direction: FrameDirection, transcript: str) -> None:
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
            name=f"avatar-barge-awaitfinal-{self._session.short()}",
        )

    async def _awaiting_fallback(self, direction: FrameDirection) -> None:
        try:
            await asyncio.sleep(_AWAITING_FINAL_TIMEOUT_S)
        except asyncio.CancelledError:
            return
        if self._state == "awaiting_final" and self._best_interim:
            log.info(
                "[avatar-barge] no final after stop — answering best interim %r",
                self._best_interim[:80],
            )
            await self._forward_question(direction, self._best_interim)

    def _cancel_awaiting(self) -> None:
        if self._awaiting_task and not self._awaiting_task.done():
            self._awaiting_task.cancel()
        self._awaiting_task = None

    # ------------------------------------------------------------------
    # ACK-grace timer — commit the pending ACK only if no continuation arrives
    # ------------------------------------------------------------------
    def _start_ack_grace(self, direction: FrameDirection, intent: BargeIntent) -> None:
        """Hold an ack-final briefly; commit it only if no continuation lands."""
        self._cancel_ack_grace()
        self._ack_grace_task = asyncio.create_task(
            self._ack_grace_expire(intent),
            name=f"avatar-barge-ackgrace-{self._session.short()}",
        )

    async def _ack_grace_expire(self, intent: BargeIntent) -> None:
        try:
            await asyncio.sleep(_ACK_GRACE_S)
        except asyncio.CancelledError:
            return
        # No continuation arrived within the grace → it really was just an ack.
        text = self._pending_ack_text
        self._pending_ack_text = ""
        if self._state != "holding":
            return
        log.info(
            "[avatar-barge] ack-grace expired — committing %s %r (no continuation)",
            intent.value, text,
        )
        if intent == BargeIntent.ANSWER:
            self._handle_answer(text)
        else:
            self._handle_ack(text)

    def _cancel_ack_grace(self) -> None:
        if self._ack_grace_task and not self._ack_grace_task.done():
            self._ack_grace_task.cancel()
        self._ack_grace_task = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _reset_holding(self, *, to_state: str) -> None:
        self._state = to_state
        self._t_user_started = None
        self._held_user_frame = None
        self._held_user_stopped_frame = None
        self._decided = False
        self._cancel_ack_grace()
        self._pending_ack_text = ""

    def _record_user_message(self, text: str) -> None:
        if not text:
            return
        try:
            from agent_backend.llm_agent.conversation import get_conversation
            get_conversation(self._session.conversation_id).append_user(text)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Tiny RMS helper — root-mean-square of int16 PCM little-endian audio.
# ---------------------------------------------------------------------------
def _rms(audio: bytes) -> float:
    if not audio:
        return 0.0
    try:
        import audioop
        return float(audioop.rms(audio, 2))
    except Exception:  # noqa: BLE001
        return 0.0


__all__ = ["BargeInManager"]
