"""BargeInManager — turns BargeClassifier decisions into pipeline actions
(meeting channel, fully self-contained).

ISOLATION NOTE
--------------
Self-contained copy for the meeting channel. Imports only its sibling
`barge_classifier` module + the shared addressee gate. Nothing from
`channels.voice` / `channels.avatar_video` / `channels.pipecat`. Each channel
owns its own human-simulation stack so they evolve independently.

Where it sits
-------------
UPSTREAM of MeetingAgentBridge (between STT and the bridge). That position lets
the manager SUPPRESS transcript frames before the brain ever sees them when the
classifier decides the input is an ack / answer / ambiguous (agent keeps
speaking), and emit its OWN InterruptionFrame on a confirmed interrupt.

Human-like interruption model
------------------------------
The hard rule from live calls: **never answer a half-sentence.** STT emits
growing interims ("you can" → "you can tell me the fees"). So we split actions:
  - STOP fast (cancel TTS + the in-flight brain) the moment the classifier is
    confident the user is really interrupting.
  - ANSWER only on the FULL final transcript — we hold in `awaiting_final`
    after stopping, then forward the FINAL (complete question) to the brain.

The InterruptionFrame this manager emits flows downstream THROUGH the
MeetingAgentBridge and TTS and reaches the Simli avatar service (when the
avatar is on), whose handler calls Simli `clearBuffer()` — so a confirmed barge
actually stops the avatar's lip-synced speech.

PANEL-MODE ADDRESSEE SCOPE
--------------------------
In a 3-party meeting the agent must NOT be interrupted by the candidate and
counsellor talking to EACH OTHER. So the manager is given the same AddresseeGate
the bridge uses: in panel mode a barge only counts if the (final) transcript is
addressed to the agent — otherwise the human↔human turn is suppressed and the
agent keeps speaking. In solo mode the gate is inert (require_address=False), so
every barge is evaluated normally — exactly like a 1:1 avatar call.

State machine (identical to the proven avatar/voice manager)
------------------------------------------------------------
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

from agent_backend.channels.meeting.barge_classifier import (
    BargeClassifier,
    BargeIntent,
)
from agent_backend.channels.meeting.events import BargeInEvent, EventBus
from agent_backend.infra import get_logger
from agent_backend.llm_agent.addressee import AddresseeGate

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
_ACK_GRACE_S: float = 0.6


class MeetingBargeInManager(FrameProcessor):
    """Per-meeting barge-in state machine driven by `BargeClassifier`.

    `gate` is the meeting's AddresseeGate. In panel mode (require_address=True)
    a barge only counts when the final transcript is addressed to the agent; in
    solo mode the gate is inert so every barge is evaluated.
    """

    def __init__(
        self,
        *,
        session,
        gate: AddresseeGate | None = None,
        human_count_fn=None,
        bus: EventBus | None = None,
    ) -> None:
        super().__init__()
        self._session = session
        # Optional per-meeting event bus: barge phases (detected / confirmed /
        # rejected) are published for the SilenceMonitor (engagement signal +
        # clock resets) and the metrics sink — same contract as avatar_video.
        self._bus = bus
        self._gate = gate or AddresseeGate(require_address=False)
        # Live human count (agent excluded). With ≤1 human the panel-addressee
        # scope is skipped — it's effectively a 1:1, so every barge is evaluated.
        self._human_count_fn = human_count_fn
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
                if self._bus is not None:
                    self._bus.publish(BargeInEvent(phase="rejected"))
                log.info(
                    "[meeting-barge] acoustic-gate rejected",
                    user_rms=self._user_rms_at_start, tts_peak=self._tts_rms_peak,
                )
                return  # SUPPRESS — agent keeps speaking

            self._state = "holding"
            self._t_user_started = time.monotonic()
            self._last_activity = self._t_user_started
            self._held_user_frame = frame
            self._held_user_stopped_frame = None
            self._decided = False
            self._best_interim = ""
            if self._bus is not None:
                self._bus.publish(BargeInEvent(phase="detected"))
            return

        # --- already classified this utterance → suppress dupes --------
        if (
            isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame))
            and self._utterance_intent is not None
        ):
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

            # ACK grace: concatenate a continuation onto a pending bare-ack final
            # and re-classify the COMBINED utterance ("okay" + "what about fees"
            # → INTERRUPT). Cancel the grace timer now a continuation arrived.
            if self._pending_ack_text:
                self._cancel_ack_grace()
                combined = f"{self._pending_ack_text} {transcript}".strip()
                log.info(
                    "[meeting-barge] ack-grace continuation: %r + %r → %r",
                    self._pending_ack_text, transcript, combined,
                )
                transcript = combined
                if transcript:
                    self._best_interim = transcript

            # PANEL ADDRESSEE SCOPE: on a FINAL, if the gate is active (panel) and
            # the turn isn't addressed to the agent, this is human↔human talk —
            # suppress the barge and keep the agent speaking. (Solo gate is inert.)
            # SKIPPED when ≤1 human remains: it's effectively a 1:1, so every turn
            # is for the agent and must be allowed to interrupt/answer.
            if is_final and self._gate.require_address and not self._solo_now():
                decision = self._gate.evaluate(transcript)
                if not decision.allowed:
                    log.info(
                        "[meeting-barge] panel: final not addressed — keep speaking",
                        transcript=transcript[:80],
                    )
                    self._handle_ack(transcript)
                    return

            intent = self._classifier.classify(
                transcript=transcript, user_duration_ms=duration_ms,
            )
            log.debug(
                "[meeting-barge] heuristic intent=%s final=%s transcript=%r duration_ms=%d",
                intent.value, is_final, transcript, duration_ms,
            )

            if intent in (BargeIntent.ACK, BargeIntent.ANSWER):
                if is_final:
                    # Don't commit a bare-ack final immediately — hold the grace
                    # window for a continuation (tight STT segmentation splits
                    # "okay" off from the real question). Agent keeps speaking.
                    self._pending_ack_text = transcript
                    self._start_ack_grace(direction, intent)
                else:
                    # Provisional ACK on an INTERIM: stay holding so growing
                    # interims can override (a continuation flips to INTERRUPT).
                    pass
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
            log.debug(
                "[meeting-barge] holding quiet timeout — assuming ACK (agent keeps speaking)"
            )
            self._handle_ack("")
            # fall through to forward the current frame

        # --- everything else passes through unchanged ------------------
        await self.push_frame(frame, direction)

    def _solo_now(self) -> bool:
        """True when ≤1 human is in the room (→ skip panel addressee scope)."""
        if self._human_count_fn is None:
            return True
        try:
            return self._human_count_fn() <= 1
        except Exception:  # noqa: BLE001
            return True

    # ------------------------------------------------------------------
    # Intent handlers
    # ------------------------------------------------------------------
    def _handle_ack(self, transcript: str) -> None:
        self._record_user_message(transcript)
        self._utterance_intent = BargeIntent.ACK
        if self._bus is not None:
            self._bus.publish(BargeInEvent(phase="rejected"))
        self._reset_holding(to_state="bot_speaking")

    def _handle_answer(self, transcript: str) -> None:
        self._record_user_message(transcript)
        self._utterance_intent = BargeIntent.ANSWER
        if self._bus is not None:
            self._bus.publish(BargeInEvent(phase="rejected"))
        self._reset_holding(to_state="bot_speaking")

    async def _fire_stop(self, direction: FrameDirection) -> None:
        """STOP the agent: cancel TTS+Simli (InterruptionFrame) + release the held
        UserStartedSpeakingFrame so the bridge cancels its in-flight brain task."""
        self._decided = True
        if self._bus is not None:
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
            name=f"meeting-barge-awaitfinal-{self._session.short()}",
        )

    async def _awaiting_fallback(self, direction: FrameDirection) -> None:
        try:
            await asyncio.sleep(_AWAITING_FINAL_TIMEOUT_S)
        except asyncio.CancelledError:
            return
        if self._state == "awaiting_final" and self._best_interim:
            log.info(
                "[meeting-barge] no final after stop — answering best interim %r",
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
        self._cancel_ack_grace()
        self._ack_grace_task = asyncio.create_task(
            self._ack_grace_expire(intent),
            name=f"meeting-barge-ackgrace-{self._session.short()}",
        )

    async def _ack_grace_expire(self, intent: BargeIntent) -> None:
        try:
            await asyncio.sleep(_ACK_GRACE_S)
        except asyncio.CancelledError:
            return
        text = self._pending_ack_text
        self._pending_ack_text = ""
        if self._state != "holding":
            return
        log.info(
            "[meeting-barge] ack-grace expired — committing %s %r (no continuation)",
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


__all__ = ["MeetingBargeInManager"]
