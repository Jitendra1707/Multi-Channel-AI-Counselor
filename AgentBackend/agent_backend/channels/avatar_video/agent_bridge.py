"""AgentBridge — connects the avatar-video pipeline to the channel-agnostic
`llm_agent.run_stream` brain (fully isolated avatar implementation).

ISOLATION NOTE
--------------
This is the avatar channel's OWN bridge. It deliberately does NOT reuse the
voice channel's bridge — so changes to avatar conversation behavior can never
affect the other channels. It imports only its sibling `events` module + the
shared brain contract (`llm_agent`).

| Inbound frame                | Action                                                       |
|------------------------------|--------------------------------------------------------------|
| StartFrame                   | forward; fire BOT-SPEAKS-FIRST opener (once); start silence  |
|                              | responder listener                                           |
| BotStarted/StoppedSpeaking   | forward only (Simli-continuous-audio makes them unreliable — |
|                              | the silence gate's BotSpeakingEvent comes from the Simli     |
|                              | service's TTSStarted/TTSStopped instead)                     |
| UserStartedSpeakingFrame     | cancel in-flight brain; forward                              |
| TranscriptionFrame (final)   | dedup; ALWAYS-CLEAR (InterruptionFrame); run brain → Text    |
| All other frames             | forward unchanged                                            |

Human-simulation behaviors hosted here (parity with the voice channel):
  * Bot-speaks-first opener — the avatar greets on connect.
  * Always-clear-before-respond — every new user turn pushes InterruptionFrame
    first so replies never queue behind a residual reply (which would also
    leave the avatar lip-syncing the OLD answer via Simli).
  * Duplicate-transcript dedup — "Yes."→"Yes." doesn't cancel-restart the brain.
  * Silence responder — on T2/T3/T4 SilenceTickEvents, inject a synthetic
    system prompt so the brain re-engages in-character.
  * Episodic capture — user + bot turns recorded for later RECALL (kept from
    the avatar's previous shared bridge).
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable

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

from agent_backend.channels.avatar_video.events import (
    EventBus,
    SilenceTickEvent,
)
from agent_backend.config import get_settings
from agent_backend.infra import get_logger
from agent_backend.llm_agent import run_stream
from agent_backend.llm_agent.conversation import get_conversation
from agent_backend.llm_agent.session import Session

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Server-authoritative MUTE registry (per conversation).
# The browser's mic track.enabled=false sends digital silence, but the backend
# can still pick up comfort-noise/residual and transcribe it. When the user
# mutes, the FE also sends {type:"mute"} over the data channel; the runner's
# on_app_message handler flips this flag, and the AgentBridge drops user
# transcripts while muted — a deterministic guarantee that nothing is heard.
# ---------------------------------------------------------------------------
_MUTED: dict[str, bool] = {}


def set_muted(conversation_id: str, muted: bool) -> None:
    _MUTED[conversation_id] = muted


def is_muted(conversation_id: str) -> bool:
    return _MUTED.get(conversation_id, False)


def clear_muted(conversation_id: str) -> None:
    _MUTED.pop(conversation_id, None)


# ---------------------------------------------------------------------------
# Per-conversation bridge registry (mirrors the _MUTED pattern) — lets the
# runner's data-channel handler route a TYPED chat message ({type:"chat"}) into
# the same turn flow as a spoken utterance, for mic-less setups. Typed input
# never passes through process_frame, so the mute gate does not apply to it.
# ---------------------------------------------------------------------------
_BRIDGES: dict[str, "AgentBridge"] = {}


def handle_typed(conversation_id: str, text: str) -> None:
    """Data-channel chat message → the same turn flow as a spoken utterance."""
    bridge = _BRIDGES.get(conversation_id)
    if bridge is None or not (text or "").strip():
        return
    asyncio.create_task(
        bridge.handle_typed_text(text.strip()),
        name=f"avatar-typed-{conversation_id[:8]}",
    )


def clear_bridge(conversation_id: str) -> None:
    _BRIDGES.pop(conversation_id, None)


# ---------------------------------------------------------------------------
# Synthetic prompts injected at silence thresholds. The brain treats these as
# normal user turns and produces a real, in-character utterance through TTS →
# Simli — so the re-engagement looks/sounds like the rest of the conversation.
# (T1 is a no-op marker by SilenceMonitor design.)
# ---------------------------------------------------------------------------
# Escalating, SHORTENING check-ins. The fire only after the avatar has fully
# finished speaking AND the user stayed quiet — so each is genuinely the user's
# turn. T2→T4 get progressively shorter & simpler; T5 is the warm goodbye+hangup.
_SILENCE_PROMPTS: dict[str, str] = {
    "T2": (
        "[SYSTEM] The person went quiet after your last turn — probably still "
        "thinking. Gently re-engage in ONE short, natural sentence tied to what "
        "you just said (invite a reaction to that point, or a small follow-up "
        "about it). Warm and brief — NOT a wall of text. BANNED filler: 'take "
        "your time', 'I'm here to help', 'no rush', 'still there?', 'can you hear "
        "me?'. Don't reuse any line you've said before this call."
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
        "your earlier two. Do NOT end the call yet."
    ),
    "T5": (
        "[SYSTEM] They've stayed silent through all your check-ins. Close the "
        "call warmly in ONE short sentence: no pressure, invite them to reach "
        "back out whenever they're free or comfortable to continue (e.g. 'No "
        "worries — call me back whenever suits you and we'll pick this up.'). "
        "Phrase it freshly, then call the `end_call(reason='silence timeout')` "
        "tool. Say nothing after invoking end_call."
    ),
}


# ---------------------------------------------------------------------------
# Knowledge-capture status lines. Spoken through the normal brain/TTS path (same
# synthetic [SYSTEM]-prompt convention as the silence check-ins) so they stay in
# persona. STRICT-KB decision: the fact is NOT used until it's approved on the
# Knowledge Review screen, so the ack must not promise to use the new value.
_KNOWLEDGE_ACK_PROMPT = (
    "[SYSTEM] The director just dictated a knowledge update for the knowledge "
    "base (a Capture, not a question). In ONE or two short sentences: confirm "
    "you've recorded it and are checking it against the knowledge base, and that "
    "they can review and approve it on the Knowledge Review screen. Do NOT "
    "answer, discuss, or judge the statement itself, do NOT restate it, and do "
    "NOT promise to use the new information — it only takes effect once "
    "approved. Then wait for them to continue."
)
_KNOWLEDGE_FAIL_PROMPT = (
    "[SYSTEM] The knowledge capture the director just dictated could NOT be "
    "recorded (no clear fact was extracted). In ONE short, warm sentence tell "
    "them it didn't come through as a clear statement and ask them to press "
    "Capture and say it again. Do not apologise at length."
)


def _silence_prompt(threshold: str, session: Session) -> str | None:
    """Re-engagement prompt for a silence threshold, PERSONA-AWARE.

    Reads `silence_prompts.<threshold>` from the active persona JSON (resolved by
    channel) if present — so the director presenter gets briefing-appropriate,
    unhurried check-ins instead of the counsellor's sales-call phrasing. Falls
    back to the counsellor defaults above when the persona doesn't define them.
    """
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
        log.debug("[avatar-silence] persona silence prompt lookup failed", err=str(e)[:160])
    return _SILENCE_PROMPTS.get(threshold)


class AgentBridge(FrameProcessor):
    """One instance per avatar-video session.

    Owns the in-flight brain task, the opener-fired flag, the optional silence
    responder listener, and threads the session through every `run_stream`.
    """

    def __init__(
        self,
        *,
        session: Session,
        bus: EventBus | None = None,
        speak_opener: bool = True,
        transcript_sink: "Callable[[str, str], None] | None" = None,
    ) -> None:
        super().__init__()
        self._session = session
        self._bus = bus
        self._speak_opener = speak_opener
        # Optional callback(role, text) — role is "user" | "assistant". Used to
        # stream the live transcript to the browser (over the WebRTC data
        # channel). None = no transcript delivery (kept fully optional so the
        # bridge has no transport coupling).
        self._transcript_sink = transcript_sink
        self._silence_listener_task: asyncio.Task | None = None
        self._stream_task: asyncio.Task | None = None
        # True once the in-flight reply has pushed its first token (i.e. audio is
        # actually flowing to TTS/Simli). Gates the eager barge cancel: a bare VAD
        # trip while the brain is still COMPOSING (no audio yet) must not kill the
        # turn — only a real talk-over while we're speaking should.
        self._reply_started = False
        self._opener_fired = False
        # Dedup the most recent user transcript so a duplicate doesn't
        # cancel-and-restart the brain on identical input.
        self._last_user_text: str | None = None
        # Reachable by conversation_id for typed chat input (runner data channel).
        _BRIDGES[session.conversation_id] = self

    # ------------------------------------------------------------------
    # Pipecat hook
    # ------------------------------------------------------------------
    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        # Pipeline started — forward StartFrame, then fire the opener once and
        # start the silence responder (if a bus is attached + flag on).
        if isinstance(frame, StartFrame):
            await self.push_frame(frame, direction)
            if self._speak_opener and not self._opener_fired:
                self._opener_fired = True
                self._stream_task = asyncio.create_task(
                    self._speak_opener_task(),
                    name=f"avatar-opener-{self._session.short()}",
                )
            if (
                self._bus is not None
                and get_settings().avatar_enable_silence_responder
                and self._silence_listener_task is None
            ):
                self._silence_listener_task = asyncio.create_task(
                    self._listen_for_silence(),
                    name=f"avatar-silence-listener-{self._session.short()}",
                )
            return

        # NOTE: we deliberately do NOT derive the silence monitor's bot-speaking
        # signal from these audio frames. Simli streams CONTINUOUS audio (it
        # emits idle/silence frames between utterances via handleSilence), so
        # the output transport's BotStoppedSpeakingFrame is unreliable here — it
        # can fire late or never, which left the silence monitor un-ARMED and no
        # follow-up nudges ever fired. The authoritative BotSpeakingEvent is now
        # published from the brain reply lifecycle (_consume_stream / opener).
        # We still forward these frames downstream so Simli/UI behave normally.
        if isinstance(frame, (BotStartedSpeakingFrame, BotStoppedSpeakingFrame)):
            await self.push_frame(frame, direction)
            return

        # User starts speaking → cancel the in-flight brain ONLY if the avatar is
        # actually emitting audio (real talk-over, or a barge the BargeInManager
        # confirmed while we were speaking and released to us). If the brain is
        # still COMPOSING (no token emitted yet), do NOT cancel: a bare VAD trip
        # here is usually a transport audio-frame-timeout flap with no transcript
        # behind it, and cancelling would silently kill a turn the user is waiting
        # on. A genuine new utterance still lands as a transcript, and
        # _handle_transcript cancels + restarts (or merges) the brain there.
        if isinstance(frame, UserStartedSpeakingFrame):
            if self._reply_started:
                await self._cancel_current_stream()
            await self.push_frame(frame, direction)
            return

        # Final transcript — run the brain.
        if isinstance(frame, TranscriptionFrame):
            text = (frame.text or "").strip()
            if not text:
                return
            # Server-authoritative MUTE: when the user muted from the FE, drop
            # the transcript entirely — the avatar must not hear or respond to
            # anything while muted, even if comfort-noise slipped past the
            # browser's track.enabled=false.
            if is_muted(self._session.conversation_id):
                log.info(
                    "[avatar-bridge] dropping transcript (muted)",
                    session=self._session.short(), text=text,
                )
                return
            if (
                text == self._last_user_text
                and self._stream_task is not None
                and not self._stream_task.done()
            ):
                log.info(
                    "[avatar-bridge] ignoring duplicate transcript",
                    session=self._session.short(),
                    text=text,
                )
                return

            # MULTI-SEGMENT MERGE: STT (esp. Azure with tight segmentation) can
            # split ONE spoken question into two finals a fraction of a second
            # apart ("what are the fees" + "for the CS program"). If the SECOND
            # final lands while the brain is STILL answering the FIRST, the user
            # almost certainly meant ONE question — so we COMBINE both parts and
            # answer them together, instead of cancelling the first and answering
            # only the second (which dropped the first part). We only merge while
            # the first answer is still in-flight (`brain_running`): a follow-up
            # after the avatar already replied is a genuine new turn, not a split.
            brain_running = (
                self._stream_task is not None and not self._stream_task.done()
            )
            if brain_running and self._last_user_text and text != self._last_user_text:
                combined = f"{self._last_user_text} {text}".strip()
                log.info(
                    "[avatar-bridge] merging follow-up segment (split utterance)",
                    session=self._session.short(),
                    first=self._last_user_text, second=text, combined=combined,
                )
                text = combined

            self._last_user_text = text
            # Knowledge capture: if the director ARMED a capture, THIS utterance
            # is the knowledge statement — divert it to the capture pipeline and
            # speak a status acknowledgment instead of a conversational reply.
            # Otherwise fire the flag-gated auto detector (no-op unless
            # KNOWLEDGE_AUTODETECT=1; runs off this path).
            try:
                from agent_backend.channels.avatar_video import knowledge as _kc

                if _kc.take_armed(self._session.conversation_id):
                    await self._divert_to_knowledge(text)
                    return
                _kc.maybe_autodetect(self._session.conversation_id, self._session, text)
            except Exception as e:  # noqa: BLE001
                log.debug("[avatar-bridge] knowledge arm/autodetect failed", err=str(e))
            await self._handle_transcript(text)
            return

        # Everything else passes through untouched.
        await self.push_frame(frame, direction)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def handle_typed_text(self, text: str) -> None:
        """A TYPED chat message from the data channel — the typed twin of the
        finalized-transcript path in `process_frame`. No split-utterance merge,
        no mute gate (typed input works with a dead/muted mic). `echo_user=False`
        throughout: the FE already echoed the typed message as a chat bubble.
        An armed knowledge capture consumes a typed statement exactly like a
        spoken one, so the whole capture flow works mic-less."""
        self._last_user_text = text
        try:
            from agent_backend.channels.avatar_video import knowledge as _kc

            if _kc.take_armed(self._session.conversation_id):
                await self._divert_to_knowledge(text, echo_user=False)
                return
            _kc.maybe_autodetect(self._session.conversation_id, self._session, text)
        except Exception as e:  # noqa: BLE001
            log.debug("[avatar-bridge] knowledge arm/autodetect failed (typed)", err=str(e))
        await self._handle_transcript(text, echo_user=False)

    async def _handle_transcript(self, text: str, *, echo_user: bool = True) -> None:
        """A finalised user utterance landed — kick off the brain.

        ALWAYS-CLEAR: push InterruptionFrame BEFORE running the new brain so the
        TTS service AND the Simli buffer (clearBuffer) are cleared — otherwise
        the avatar would finish lip-syncing the previous reply before starting
        the new one ("robot queue"). Idempotent: no-op when nothing to clear.
        """
        log.info("[avatar-bridge] >>> USER", session=self._session.short(), text=text)
        # 1. Cancel any in-flight brain task FIRST. On a split utterance we've
        #    already merged its text into `text`, so the old run is superseded.
        await self._cancel_current_stream()
        # 2. Record the user turn to conversation memory HERE (not inside
        #    run_stream). run_stream's own append happens AFTER a ~50-300ms RAG
        #    step, so a fast cancel (split utterance / barge) can kill the run
        #    before it records — losing that turn from history. Recording here is
        #    cancellation-proof. We pass record_user=False below so run_stream
        #    doesn't double-record. If this exact text is already the last user
        #    message (the cancelled first-part run beat us to it), skip to avoid
        #    a duplicate.
        try:
            convo = get_conversation(self._session.conversation_id)
            recent = convo.recent(n=1)
            last_is_same = bool(
                recent and getattr(recent[-1], "type", "") == "human"
                and (getattr(recent[-1], "content", "") or "").strip() == text
            )
            if not last_is_same:
                convo.append_user(text)
        except Exception as e:  # noqa: BLE001
            log.debug("[avatar-bridge] convo append_user failed", err=str(e))
        # 3. Capture to episodic memory (RECALL) + push to browser transcript
        #    (skipped for typed input — the FE already echoed the chat bubble).
        self._capture_episodic(source="user", content=text)
        if echo_user:
            self._emit_transcript("user", text)
        # 4. Clear downstream TTS + Simli so a buffered reply stops immediately.
        await self.push_frame(InterruptionFrame())
        # 5. Start the new brain run (record_user=False — we recorded above).
        self._stream_task = asyncio.create_task(
            self._consume_stream(text),
            name=f"avatar-brain-{self._session.short()}",
        )

    async def _divert_to_knowledge(self, text: str, *, echo_user: bool = True) -> None:
        """An ARMED knowledge statement landed — record it like any user turn
        (history / episodic / browser transcript), but run the capture pipeline
        and a spoken status acknowledgment INSTEAD of a conversational reply.
        Mirrors `_handle_transcript`'s side-effect sequence minus the brain."""
        log.info("[avatar-bridge] >>> KNOWLEDGE", session=self._session.short(), text=text)
        await self._cancel_current_stream()
        try:
            convo = get_conversation(self._session.conversation_id)
            recent = convo.recent(n=1)
            last_is_same = bool(
                recent and getattr(recent[-1], "type", "") == "human"
                and (getattr(recent[-1], "content", "") or "").strip() == text
            )
            if not last_is_same:
                convo.append_user(text)
        except Exception as e:  # noqa: BLE001
            log.debug("[avatar-bridge] convo append_user failed", err=str(e))
        self._capture_episodic(source="user", content=text)
        if echo_user:
            self._emit_transcript("user", text)
        await self.push_frame(InterruptionFrame())
        self._stream_task = asyncio.create_task(
            self._knowledge_flow(text),
            name=f"avatar-knowledge-{self._session.short()}",
        )

    async def _knowledge_flow(self, text: str) -> None:
        """Run the capture pipeline concurrently with the spoken ack; speak the
        failure line if no candidate came out. The capture task is independent —
        a barge-in cancels the SPEECH, not the capture itself."""
        from agent_backend.channels.avatar_video import knowledge as _kc

        capture_task = asyncio.create_task(
            _kc.capture_armed(self._session.conversation_id, self._session, text),
            name=f"avatar-kcapture-{self._session.short()}",
        )
        await self._consume_stream(_KNOWLEDGE_ACK_PROMPT)
        try:
            ok = await capture_task
        except Exception as e:  # noqa: BLE001
            log.warning("[avatar-bridge] knowledge capture crashed", err=str(e)[:200])
            ok = False
        if not ok:
            await self._consume_stream(_KNOWLEDGE_FAIL_PROMPT)

    async def _consume_stream(self, text: str) -> None:
        """Pull tokens from the brain and push each as a TextFrame downstream."""
        t0 = time.monotonic()
        # Reset per run — flips True on the first emitted token (audio now flowing),
        # which is what arms the eager barge cancel for genuine talk-over.
        self._reply_started = False
        await self.push_frame(LLMFullResponseStartFrame())
        bot_chunks: list[str] = []
        try:
            async for tok in run_stream(
                text,
                channel=self._session.channel,
                session=self._session,
                # The bridge already recorded the user turn in conversation
                # memory (cancellation-proof). Don't double-record here.
                record_user=False,
            ):
                bot_chunks.append(tok)
                self._reply_started = True
                await self.push_frame(TextFrame(text=tok))
        except asyncio.CancelledError:
            log.info("[avatar-bridge] brain cancelled (barge-in)", session=self._session.short())
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("[avatar-bridge] brain failed", session=self._session.short(), err=str(e))
        finally:
            await self.push_frame(LLMFullResponseEndFrame())
            text_full = "".join(bot_chunks).strip()
            if text_full:
                log.info(
                    "[avatar-bridge] <<< BOT",
                    session=self._session.short(),
                    tokens=len(bot_chunks),
                    ms=int((time.monotonic() - t0) * 1000),
                    text=text_full,
                )
                # Capture the bot turn (full reply OR interrupted partial) +
                # push it to the browser transcript panel.
                self._capture_episodic(source="bot", content=text_full)
                self._emit_transcript("assistant", text_full)

    async def _speak_opener_task(self) -> None:
        """Bot-speaks-first — fired on pipeline start.

        WARM-UP: we do NOT speak the instant StartFrame propagates. At that
        moment ICE may still be completing, Simli's WebRTC leg + send buffer are
        still priming, and the output transport hasn't fully started — so the
        FIRST audio chunks get dropped and the greeting's opening words clip
        ("...lo, I'm Aisha" instead of "Hello, I'm Aisha"). We wait a short,
        bounded warm-up so the whole path is live before the first real audio.
        This also feels natural — a person takes a beat before speaking.
        """
        t0 = time.monotonic()
        warmup_s = get_settings().avatar_opener_warmup_s
        if warmup_s > 0:
            await asyncio.sleep(warmup_s)

        # Late import — keep the opener module out of the cold-start surface.
        from agent_backend.llm_agent.openers import render_opener
        opener = render_opener(self._session)

        log.info("[avatar-bridge] <<< OPENER", session=self._session.short(), text=opener)

        # Persist to conversation memory so the next turn's brain prompt sees it
        # as an AIMessage — prevents the brain from re-greeting.
        with contextlib.suppress(Exception):
            get_conversation(self._session.conversation_id).append_bot(opener)
        self._capture_episodic(source="bot", content=opener)
        self._emit_transcript("assistant", opener)

        await self.push_frame(LLMFullResponseStartFrame())
        await self.push_frame(TextFrame(text=opener))
        await self.push_frame(LLMFullResponseEndFrame())

        log.info(
            "[avatar-bridge] opener queued",
            session=self._session.short(),
            warmup_s=warmup_s,
            ms=int((time.monotonic() - t0) * 1000),
        )

    async def _cancel_current_stream(self) -> None:
        """Cancel + await the in-flight stream task so the next turn starts clean.

        Awaiting after cancel() guarantees LLMFullResponseEndFrame is pushed
        before the next turn — so TTS isn't left mid-utterance.
        """
        task = self._stream_task
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        self._stream_task = None

    # ------------------------------------------------------------------
    # Silence responder — subscribe to T2/T3/T4 SilenceTickEvents and inject a
    # synthetic system-side prompt so the brain re-engages like a human.
    # ------------------------------------------------------------------
    async def _listen_for_silence(self) -> None:
        assert self._bus is not None
        try:
            async for ev in self._bus.subscribe(types=(SilenceTickEvent,)):
                if not isinstance(ev, SilenceTickEvent):
                    continue
                if ev.threshold == "T1":
                    continue
                # NOTE: we do NOT re-gate on `self._stream_task.done()` here. That
                # task completes when token-pushing finishes (~ms), but Simli keeps
                # PLAYING the reply audio for seconds afterward — so it's a stale
                # signal that wrongly let early nudges fire over the avatar's voice
                # AND (worse) could swallow a threshold. The SilenceMonitor already
                # gates correctly: it only emits a SilenceTickEvent when its
                # bot_speaking flag is False, and that flag is now driven by the
                # Simli service's TTSStarted/TTSStopped (+playout drain). So by the
                # time a tick reaches us, the avatar is genuinely quiet.
                prompt = _silence_prompt(ev.threshold, self._session)
                if not prompt:
                    continue
                log.info(
                    "[avatar-silence-responder] firing %s re-engagement",
                    ev.threshold, elapsed_s=round(ev.elapsed_s, 1),
                )
                self._stream_task = asyncio.create_task(
                    self._consume_stream(prompt),
                    name=f"avatar-silence-{ev.threshold}-{self._session.short()}",
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("[avatar-silence-responder] listener crashed: %s", e)

    # ------------------------------------------------------------------
    # Episodic memory capture — best-effort; never blocks the audio path.
    # ------------------------------------------------------------------
    def _emit_transcript(self, role: str, text: str) -> None:
        """Best-effort push of a transcript line to the browser (data channel).
        role: 'user' | 'assistant'. Never raises into the audio path."""
        if not self._transcript_sink or not text:
            return
        try:
            self._transcript_sink(role, text)
        except Exception as e:  # noqa: BLE001
            log.debug("[avatar-bridge] transcript sink failed", err=str(e))

    def _capture_episodic(self, *, source: str, content: str) -> None:
        if not content:
            return
        try:
            from agent_backend.llm_agent.memory import get_episodic_store
            from agent_backend.llm_agent.memory.episodic import make_conversation_record
            episodic = get_episodic_store(self._session.conversation_id)
            episodic.append(
                make_conversation_record(
                    source=source,
                    content=content,
                    display_name=self._session.display_name if source == "user" else None,
                    channel=self._session.channel,
                )
            )
        except Exception as e:  # noqa: BLE001
            log.debug(
                "[avatar-bridge] episodic capture failed",
                session=self._session.short(), source=source, err=str(e),
            )


__all__ = ["AgentBridge"]
