"""SilenceMonitor — fires re-engagement thresholds ONLY when both sides quiet.

Why the redesign
----------------
Previous version started its timer on `TurnEvent(turn_complete)` — the user's
turn-complete. Problem: between turn_complete and the bot's reply, the bot
spends 2-10 seconds GENERATING + SPEAKING. During that window the user is
silent (they're listening), but the timer kept ticking. T2 (5s) would fire
while the bot was still mid-explanation -> "still there?" interjections
during the bot's own answer. Bad.

New semantics
-------------
"Silence" = both bot quiet AND user quiet. The clock only counts time
when BOTH sides are quiet. Every signal of activity (user starts speaking,
bot starts speaking, bot stops speaking, user finishes a turn) RESETS the
"last activity" timestamp.

Concretely:
  - bot speaking starts -> reset; pause threshold checks
  - bot speaking stops  -> reset; resume checks from here
  - user starts speaking -> reset; pause
  - user turn complete  -> reset; resume (only if bot not also speaking)
  - barge-in detected/confirmed -> reset; pause

Threshold checks run in a 250ms tick loop independent of event arrival —
that way the elapsed-silence comparison is always against real wall-clock,
not against when the last event happened to arrive.

Behaviour when the flag is off: this background task isn't started by the
composer, so no events are observed and no thresholds fire.
"""
from __future__ import annotations

import asyncio
import time

from agent_backend.channels.voice.events import (
    BargeInEvent,
    BotSpeakingEvent,
    EventBus,
    SilenceTickEvent,
    TurnEvent,
)
from agent_backend.config import get_settings
from agent_backend.infra import get_logger

log = get_logger(__name__)


async def run_silence_monitor(bus: EventBus, conversation_id: str) -> None:
    """Tick-driven silence threshold monitor.

    Cancelled when the call ends (bus closes -> subscribe() returns ->
    `_listen` task ends -> gather returns -> this task ends).
    """
    s = get_settings()
    thresholds: list[tuple[str, float]] = [
        ("T2", s.silence_t2_s),
        ("T3", s.silence_t3_s),
        ("T4", s.silence_t4_s),
    ]

    # Shared state between the two loops (listen + tick).
    state = _MonitorState(last_activity=time.monotonic())

    try:
        await asyncio.gather(
            _listen(bus, state, conversation_id),
            _tick(bus, state, thresholds, conversation_id),
        )
    except asyncio.CancelledError:
        raise


class _MonitorState:
    """Mutable shared state between the listen + tick loops."""
    __slots__ = (
        "last_activity", "bot_speaking", "user_speaking", "fired", "armed",
        "threshold_bonus_s", "engagement_pending",
    )

    def __init__(self, last_activity: float) -> None:
        self.last_activity: float = last_activity
        # When either side is "speaking", silence checks pause entirely.
        self.bot_speaking: bool = False
        self.user_speaking: bool = False
        # Which thresholds have fired since the last activity event.
        self.fired: set[str] = set()
        # `armed` is True once we've seen a "user finished" signal AND the
        # bot has finished speaking. While armed, the tick loop checks
        # thresholds. Reset to False on any speech start.
        self.armed: bool = False
        # Adaptive bonus added to T2/T3/T4 thresholds when armed. Recomputed
        # each time the bot stops speaking based on what the bot just said:
        # long explanations (>30 words) + questions (contain `?`) earn extra
        # thinking time. Stops "still there?" firing 5 s after a 67-word
        # explanation with a yes/no ask. Reset to 0 on user speech start.
        self.threshold_bonus_s: float = 0.0
        # True when the user gave a barge signal (ACK like "ah" or a real
        # interrupt attempt) since their last full turn. Adds extra thinking
        # time bonus when armed — "user signalled engagement, they're still
        # processing, give them more time before checking in". Cleared only
        # when the user takes a real turn (TurnEvent("speaking") or
        # BargeInEvent("confirmed")).
        self.engagement_pending: bool = False


def _compute_adaptive_bonus(conversation_id: str) -> float:
    """Extra seconds to add to T2/T3/T4 thresholds based on the bot's last
    utterance. The intuition: long replies + questions need more thinking
    time before we check "still there?".

    Rules (cumulative):
      - bot last reply > 30 words           → +3.0 s
      - bot last reply > 60 words           → +2.0 s (in addition)
      - bot last reply contains '?'          → +3.0 s

    So a short statement gets no bonus; a 67-word reply ending in a yes/no
    question gets 3 + 2 + 3 = 8 s on top of the baseline (8 s → 16 s before
    the first probe fires).
    """
    try:
        from langchain_core.messages import AIMessage
        from agent_backend.llm_agent.conversation import get_conversation

        history = get_conversation(conversation_id).recent(n=4)
        for msg in reversed(history):
            if isinstance(msg, AIMessage):
                content = msg.content or ""
                text = content if isinstance(content, str) else str(content)
                word_count = len(text.split())
                bonus = 0.0
                if word_count > 30:
                    bonus += 3.0
                if word_count > 60:
                    bonus += 2.0
                if "?" in text:
                    bonus += 3.0
                return bonus
        return 0.0
    except Exception:  # noqa: BLE001
        # If conversation memory isn't available, fall back to baseline. Less
        # surprising than spuriously extending the silence window.
        return 0.0


async def _listen(
    bus: EventBus,
    state: _MonitorState,
    conversation_id: str,
) -> None:
    """Update state from event bus until bus closes."""
    types_we_care_about = (TurnEvent, BotSpeakingEvent, BargeInEvent)
    async for ev in bus.subscribe(types=types_we_care_about):
        now = time.monotonic()

        if isinstance(ev, BotSpeakingEvent):
            if ev.speaking:
                # Bot started talking -> silence clock pauses entirely.
                # CRITICAL: do NOT clear `fired` here. If T2 already fired
                # in this silence episode and the bot is now replying with
                # "still there?", we don't want T2 to fire AGAIN after that
                # reply finishes. Only a real user turn clears `fired`.
                state.bot_speaking = True
                state.armed = False
                state.last_activity = now
                log.debug("[silence] bot started speaking -> pause")
            else:
                # Bot finished -> arm the clock. Compute the adaptive bonus
                # based on what the bot just said. THEN add an engagement
                # bonus if the user signalled they're tracking (e.g. said
                # "ah" during the bot's reply): they're processing, give
                # them more thinking time.
                state.bot_speaking = False
                state.last_activity = now
                # Do NOT clear `fired` — see comment above.
                state.armed = not state.user_speaking
                base_bonus = _compute_adaptive_bonus(conversation_id)
                # +15 s when engagement is pending. The user said "ah" or
                # similar during the bot's reply — they're thinking. Real
                # counsellors wait 25-45 s before nudging in that case.
                # Combined with skipping T2 entirely (see _tick), this gives
                # ~30-36 s before the FIRST check-in (T3).
                engagement_bonus = 15.0 if state.engagement_pending else 0.0
                state.threshold_bonus_s = base_bonus + engagement_bonus
                log.debug(
                    "[silence] bot stopped -> armed=%s base_bonus=%.1fs "
                    "engagement_bonus=%.1fs total=%.1fs",
                    state.armed, base_bonus, engagement_bonus,
                    state.threshold_bonus_s,
                )

        elif isinstance(ev, TurnEvent):
            if ev.state == "speaking":
                # User actually started speaking — this is a REAL turn, not
                # a backchannel. Pause the clock, clear `fired` (fresh
                # episode), and CLEAR engagement_pending (engagement consumed).
                state.user_speaking = True
                state.armed = False
                state.fired.clear()
                state.last_activity = now
                state.threshold_bonus_s = 0.0
                state.engagement_pending = False
                log.debug("[silence] user speaking -> pause + reset")
            elif ev.state in ("turn_complete", "abandoned"):
                # User finished their turn. Clear `fired` (next silence
                # episode is independent of previous ones). Clear engagement
                # since they took a real turn now.
                state.user_speaking = False
                state.last_activity = now
                state.fired.clear()
                state.engagement_pending = False
                state.armed = not state.bot_speaking
                log.debug("[silence] user turn complete -> armed=%s", state.armed)
            elif ev.state in ("brief_pause", "thinking"):
                # Don't arm; user is mid-thought.
                state.last_activity = now

        elif isinstance(ev, BargeInEvent):
            if ev.phase == "confirmed":
                # Real interrupt — user is taking the turn. Clear `fired`
                # and engagement; this is a fresh turn now.
                state.armed = False
                state.fired.clear()
                state.last_activity = now
                state.threshold_bonus_s = 0.0
                state.engagement_pending = False
            elif ev.phase in ("detected", "rejected"):
                # Barge happened but bot keeps speaking (ACK / acoustic-gate
                # reject / etc). User is engaged but not taking the turn.
                # Set engagement_pending so the silence threshold extends
                # when the bot finishes. Do NOT clear `fired` — we don't
                # want T2 to re-fire just because the user "ah"'d.
                state.engagement_pending = True
                state.last_activity = now


async def _tick(
    bus: EventBus,
    state: _MonitorState,
    thresholds: list[tuple[str, float]],
    conversation_id: str,
) -> None:
    """Fire SilenceTickEvents when armed AND elapsed crosses a threshold.

    Ticks at 250ms. Fast enough to feel snappy on a 5s threshold; cheap.
    """
    try:
        while True:
            await asyncio.sleep(0.25)

            # Hard gate — never fire while either side is talking.
            if not state.armed or state.bot_speaking or state.user_speaking:
                continue

            elapsed = time.monotonic() - state.last_activity
            bonus = state.threshold_bonus_s
            for label, secs in thresholds:
                # SKIP T2 entirely when the user has signalled engagement
                # (said "ah" / similar during the bot's reply). They're
                # clearly tracking — the bot shouldn't probe with "any
                # questions?" at all. The next check-in is T3, which is
                # already pushed out by the +15 s engagement bonus.
                if label == "T2" and state.engagement_pending:
                    continue
                effective = secs + bonus
                if elapsed >= effective and label not in state.fired:
                    bus.publish(SilenceTickEvent(threshold=label, elapsed_s=elapsed))
                    state.fired.add(label)
                    log.info(
                        "[silence] %s fired",
                        label,
                        elapsed_s=round(elapsed, 1),
                        threshold_s=round(effective, 1),
                        bonus_s=round(bonus, 1),
                        engagement=state.engagement_pending,
                        conv=conversation_id[:12],
                    )
    except asyncio.CancelledError:
        raise


__all__ = ["run_silence_monitor"]
