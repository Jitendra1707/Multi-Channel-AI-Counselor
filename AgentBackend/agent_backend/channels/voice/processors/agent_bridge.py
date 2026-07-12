"""AgentBridge — the Pipecat FrameProcessor that connects the voice pipeline
to the channel-agnostic `llm_agent.run_stream` brain.

| Inbound frame                 | Action                                                       |
|-------------------------------|--------------------------------------------------------------|
| StartFrame                    | forward; fire the BOT-SPEAKS-FIRST opener (once per call)    |
| TranscriptionFrame (final)    | cancel any in-flight stream; run_stream → emit TextFrames    |
| UserStartedSpeakingFrame      | barge-in: cancel in-flight stream; forward                   |
| All other frames              | forward unchanged                                            |

The bridge OWNS the in-flight brain task for this conversation, so when
the user starts speaking mid-bot-reply, we cancel cleanly. Pipecat's TTS
service stops emitting audio frames as soon as TextFrames stop flowing,
and the InterruptionFrame the transport emits also makes ACS clear its
playback buffer immediately (see `serializer.py` → StopAudio envelope).

Bot-speaks-first: the moment the pipeline starts (`StartFrame`), we kick
off `open_call(session)` and pipe its tokens downstream as `TextFrame`s.
The candidate hears the opener within a few hundred ms of pickup — no need
to say "hello?" first to discover who's calling.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from typing import Callable, Optional

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

from agent_backend.channels.voice.events import BotSpeakingEvent, EventBus, SilenceTickEvent
from agent_backend.config import get_settings
from agent_backend.data import LeadRepo
from agent_backend.infra import get_logger
from agent_backend.llm_agent import run_stream
from agent_backend.llm_agent.conversation import get_conversation
from agent_backend.llm_agent.session import Session
from agent_backend.llm_agent.tools.voice.end_call import hangup_pending

log = get_logger(__name__)


# Type aliases so tests can swap in fake streamers without touching shape.
StreamFactory = Callable[..., AsyncIterator[str]]


# ---------------------------------------------------------------------------
# Synthetic prompts injected at silence thresholds. The brain treats these
# like normal user turns (HumanMessage on the LLM) and produces a real,
# in-character utterance through TTS — so the re-engagement sounds like the
# rest of the conversation, not a stock phrase.
#
# T1 is intentionally absent (no-op marker per SilenceMonitor design).
# T4 = graceful close: the brain says goodbye; the `end_call` tool fires
# shortly after via the playbook (or the operator can wire an auto-hangup).
# ---------------------------------------------------------------------------
_SILENCE_PROMPTS: dict[str, str] = {
    # T2 is the FIRST nudge — soft and patient. The candidate may be thinking
    # (especially after a long explanation). Don't ask "still there?" — it
    # sounds rude. Instead, offer help, invite questions, or reassure.
    "T2": (
        "[SYSTEM] The candidate has gone quiet — likely thinking. Offer a "
        "gentle, patient nudge in ONE short sentence. DO NOT ask 'still "
        "there?' or 'can you hear me?' — those sound rude after a candidate "
        "was clearly engaged. Examples (pick the spirit, not the words): "
        "'Take your time.', 'Any questions on that so far?', "
        "'No rush — happy to clarify anything.'"
    ),
    # T3 is the SECOND check, ~15s in. If they're STILL silent after T2, they
    # may have stepped away. Ask more directly — but still warmly.
    "T3": (
        "[SYSTEM] The candidate has been silent for a while. Re-engage in "
        "ONE short sentence — check in directly but warmly. Don't apologise, "
        "don't repeat the opener. Examples: 'Just checking — were you still "
        "with me?', 'Lost you for a moment — should we continue?'"
    ),
    # T4 is the graceful exit — the line is probably dead.
    "T4": (
        "[SYSTEM] The candidate has been silent for too long. The line may "
        "be dead. Close the call POLITELY in ONE sentence saying you'll "
        "WhatsApp them and try again later. Then call the "
        "`end_call(reason='silence timeout')` tool. Do not say anything "
        "after invoking end_call."
    ),
}


# ---------------------------------------------------------------------------
# GOODBYE SAFETY NET. The model is supposed to invoke the `end_call` tool in the
# SAME turn it speaks a closing line — but it intermittently speaks the goodbye
# and FORGETS the tool call, leaving the line open until the silence timers fire
# (the "...take care and goodbye." → then 8s/18s silence in the logs). This
# detector recognises a genuine TERMINAL sign-off in the bot's spoken text so the
# bridge can fire the same graceful hangup the tool would have. It must be
# PRECISE — only true farewells, never mid-call pleasantries like "take care of
# it" or "I'll send that, all good?".
#
# Strategy: require a real PARTING phrase ("goodbye", "bye for now", "take care"
# as a sign-off, "have a good day/evening", "all the best ... bye"). A bare
# "take care" is only treated as terminal when it co-occurs with another parting
# cue OR sits at the very end of the utterance (where a human only says it to
# close). Anything ending in a QUESTION is never terminal — the bot is still
# driving the conversation.
_PARTING_STRONG: tuple[str, ...] = (
    "goodbye", "bye for now", "good bye", "talk to you then", "talk soon",
    "talk later", "have a great day", "have a good day", "have a lovely day",
    "have a good evening", "have a lovely evening", "have a nice day",
    "all the best with your application",
)
# Softer cues — terminal only when paired (e.g. "take care" + "all the best").
_PARTING_SOFT: tuple[str, ...] = ("take care", "all the best", "see you")


def _is_terminal_goodbye(text: str) -> bool:
    """True if the bot's spoken text is a genuine end-of-call sign-off (so the
    call should be torn down), False for mid-call pleasantries or any turn that
    ends by asking a question."""
    t = (text or "").strip().lower()
    if not t:
        return False
    # Still steering the conversation → not a goodbye, regardless of wording.
    if t.endswith("?"):
        return False
    if any(p in t for p in _PARTING_STRONG):
        return True
    # Soft cues: need two distinct ones, OR one sitting in the closing clause.
    soft_hits = [p for p in _PARTING_SOFT if p in t]
    if len(soft_hits) >= 2:
        return True
    if soft_hits:
        # "take care" / "all the best" only count as a farewell when they're at
        # the tail of the utterance (the last ~40 chars) — i.e. used to close,
        # not "take care of it" buried mid-sentence.
        tail = t[-40:]
        if any(p in tail for p in soft_hits):
            return True
    return False


class AgentBridge(FrameProcessor):
    """One instance per active Pipecat pipeline (i.e. per call).

    Owns the in-flight brain task, the opener-fired flag, and threads the
    session through every `run_stream` call.
    """

    def __init__(
        self,
        *,
        session: Session,
        delegate_barge_in: bool = False,
        bus: EventBus | None = None,
    ) -> None:
        super().__init__()
        self._session = session
        self._bus = bus
        # Silence-responder subscription is created on StartFrame so we hook
        # into the asyncio loop that's actually running the pipeline.
        self._silence_listener_task: asyncio.Task | None = None
        # The running consumer task, if a turn is mid-flight. Held so a
        # barge-in (UserStartedSpeakingFrame) or a new turn can cancel it.
        self._stream_task: asyncio.Task | None = None
        self._opener_fired = False
        # Bot-speaking state for the barge-in grace period. When the TTS
        # service starts emitting audio it pushes BotStartedSpeakingFrame
        # downstream; we record the timestamp and suppress UserStartedSpeakingFrame
        # for the first BARGE_IN_GRACE_S seconds of bot speech. This prevents
        # acoustic echo from the candidate's phone speaker → mic from being
        # mis-identified as user speech and instantly cancelling the bot.
        #
        # If `delegate_barge_in=True`, the new BargeInManager (downstream)
        # owns barge-in decisions and this flat-grace logic stays inert. We
        # still track _bot_speaking_since for observability but never use it
        # to gate frames.
        self._bot_speaking_since: float | None = None
        self._delegate_barge_in = delegate_barge_in
        # Dedup the most recent user transcript so a duplicate ("Yes." → "Yes.")
        # doesn't cancel-and-restart the brain task on identical input.
        self._last_user_text: str | None = None

        # --- Latency-masking FILLER state ---
        # A RAG tool can speak a "let me check…" filler the moment retrieval
        # starts (see llm_agent.filler_speaker). We push it as a normal spoken
        # utterance, and gate the real answer behind it so they never overlap:
        # `_filler_done` is cleared when a filler starts and SET when the bot
        # stops speaking it; `_consume_stream` awaits it before the first answer
        # token. Idempotent + bounded so a missing stop-signal can't deadlock.
        self._filler_in_flight = False
        self._filler_done = asyncio.Event()
        self._filler_done.set()  # nothing pending initially

    # How long after `BotStartedSpeakingFrame` we suppress BOTH the
    # UserStartedSpeakingFrame AND any InterruptionFrame the framework emits
    # automatically (Pipecat's `allow_interruptions=True` pushes InterruptionFrame
    # downstream as soon as VAD trips — that's what's cancelling TTS synthesis
    # mid-utterance when the candidate's phone speaker echoes the bot's audio
    # back into the mic). 1.2 sec is enough to clear the first burst of echo
    # on a typical mobile speaker without blocking real user barge-in for long.
    BARGE_IN_GRACE_S: float = 1.2

    # Spoken when end_call fired but the model produced NO closing line, so the
    # caller hears a warm sign-off instead of dead air before the line drops.
    # Deliberately short + generic (no lead name / next-step) since we don't
    # know the conversation specifics at this point.
    _FALLBACK_GOODBYE: str = "Thanks so much for your time today — take care, bye for now!"

    # ------------------------------------------------------------------
    # Pipecat hook
    # ------------------------------------------------------------------
    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        # Pipeline just started — forward StartFrame downstream, then fire
        # the bot-speaks-first opener exactly once.
        if isinstance(frame, StartFrame):
            await self.push_frame(frame, direction)
            if not self._opener_fired:
                self._opener_fired = True
                self._stream_task = asyncio.create_task(
                    self._speak_opener(),
                    name=f"opener-{self._session.short()}",
                )
            # Register the latency-masking filler speaker so a RAG tool can speak
            # "let me check…" the moment retrieval starts. Runs on THIS loop.
            try:
                from agent_backend.llm_agent.filler_speaker import register_filler_speaker

                register_filler_speaker(self._session.conversation_id, self._speak_filler)
            except Exception as e:  # noqa: BLE001
                log.debug("[agent-bridge] filler speaker register failed", err=str(e)[:120])
            # Start the silence-responder listener if a bus is attached + flag on.
            if (
                self._bus is not None
                and get_settings().enable_silence_responder
                and self._silence_listener_task is None
            ):
                self._silence_listener_task = asyncio.create_task(
                    self._listen_for_silence(),
                    name=f"silence-listener-{self._session.short()}",
                )
            return

        # Track when the bot starts/stops actually producing audio. The TTS
        # service pushes these frames around its synthesis lifecycle. The
        # timestamp drives the barge-in grace window below.
        #
        # Also publishes BotSpeakingEvent to the bus (when one is attached) so
        # SilenceMonitor can GATE its threshold timer on bot-speaking state —
        # otherwise T2 (5s) fires while the bot is still mid-explanation,
        # which is the "still there?" mid-sentence bug.
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking_since = time.monotonic()
            if self._bus is not None:
                self._bus.publish(BotSpeakingEvent(speaking=True))
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking_since = None
            if self._bus is not None:
                self._bus.publish(BotSpeakingEvent(speaking=False))
            # A filler that was in flight has now finished playing → release the
            # gate so the queued real answer can start (no overlap).
            if self._filler_in_flight:
                self._filler_in_flight = False
                self._filler_done.set()
            await self.push_frame(frame, direction)
            return

        # Acoustic-echo guard. When `allow_interruptions=True` is set on the
        # PipelineTask (it is), Pipecat's input transport automatically pushes
        # BOTH a UserStartedSpeakingFrame (informational) AND an InterruptionFrame
        # (which the TTS service hears and cancels mid-synthesis, and which the
        # serializer turns into ACS StopAudio).
        #
        # During the first BARGE_IN_GRACE_S of bot speech, both events are very
        # likely echo from the candidate's phone speaker → mic loop, not a real
        # user interruption. We swallow BOTH frames here so neither the TTS nor
        # the serializer act on them. After the grace window, real user speech
        # / interruption flows through normally and barge-in works as designed.
        if isinstance(frame, (UserStartedSpeakingFrame, InterruptionFrame)):
            # When the BargeInManager owns barge-in (downstream), we skip the
            # flat-grace logic here entirely. Real user speech still cancels
            # the in-flight brain task; the Manager's confirmation window
            # decides whether the frame propagates further.
            if not self._delegate_barge_in and self._bot_speaking_since is not None:
                since = time.monotonic() - self._bot_speaking_since
                if since < self.BARGE_IN_GRACE_S:
                    log.info(
                        "[agent-bridge] suppressing %s (echo-grace, bot speaking %dms)",
                        type(frame).__name__,
                        int(since * 1000),
                    )
                    # Don't push the frame — downstream (TTS, serializer)
                    # must not see it during the grace window.
                    return
            # Real user speech (or interruption raised programmatically) —
            # let it through and cancel any in-flight brain task.
            if isinstance(frame, UserStartedSpeakingFrame):
                await self._cancel_current_stream()
            await self.push_frame(frame, direction)
            return

        # Final transcript — run the brain.
        if isinstance(frame, TranscriptionFrame):
            text = (frame.text or "").strip()
            if not text:
                return
            # Dedup: if the previous turn was the same text and a brain is
            # still running on it, ignore. Stops the "Yes." → "Yes." (640 ms
            # later) gridlock where each duplicate cancels the previous
            # brain before it can finish.
            if (
                text == self._last_user_text
                and self._stream_task is not None
                and not self._stream_task.done()
            ):
                log.info(
                    "[agent-bridge] ignoring duplicate transcript",
                    session=self._session.short(),
                    text=text,
                )
                return
            self._last_user_text = text
            await self._handle_transcript(text)
            return

        # Everything else passes through untouched (interim transcripts,
        # audio frames, metric frames, end frames, ...).
        await self.push_frame(frame, direction)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _handle_transcript(self, text: str) -> None:
        """A finalised user utterance landed — kick off the brain.

        Every new user turn ALWAYS forces a full pipeline interruption before
        starting a new brain run. This is the industry-standard "always
        clear before responding" pattern (Retell, Vapi, LiveKit, ElevenLabs
        Conversational all do this). Without it, the bot QUEUES replies:

          1. User asks Q1 → brain generates 50 tokens → TTS buffers them.
          2. User asks Q2 just as brain finishes Q1's last token.
          3. AgentBridge cancels the brain task (no-op — already done).
          4. AgentBridge runs new brain on Q2.
          5. Q2 tokens APPEND to the TTS buffer behind Q1.
          6. Bot speaks Q1 in full, THEN Q2 — sounds like robot queue.

        Pushing InterruptionFrame BEFORE running the new brain clears the
        TTS service AND transport buffers, so Q2's tokens replace Q1's
        residual audio immediately. Idempotent: no-op when there's nothing
        to interrupt. With this, every new user turn cuts off the prior
        reply mid-stream — which is how human counsellors handle being
        asked a new question.
        """
        log.info(
            "[agent-bridge] >>> USER",
            session=self._session.short(),
            text=text,
        )
        # 1. Cancel any in-flight brain task (no-op if already complete).
        await self._cancel_current_stream()
        # 2. Cancel the downstream TTS pipeline + transport audio queue so
        #    a previously-buffered reply doesn't keep playing. The
        #    BargeInManager fires InterruptionFrame during 'holding' state
        #    transitions, but NOT in this path — when STT delivers a final
        #    after BargeInManager already exited holding (e.g. transition
        #    back to idle), there's no other source of InterruptionFrame.
        #    So we emit it here, unconditionally, on every new user turn.
        await self.push_frame(InterruptionFrame())
        # 3. Start the new brain run.
        self._stream_task = asyncio.create_task(
            self._consume_stream(text),
            name=f"brain-{self._session.short()}",
        )

    def _speak_filler(self, line: str) -> None:
        """Speak a latency-masking filler line NOW (called by a RAG tool the
        moment retrieval starts). Pushes it as a self-contained spoken utterance
        and ARMS the answer-gate: `_filler_done` is cleared and only re-set when
        the bot stops speaking this filler (BotStoppedSpeakingFrame), so the real
        answer queues behind it instead of overlapping. Schedules the push as a
        task because the caller (the tool) is mid-await on this same loop."""
        line = (line or "").strip()
        if not line:
            return
        self._filler_in_flight = True
        self._filler_done.clear()

        async def _push() -> None:
            try:
                await self.push_frame(LLMFullResponseStartFrame())
                await self.push_frame(TextFrame(text=line + " "))
                await self.push_frame(LLMFullResponseEndFrame())
            except Exception as e:  # noqa: BLE001
                log.debug("[agent-bridge] filler push failed", err=str(e)[:120])
                # Don't leave the gate stuck closed if the push failed.
                self._filler_in_flight = False
                self._filler_done.set()

        asyncio.create_task(_push(), name=f"filler-{self._session.short()}")

    async def _await_filler_done(self) -> None:
        """Block until any in-flight filler has finished playing, so the answer
        never overlaps it. Bounded so a missing stop-signal can't deadlock the
        turn — after the cap we proceed anyway (the answer just follows sooner)."""
        if self._filler_done.is_set():
            return
        try:
            await asyncio.wait_for(self._filler_done.wait(), timeout=8.0)
        except asyncio.TimeoutError:
            log.info("[agent-bridge] filler gate timed out — proceeding", session=self._session.short())
            self._filler_in_flight = False
            self._filler_done.set()

    async def _consume_stream(self, text: str) -> None:
        """Pull tokens from the brain and push each as a TextFrame downstream
        so the TTS service picks them up immediately.

        If a RAG tool spoke a filler during this turn, the FIRST answer token
        waits for that filler to finish playing (so they don't overlap); the
        rest stream normally."""
        t0 = time.monotonic()
        await self.push_frame(LLMFullResponseStartFrame())
        bot_chunks: list[str] = []
        first_token = True
        try:
            async for tok in run_stream(
                text,
                channel=self._session.channel,
                session=self._session,
            ):
                if first_token:
                    first_token = False
                    # Queue behind the filler if one is mid-flight.
                    await self._await_filler_done()
                bot_chunks.append(tok)
                await self.push_frame(TextFrame(text=tok))
        except asyncio.CancelledError:
            log.info(
                "[agent-bridge] brain cancelled (barge-in)",
                session=self._session.short(),
            )
            raise
        except Exception as e:  # noqa: BLE001
            log.warning(
                "[agent-bridge] brain failed",
                session=self._session.short(),
                err=str(e),
            )
        finally:
            await self.push_frame(LLMFullResponseEndFrame())
            text_full = "".join(bot_chunks).strip()

            # EMPTY-GOODBYE FALLBACK. The model is supposed to SPEAK a closing
            # line and THEN call end_call in the same turn — but it sometimes
            # calls the tool and emits NO text (tokens=0). end_call has already
            # scheduled a graceful hangup that's now waiting for a goodbye to
            # play; with nothing spoken the caller hears dead air until the
            # waiter's cap, and the silence-monitor would otherwise nudge. So if
            # a hangup is pending and we produced no text, speak a short, warm
            # fallback sign-off so the close still sounds human.
            if (
                not text_full
                and self._session.channel in ("voice", "avatar_video")
                and hangup_pending(self._session.conversation_id)
            ):
                text_full = self._FALLBACK_GOODBYE
                with contextlib.suppress(Exception):
                    get_conversation(self._session.conversation_id).append_bot(text_full)
                await self.push_frame(LLMFullResponseStartFrame())
                await self.push_frame(TextFrame(text=text_full))
                await self.push_frame(LLMFullResponseEndFrame())
                log.info(
                    "[agent-bridge] empty goodbye — spoke fallback sign-off",
                    session=self._session.short(),
                    text=text_full,
                )

            if text_full:
                log.info(
                    "[agent-bridge] <<< BOT",
                    session=self._session.short(),
                    tokens=len(bot_chunks),
                    ms=int((time.monotonic() - t0) * 1000),
                    text=text_full,
                )
                # GOODBYE SAFETY NET — if the bot just spoke a genuine sign-off
                # but the model FORGOT to invoke `end_call` (it intermittently
                # does), tear the call down the same graceful way the tool would.
                # schedule_graceful_hangup is idempotent per call, so this is a
                # no-op when the tool already scheduled the hangup this turn.
                if self._session.channel in ("voice", "avatar_video") and _is_terminal_goodbye(text_full):
                    try:
                        from agent_backend.llm_agent.tools.voice.end_call import (
                            schedule_graceful_hangup,
                        )

                        scheduled = schedule_graceful_hangup(
                            conversation_id=self._session.conversation_id,
                            call_id=self._session.call_id,
                            is_avatar=self._session.channel == "avatar_video",
                            reason="goodbye spoken without end_call (safety net)",
                        )
                        if scheduled:
                            log.info(
                                "[agent-bridge] goodbye detected without end_call "
                                "— firing graceful hangup safety net",
                                session=self._session.short(),
                            )
                    except Exception as e:  # noqa: BLE001
                        log.warning(
                            "[agent-bridge] goodbye safety net failed",
                            session=self._session.short(), err=str(e)[:160],
                        )
                # NOTE: per-turn conversation-state extraction has been REMOVED.
                # Conversation analysis (facts, summary, status, scoring) now
                # happens ONCE, post-call, in the BusinessLayer analyzer — which
                # also owns persistence. The agent no longer does per-turn LLM
                # analysis or writes anything back to leads.json.

    async def _speak_opener(self) -> None:
        """Bot-speaks-first — fired on pipeline start.

        Opener composition lives in `llm_agent.openers.render_opener`:
          - Multi-variant templates picked deterministically per (lead, IST
            calendar-day) so consecutive calls to different leads sound
            different but a same-day re-dial is reproducible.
          - Slots: time-of-day greeting × (status, language) middle ×
            permission-ask. Bot name + university name auto-filled.
          - Reserves a Layer-2 hook (pre-warmed LLM opener) — the dial
            endpoint can stash a richer LLM-generated opener during the
            ringing window; if present, `render_opener` returns it instead
            of the template. Not used today; the seam is in place.

        Still <5 ms render; total pickup-to-first-word ~200-500 ms (TTS
        first-byte dominated). The candidate now hears a different shape of
        opener depending on who they are and when we call — closer to how a
        real counsellor sounds.
        """
        t0 = time.monotonic()

        # Late import — keeps the opener module out of agent_bridge's import
        # surface during cold start (it pulls in LeadRepo + university JSON).
        from agent_backend.llm_agent.openers import render_opener
        opener = render_opener(self._session)

        log.info(
            "[agent-bridge] <<< OPENER",
            session=self._session.short(),
            text=opener,
        )

        # Persist to conversation memory so the next turn's brain prompt
        # contains this as an AIMessage — prevents the brain from re-greeting.
        with contextlib.suppress(Exception):
            get_conversation(self._session.conversation_id).append_bot(opener)

        # Push downstream to the TTS service. Wrapping in LLMFullResponse*Frames
        # is what Pipecat's TTS services use to know an utterance has begun
        # and ended (matters for synthesis batching + BotStoppedSpeakingFrame).
        await self.push_frame(LLMFullResponseStartFrame())
        await self.push_frame(TextFrame(text=opener))
        await self.push_frame(LLMFullResponseEndFrame())

        log.info(
            "[agent-bridge] opener queued",
            session=self._session.short(),
            ms=int((time.monotonic() - t0) * 1000),
        )

    async def _cancel_current_stream(self) -> None:
        """Cancel + await the in-flight stream task (if any) so the next turn
        starts from a clean slate."""
        task = self._stream_task
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        self._stream_task = None

    # ------------------------------------------------------------------
    # Silence responder — subscribe to T2/T3/T4 SilenceTickEvents and
    # inject a synthetic system-side prompt so the brain re-engages the
    # candidate the way a human counsellor would.
    # ------------------------------------------------------------------
    async def _listen_for_silence(self) -> None:
        """Subscribe to SilenceTickEvent on the bus until the call ends."""
        assert self._bus is not None
        try:
            async for ev in self._bus.subscribe(types=(SilenceTickEvent,)):
                if not isinstance(ev, SilenceTickEvent):
                    continue
                # Skip T1 — it's a no-op marker in SilenceMonitor by design.
                if ev.threshold == "T1":
                    continue
                # Don't re-engage once the call is ENDING. After end_call (or the
                # goodbye safety net) schedules the hangup, the call is in its
                # closing window — the silence we're seeing is the gap while the
                # goodbye plays (or the model skipped speaking one). Injecting a
                # 'still there?'/'no rush' nudge here talks over the teardown and
                # contradicts the goodbye the user already accepted.
                if hangup_pending(self._session.conversation_id):
                    log.debug(
                        "[silence-responder] %s skipped (hangup pending)",
                        ev.threshold,
                    )
                    continue
                # Don't talk over an in-flight bot turn.
                if self._stream_task is not None and not self._stream_task.done():
                    log.debug(
                        "[silence-responder] %s skipped (bot already speaking)",
                        ev.threshold,
                    )
                    continue
                prompt = _SILENCE_PROMPTS.get(ev.threshold)
                if not prompt:
                    continue
                log.info(
                    "[silence-responder] firing %s re-engagement",
                    ev.threshold,
                    elapsed_s=round(ev.elapsed_s, 1),
                )
                self._stream_task = asyncio.create_task(
                    self._consume_stream(prompt),
                    name=f"silence-{ev.threshold}-{self._session.short()}",
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("[silence-responder] listener crashed: %s", e)

