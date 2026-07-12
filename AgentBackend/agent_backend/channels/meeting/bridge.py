"""Meeting AgentBridge — name-gated, speaker-aware bridge to the one brain.

This is the meeting channel's OWN Pipecat FrameProcessor. It is the closest
sibling of `channels/pipecat/processors/agent_bridge.py` (the Teams meeting
bridge) — same job (a bot sitting in a multi-party meeting that must only speak
when addressed) — but it adds two meeting-specific behaviours and stays fully
self-contained (it never imports the avatar or voice channels):

  1. ADDRESSEE GATE (reused logic). Two humans talking to each other must NOT
     trigger the agent. `AddresseeGate` (built from the persona name + aliases)
     lets a turn through only when the agent is addressed by name OR given a
     direct command. Off → the turn is dropped (but still captured to episodic +
     the diarised transcript, so the end-of-meeting analysis sees the whole
     conversation, not just the bits aimed at the agent).

  2. SPEAKER ATTRIBUTION (M4). Every `TranscriptionFrame` from the LiveKit
     transport carries `user_id` = the speaker's participant SID (Pipecat's
     STTService copies it off the per-track `UserAudioRawFrame`). We resolve
     SID → role ("candidate" / "counsellor") via a caller-supplied lookup
     (participant metadata, set at token-mint time) and:
       - prefix the brain's user text with [CANDIDATE] / [COUNSELLOR] so the
         meeting OUTPUT STYLE can answer the right person, and
       - tag the transcript line with the speaker for the dual analysis.

| Inbound frame                | Action                                                       |
|------------------------------|--------------------------------------------------------------|
| StartFrame                   | forward (NO opener — the humans run the meeting, not the bot) |
| UserStartedSpeakingFrame     | cancel any in-flight brain reply; forward                     |
| TranscriptionFrame (final)   | resolve speaker → capture (diarised) → gate → maybe run brain |
| All other frames             | forward unchanged                                             |

Deliberately NO bot-speaks-first opener and NO silence re-engagement: the agent
is a co-pilot, not the host. It stays silent until called on.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    StartFrame,
    TextFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from agent_backend.channels.meeting.events import (
    BotSpeakingEvent,
    EventBus,
    SilenceTickEvent,
)
from agent_backend.config import get_settings
from agent_backend.infra import get_logger
from agent_backend.llm_agent import run_stream
from agent_backend.llm_agent.addressee import AddresseeGate
from agent_backend.llm_agent.conversation import get_conversation
from agent_backend.llm_agent.memory import get_episodic_store
from agent_backend.llm_agent.memory.episodic import make_conversation_record
from agent_backend.llm_agent.session import Session

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Synthetic prompts injected at silence thresholds (SOLO meetings only). The
# brain treats these as normal turns and produces a real, in-character
# utterance through TTS → avatar — so the re-engagement looks/sounds like the
# rest of the conversation. Ported from the avatar channel; T5 is meeting-
# specific (the runner leaves the room after the goodbye instead of end_call).
# Persona overrides via identity `silence_prompts.<threshold>` — see
# `_silence_prompt`.
# ---------------------------------------------------------------------------
_SILENCE_PROMPTS: dict[str, str] = {
    "T2": (
        "[SYSTEM] The person went quiet after your last turn — probably still "
        "thinking. Gently re-engage in ONE short, natural sentence tied to what "
        "you just said (invite a reaction to that point, or a small follow-up "
        "about it). Warm and brief — NOT a wall of text. BANNED filler: 'take "
        "your time', 'I'm here to help', 'no rush', 'still there?', 'can you hear "
        "me?'. Don't reuse any line you've said before this meeting."
    ),
    "T3": (
        "[SYSTEM] Still no response. Check in once more — even SHORTER this time "
        "(a brief half-sentence is fine), and clearly DIFFERENT from your first "
        "nudge: switch angle, e.g. offer to move to another topic or ask if now "
        "is still a good time. Stay warm, don't apologise, don't repeat anything "
        "you've already said."
    ),
    "T4": (
        "[SYSTEM] Still silence. One LAST very short, soft check before you let "
        "them go — just a few words, human and unhurried (e.g. a quiet 'Are you "
        "still with me?'-type line in your OWN fresh words). Different again from "
        "your earlier two. Do NOT end the meeting yet."
    ),
    "T5": (
        "[SYSTEM] They've stayed silent through all your check-ins. Close the "
        "meeting warmly in ONE short sentence: no pressure, invite them to join "
        "again or reach out whenever suits them and you'll pick this up. Phrase "
        "it freshly. Say ONLY that one sentence — the meeting ends after it."
    ),
}


def _silence_prompt(threshold: str, session: Session) -> str | None:
    """Re-engagement prompt for a silence threshold, PERSONA-AWARE.

    Reads `silence_prompts.<threshold>` from the active persona (resolved by
    channel) if present, so a custom meeting persona can override the phrasing.
    Falls back to the defaults above."""
    try:
        from agent_backend.llm_agent.agent import _persona_name_for_channel
        from agent_backend.llm_agent.identity import get_identity

        identity = get_identity(_persona_name_for_channel(session.channel))
        sp = identity.get("silence_prompts")
        if isinstance(sp, dict):
            val = sp.get(threshold)
            if isinstance(val, str) and val.strip():
                return val.strip()
    except Exception as e:  # noqa: BLE001
        log.debug("[meeting-silence] persona silence prompt lookup failed", err=str(e)[:160])
    return _SILENCE_PROMPTS.get(threshold)

# Resolve a participant SID → role label ("candidate" | "counsellor" | other).
# Supplied by the runner, which reads it off the LiveKit participant metadata.
SpeakerResolver = Callable[[str], str]
# Resolve a participant SID → their DISPLAY NAME (the name they joined with, e.g.
# "Rahul"). Used for human-readable logs/diarisation; returns "" when unknown.
NameResolver = Callable[[str], str]
# Optional async sink to record a diarised transcript line for end-of-meeting
# analysis: (role, text) where role is the speaker label or "agent".
TranscriptSink = Callable[[str, str], Awaitable[None]]
# Optional async sink that DURABLY persists a turn (role, text) to the
# BusinessLayer as it happens, so a mid-meeting crash/restart doesn't lose the
# conversation. Distinct from TranscriptSink (in-RAM, drives end-of-meeting
# analysis); this one is fire-and-forget durability.
TurnSink = Callable[[str, str], Awaitable[None]]


class MeetingAgentBridge(FrameProcessor):
    """One instance per meeting the agent sits in. Owns the in-flight brain
    task, the addressee gate + mute state, and the speaker resolver."""

    def __init__(
        self,
        *,
        session: Session,
        gate: AddresseeGate | None = None,
        speaker_resolver: SpeakerResolver | None = None,
        name_resolver: NameResolver | None = None,
        transcript_sink: TranscriptSink | None = None,
        turn_sink: TurnSink | None = None,
        human_count_fn: "Callable[[], int] | None" = None,
        bus: EventBus | None = None,
        publish_bot_speaking: bool = False,
    ) -> None:
        super().__init__()
        self._session = session
        self._gate = gate or AddresseeGate(require_address=False)
        self._speaker_resolver = speaker_resolver
        self._name_resolver = name_resolver
        self._transcript_sink = transcript_sink
        self._turn_sink = turn_sink
        # Returns the number of HUMANS currently in the room (agent excluded).
        # Drives dynamic gating: with ≤1 human it's effectively a 1:1, so the
        # agent answers EVERY turn (no need to address it by name); with 2+ humans
        # it's a panel and the addressee gate applies. None → assume 1 (solo).
        self._human_count_fn = human_count_fn
        # Per-meeting event bus (turn detector / barge manager / silence monitor
        # / metrics). None → all bus-driven behaviours are inert.
        self._bus = bus
        # AUDIO-ONLY meetings: the bridge publishes BotSpeakingEvent from the
        # transport's BotStarted/StoppedSpeakingFrame (reliable without Simli's
        # continuous idle audio). With the AVATAR ON this stays False — the
        # render service publishes the authoritative signal from its own
        # TTSStarted/TTSStopped (+ playout drain), exactly like avatar_video.
        self._publish_bot_speaking = publish_bot_speaking
        self._muted = False
        # Set True after the agent speaks a question ("…?"); lets the NEXT human
        # turn through in panel mode even if unaddressed (they're answering the
        # agent). Cleared once consumed or when a new agent reply starts.
        self._agent_awaiting_answer = False
        self._stream_task: asyncio.Task | None = None
        self._silence_listener_task: asyncio.Task | None = None
        # True once the in-flight reply has pushed its first token (i.e. audio is
        # actually flowing to TTS/Simli). Gates the eager barge cancel: a bare VAD
        # trip while the brain is still COMPOSING (no audio yet) must not kill the
        # turn — only a real talk-over while the agent is speaking should. In a
        # room with N microphones this matters even more than in a 1:1: any
        # cough/noise from ANY participant trips VAD, and without this gate it
        # silently killed the reply the candidate was waiting on.
        self._reply_started = False
        # Most recent RAW user text + its speaker — drives the duplicate-
        # transcript dedup and the split-utterance merge (both from avatar_video).
        self._last_user_text: str | None = None
        self._last_user_uid: str = ""
        # NOTE: cross-speaker TEXT dedup was removed. The duplicate is now stopped
        # DETERMINISTICALLY upstream — browser echo-cancellation (FE) stops the
        # re-capture at the source, and the STT router's SFU single-dominant-
        # speaker gate (stt_router.py `_sfu_dominant_id`) feeds STT for only the
        # loudest active track, so an attenuated echo copy never reaches the brain.
        # Text-similarity dedup was a lossy backstop (it guessed by threshold and
        # could drop two people genuinely saying the same short word); the
        # deterministic gate makes it unnecessary.

    # ------------------------------------------------------------------
    async def speak_opener(self, text: str) -> None:
        """Speak a one-shot opener into the room (used in solo mode when the
        candidate joins). Goes through the brain-free emit path so it's instant,
        and is captured + persisted like any agent turn."""
        line = (text or "").strip()
        if not line:
            return
        await self._emit_text(line)
        if self._transcript_sink is not None:
            await self._safe_sink(self._transcript_sink, "agent", line)
        await self._persist("agent", line)

    # ------------------------------------------------------------------
    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            # No opener here — the runner fires the solo opener on candidate-join.
            await self.push_frame(frame, direction)
            # Start the silence responder listener (solo re-engagement) when a
            # bus is attached and the flag is on — mirrors avatar_video.
            if (
                self._bus is not None
                and get_settings().meeting_enable_silence_responder
                and self._silence_listener_task is None
            ):
                self._silence_listener_task = asyncio.create_task(
                    self._listen_for_silence(),
                    name=f"meeting-silence-listener-{self._session.short()}",
                )
            return

        # Bot speaking lifecycle: forward, and (audio-only mode) publish the
        # BotSpeakingEvent that arms/pauses the SilenceMonitor. With the avatar
        # ON the render service publishes the authoritative signal instead —
        # the transport's audio-derived frames are unreliable under Simli's
        # continuous idle audio (same reasoning as avatar_video's bridge).
        if isinstance(frame, (BotStartedSpeakingFrame, BotStoppedSpeakingFrame)):
            if self._publish_bot_speaking and self._bus is not None:
                self._bus.publish(
                    BotSpeakingEvent(speaking=isinstance(frame, BotStartedSpeakingFrame))
                )
            await self.push_frame(frame, direction)
            return

        # A human starts speaking → cancel the in-flight brain ONLY if the agent
        # is actually emitting audio (real talk-over, or a barge the manager
        # confirmed while it was speaking and released to us). If the brain is
        # still COMPOSING (no token emitted yet), do NOT cancel: a bare VAD trip
        # here is usually noise/cross-talk with no transcript behind it, and
        # cancelling would silently kill a turn the candidate is waiting on. A
        # genuine new utterance still lands as a transcript, and
        # _handle_transcript supersedes (or merges) the brain run there.
        if isinstance(frame, UserStartedSpeakingFrame):
            if self._reply_started:
                await self._cancel_current_stream()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TranscriptionFrame):
            text = (frame.text or "").strip()
            if not text:
                return
            user_id = getattr(frame, "user_id", "") or ""
            speaker = self._resolve_speaker(user_id)
            name = self._resolve_name(user_id)
            await self._handle_transcript(text, speaker, name, user_id=user_id)
            return

        await self.push_frame(frame, direction)

    # ------------------------------------------------------------------
    def _solo_now(self) -> bool:
        """True when ≤1 human is in the room (→ behave like a 1:1, answer all).
        No callback wired → assume solo (the safe default for a 1:1 channel)."""
        if self._human_count_fn is None:
            return True
        try:
            return self._human_count_fn() <= 1
        except Exception:  # noqa: BLE001
            return True

    @staticmethod
    def _is_question(text: str) -> bool:
        """Did the agent's reply end by asking something? (drives the
        'let the answer through' override in panel mode)."""
        t = (text or "").rstrip().rstrip("\"')]").rstrip()
        return t.endswith("?")

    def _resolve_speaker(self, user_id: str) -> str:
        """Map the STT frame's participant SID to a role label. Falls back to
        'candidate' when unknown so a missed metadata read never silently drops
        attribution (the candidate is the more common speaker to mis-tag)."""
        if not user_id or self._speaker_resolver is None:
            return "candidate"
        try:
            role = (self._speaker_resolver(user_id) or "").strip().lower()
        except Exception as e:  # noqa: BLE001
            log.debug("[meeting-bridge] speaker resolve failed", err=str(e))
            return "candidate"
        return role or "candidate"

    def _resolve_name(self, user_id: str) -> str:
        """Map the STT frame's participant id to the speaker's DISPLAY NAME (the
        name they joined with). Returns "" when unknown — callers then fall back
        to the role label. Best-effort; never raises."""
        if not user_id or self._name_resolver is None:
            return ""
        try:
            return (self._name_resolver(user_id) or "").strip()
        except Exception as e:  # noqa: BLE001
            log.debug("[meeting-bridge] name resolve failed", err=str(e))
            return ""

    async def _handle_transcript(
        self, text: str, speaker: str, name: str = "", user_id: str = ""
    ) -> None:
        """A finalised transcript landed from `speaker` (role), spoken by the
        participant named `name` (display name, may be "").

        Order: log → capture diarised (ALWAYS, even if dropped) → gate → maybe
        run the brain. Capturing every turn (not just addressed ones) is what
        lets the end-of-meeting analysis judge BOTH humans on the full
        conversation."""
        # Human-readable speaker label for the log: the actual person's NAME when
        # we know it, with the role in parentheses for context (e.g.
        # "RAHUL (candidate)"); falls back to just the ROLE when the name is
        # unknown. The `speaker` ROLE is still what drives gating + attribution.
        label = f"{name} ({speaker})" if name else speaker.upper()

        log.info("[meeting] >>> %s", label, session=self._session.short(), text=text)

        # ALWAYS capture the human turn (diarised) — side-talk between the two
        # humans is the bulk of what the counsellor-evaluation analysis reads.
        self._capture(source="user", content=text, speaker=speaker)
        if self._transcript_sink is not None:
            await self._safe_sink(self._transcript_sink, speaker, text)
        # Durably persist the human turn so a mid-meeting crash doesn't lose it.
        await self._persist(speaker, text)

        decision = self._gate.evaluate(text, muted=self._muted)

        # --- DYNAMIC GATING -------------------------------------------------
        # The static gate says "not addressed", but two situations override it so
        # the agent doesn't ignore a turn that's obviously meant for it:
        #
        #   1) ONLY ONE HUMAN LEFT. With ≤1 human in the room it's effectively a
        #      1:1 — there's no second human to be talking to, so EVERY turn is
        #      for the agent. (Fixes: counsellor leaves a panel → agent keeps
        #      asking the candidate questions but drops their answers.)
        #
        #   2) THE AGENT JUST ASKED A QUESTION. In a panel, if the agent's last
        #      utterance ended with "?", the next human turn is almost certainly
        #      the answer — let it through once even without the wake-word.
        #
        # Mute always wins: a muted agent only un-gates on an explicit address /
        # unmute (handled by the gate's own `muted` path), so we don't override
        # while muted.
        allowed = decision.allowed
        override_reason = None
        if not allowed and not self._muted:
            if self._solo_now():
                allowed = True
                override_reason = "solo-1-human"
            elif self._agent_awaiting_answer:
                allowed = True
                override_reason = "answering-agent-question"

        if not allowed:
            log.info(
                "[meeting-gate] dropping turn (not addressed)",
                session=self._session.short(),
                speaker=speaker,
                reason=decision.reason,
                text=text[:80],
            )
            return

        # An addressed/allowed turn consumes the "agent just asked" flag.
        self._agent_awaiting_answer = False
        if override_reason:
            log.debug(
                "[meeting-gate] turn allowed by override",
                session=self._session.short(), speaker=speaker, reason=override_reason,
            )

        # Mute / unmute verbal controls (parity with the Teams bridge).
        if decision.wants_mute and not self._muted:
            self._muted = True
            log.info("[meeting-gate] muted by user", session=self._session.short())
            await self._emit_text("Okay, I'll stay quiet — just say my name when you need me.")
            return
        if self._muted and decision.wants_unmute:
            self._muted = False
            log.info("[meeting-gate] unmuted by user", session=self._session.short())
            if len(text.split()) <= 4:
                await self._emit_text("I'm back.")
                return

        log.debug(
            "[meeting-gate] turn allowed",
            session=self._session.short(),
            speaker=speaker,
            reason=decision.reason,
        )

        # SERIALIZE TURNS — never let two brain replies run at once (Azure's
        # continuous recognizer often emits a single spoken question as TWO
        # finals a few hundred ms apart). The machinery below is the avatar
        # channel's proven turn flow, ported as-is:
        brain_running = self._stream_task is not None and not self._stream_task.done()

        # DUPLICATE-TRANSCRIPT DEDUP: an identical re-final of the SAME text
        # while its reply is still in flight must not cancel-and-restart the
        # brain (STT sometimes re-emits a final verbatim).
        if brain_running and text == self._last_user_text:
            log.info(
                "[meeting-bridge] ignoring duplicate transcript",
                session=self._session.short(), text=text,
            )
            return

        # MULTI-SEGMENT MERGE: if the SECOND final lands while the brain is
        # STILL answering the FIRST, the speaker almost certainly meant ONE
        # question ("what are the fees" + "for the CS program") — so COMBINE
        # both parts and answer them together, instead of answering only the
        # second (which drops the first half of the question). Only merged for
        # the SAME speaker (a different participant's turn is a genuine new
        # turn, never a split), and only while the first answer is in flight.
        if (
            brain_running
            and self._last_user_text
            and text != self._last_user_text
            and user_id == self._last_user_uid
        ):
            combined = f"{self._last_user_text} {text}".strip()
            log.info(
                "[meeting-bridge] merging follow-up segment (split utterance)",
                session=self._session.short(),
                first=self._last_user_text, second=text, combined=combined,
            )
            text = combined

        self._last_user_text = text
        self._last_user_uid = user_id

        # 1. Cancel any in-flight brain task FIRST. On a split utterance we've
        #    already merged its text into `text`, so the old run is superseded.
        await self._cancel_current_stream()

        # Prefix the speaker tag so the meeting OUTPUT STYLE answers the right
        # person. The brain strips the tag conceptually (it's documented in the
        # style block as a system signal), the same way [CONFUSED] is handled.
        # IMPORTANT: the tag uses the ROLE ([CANDIDATE]/[COUNSELLOR]) — that's the
        # exact token the output-style documents; never the log label. When we
        # know the speaker's name we add it (e.g. "[CANDIDATE Rahul]") so the
        # agent can address them naturally, but the role token stays first.
        role_tag = speaker.upper()
        tag = f"{role_tag} {name}" if name else role_tag
        tagged = f"[{tag}] {text}"

        # 2. Record the user turn (the TAGGED form run_stream would have
        #    recorded) to conversation memory HERE, not inside run_stream —
        #    run_stream's own append happens AFTER a ~50-300ms RAG step, so a
        #    fast cancel (split utterance / barge) can kill the run before it
        #    records, losing the turn from history. Recording here is
        #    cancellation-proof; _consume_stream passes record_user=False so it
        #    isn't double-recorded. Skip if this exact text is already the last
        #    user message (the cancelled first-part run beat us to it).
        try:
            convo = get_conversation(self._session.conversation_id)
            recent = convo.recent(n=1)
            last_is_same = bool(
                recent and getattr(recent[-1], "type", "") == "human"
                and (getattr(recent[-1], "content", "") or "").strip() == tagged
            )
            if not last_is_same:
                convo.append_user(tagged)
        except Exception as e:  # noqa: BLE001
            log.debug("[meeting-bridge] convo append_user failed", err=str(e))
        # 3. ALWAYS-CLEAR: push InterruptionFrame BEFORE running the new brain so
        #    TTS AND the avatar buffer (Simli clearBuffer) are cleared — otherwise
        #    the agent finishes speaking the previous reply before starting the
        #    new one ("robot queue"). Idempotent: no-op when nothing to clear.
        await self.push_frame(InterruptionFrame())
        # Stamp turn start (final transcript landed → about to run the brain) so
        # _consume_stream can log one clean end-to-end timing line per turn.
        t_turn = time.monotonic()
        self._stream_task = asyncio.create_task(
            self._consume_stream(tagged, t_turn=t_turn, speaker=speaker),
            name=f"meeting-brain-{self._session.short()}",
        )

    async def _consume_stream(
        self, text: str, *, t_turn: float | None = None, speaker: str = ""
    ) -> None:
        """Pull tokens from the brain and push each as a TextFrame to TTS.

        Logs ONE end-to-end timing line per turn ([meeting-turn]):
          first_ms = transcript-final → first reply token (LLM time-to-first-token,
                     the "how long until it starts talking" number)
          total_ms = transcript-final → full reply generated.
        """
        # Reset per run — flips True on the first emitted token (audio now
        # flowing), which is what arms the eager barge cancel for genuine
        # talk-over (see UserStartedSpeakingFrame handling).
        self._reply_started = False
        await self.push_frame(LLMFullResponseStartFrame())
        bot_chunks: list[str] = []
        t_first: float | None = None
        try:
            async for tok in run_stream(
                text,
                channel=self._session.channel,
                session=self._session,
                # The bridge already recorded the user turn in conversation
                # memory (cancellation-proof). Don't double-record here.
                record_user=False,
            ):
                if t_first is None:
                    t_first = time.monotonic()
                bot_chunks.append(tok)
                self._reply_started = True
                await self.push_frame(TextFrame(text=tok))
        except asyncio.CancelledError:
            log.info("[meeting-bridge] brain cancelled (barge-in)", session=self._session.short())
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("[meeting-bridge] brain failed", session=self._session.short(), err=str(e))
        finally:
            await self.push_frame(LLMFullResponseEndFrame())
            full = "".join(bot_chunks).strip()
            if full:
                log.info("[meeting] <<< AGENT", session=self._session.short(), text=full)
                self._capture(source="bot", content=full, speaker="agent")
                if self._transcript_sink is not None:
                    await self._safe_sink(self._transcript_sink, "agent", full)
                await self._persist("agent", full)
                # If the agent just asked something, the next human turn is the
                # answer — let it through once in panel mode even unaddressed.
                self._agent_awaiting_answer = self._is_question(full)
                # ── ONE clean end-to-end timing line per turn ──────────────────
                # first_ms = how long until the agent STARTED replying (LLM
                # time-to-first-token); total_ms = until the full reply was
                # generated. Measured from the final transcript landing. This is
                # the single number to watch for "is it human-speed?".
                if t_turn is not None:
                    now = time.monotonic()
                    first_ms = int(((t_first or now) - t_turn) * 1000)
                    total_ms = int((now - t_turn) * 1000)
                    log.info(
                        "[meeting-turn] done",
                        session=self._session.short(), speaker=speaker or "?",
                        first_ms=first_ms, total_ms=total_ms, chars=len(full),
                    )

    async def _emit_text(self, text: str) -> None:
        """Speak a fixed line (mute/unmute ack) without invoking the brain."""
        await self.push_frame(LLMFullResponseStartFrame())
        await self.push_frame(TextFrame(text=text))
        await self.push_frame(LLMFullResponseEndFrame())
        self._capture(source="bot", content=text, speaker="agent")

    async def _cancel_current_stream(self) -> None:
        task = self._stream_task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        finally:
            self._stream_task = None

    # ------------------------------------------------------------------
    # Silence responder — subscribe to T2/T3/T4 SilenceTickEvents and inject a
    # synthetic system-side prompt so the brain re-engages like a human. SOLO
    # meetings only: in a panel the humans run the meeting and must never be
    # nudged. T5 (goodbye + leave) is handled by the runner, which owns
    # teardown — see runner._watch_silence_close.
    # ------------------------------------------------------------------
    async def _listen_for_silence(self) -> None:
        assert self._bus is not None
        try:
            async for ev in self._bus.subscribe(types=(SilenceTickEvent,)):
                if not isinstance(ev, SilenceTickEvent):
                    continue
                if ev.threshold in ("T1", "T5"):
                    continue
                if not self._solo_now():
                    log.debug(
                        "[meeting-silence-responder] skipping %s (panel — humans run the meeting)",
                        ev.threshold,
                    )
                    continue
                # NOTE: we do NOT re-gate on `self._stream_task.done()` here. The
                # SilenceMonitor already gates correctly: it only emits a tick
                # when its bot_speaking flag is False (driven by the render
                # service's TTSStarted/TTSStopped + playout drain, or the
                # transport's Bot frames in audio-only mode) — so by the time a
                # tick reaches us, the agent is genuinely quiet.
                prompt = _silence_prompt(ev.threshold, self._session)
                if not prompt:
                    continue
                log.info(
                    "[meeting-silence-responder] firing %s re-engagement",
                    ev.threshold, elapsed_s=round(ev.elapsed_s, 1),
                )
                self._stream_task = asyncio.create_task(
                    self._consume_stream(prompt),
                    name=f"meeting-silence-{ev.threshold}-{self._session.short()}",
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("[meeting-silence-responder] listener crashed: %s", e)

    async def speak_system_prompt(self, prompt: str) -> None:
        """Run the brain on a synthetic [SYSTEM] prompt (spoken through the
        normal TTS/avatar path). Used by the runner for the T5 silence goodbye.
        Cancels any in-flight reply first."""
        await self._cancel_current_stream()
        self._stream_task = asyncio.create_task(
            self._consume_stream(prompt),
            name=f"meeting-sysprompt-{self._session.short()}",
        )

    async def speak_silence_close(self) -> None:
        """Speak the T5 goodbye (persona-aware, brain-generated). The runner
        calls this on the final silence threshold, waits for the speech to
        finish (BotSpeakingEvent False), then tears the meeting down."""
        prompt = _silence_prompt("T5", self._session)
        if prompt:
            await self.speak_system_prompt(prompt)

    # ------------------------------------------------------------------
    def _capture(self, *, source: str, content: str, speaker: str) -> None:
        """Best-effort episodic capture. `display_name` carries the speaker role
        so a later RECALL / the analyzer can tell who said what."""
        if not content:
            return
        try:
            episodic = get_episodic_store(self._session.conversation_id)
            episodic.append(
                make_conversation_record(
                    source=source,
                    content=content,
                    display_name=speaker if source == "user" else "agent",
                    channel=self._session.channel,
                )
            )
        except Exception as e:  # noqa: BLE001
            log.debug("[meeting-bridge] episodic capture failed", err=str(e))

    async def _safe_sink(self, sink: TranscriptSink, role: str, text: str) -> None:
        try:
            await sink(role, text)
        except Exception as e:  # noqa: BLE001
            log.debug("[meeting-bridge] transcript sink failed", err=str(e))

    async def _persist(self, role: str, text: str) -> None:
        """Fire-and-forget durable persistence of one turn (crash-safety). No-op
        when no turn_sink is wired."""
        if self._turn_sink is None or not text:
            return
        try:
            await self._turn_sink(role, text)
        except Exception as e:  # noqa: BLE001
            log.debug("[meeting-bridge] turn persist failed", err=str(e))


__all__ = ["MeetingAgentBridge", "SpeakerResolver", "TranscriptSink", "TurnSink"]
