"""SilenceMonitor — fires re-engagement thresholds ONLY when both sides quiet
(meeting channel, fully self-contained).

ISOLATION NOTE
--------------
Self-contained copy for the meeting channel (ported from the proven
avatar_video monitor). Imports only its sibling `events` module and config.
Nothing from `channels.voice` / `channels.avatar_video` / `channels.pipecat`.

Semantics
---------
"Silence" = agent quiet AND every human quiet. The clock only counts time when
BOTH sides are quiet. Every activity signal (a human starts speaking, the agent
starts speaking, the agent stops speaking, a human finishes a turn, a barge is
confirmed) RESETS the "last activity" timestamp.

Threshold checks run in a 250ms tick loop against real wall-clock, independent
of event arrival. T2/T3/T4 fire SilenceTickEvents; the MeetingAgentBridge's
silence responder turns those into in-character re-engagement utterances (solo
meetings only — a panel's humans are never nudged; that gating lives in the
responder, not here). T5 is the graceful goodbye-and-leave, handled by the
runner.

Adaptive bonus: long agent replies earn extra thinking time before the first
probe; an "engagement pending" signal (the candidate said "ah" mid-reply)
earns a small extra grace.
"""
from __future__ import annotations

import asyncio
import time

from agent_backend.channels.meeting.events import (
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
    """Tick-driven silence threshold monitor. Cancelled when the bus closes."""
    s = get_settings()
    thresholds: list[tuple[str, float]] = [
        ("T2", s.meeting_silence_t2_s),
        ("T3", s.meeting_silence_t3_s),
        ("T4", s.meeting_silence_t4_s),
        ("T5", s.meeting_silence_t5_s),
    ]

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
        self.bot_speaking: bool = False
        self.user_speaking: bool = False
        self.fired: set[str] = set()
        self.armed: bool = False
        self.threshold_bonus_s: float = 0.0
        self.engagement_pending: bool = False


def _compute_adaptive_bonus(conversation_id: str) -> float:
    """A small, CAPPED thinking-beat added uniformly to every check-in.

    Since the silence clock starts only AFTER the agent has fully finished
    speaking, the candidate has already had the whole reply to start thinking —
    so the bonus is just a brief grace after a meatier reply, NOT a long delay.
    A single uniform shift (applied to T2 and inherited by T3/T4/T5 so the
    SPACING between check-ins is unchanged), capped so a long answer can't push
    the first nudge out to 10s+.

      - last reply > 40 words → +2.0 s (a beat to digest a longer explanation)
      - capped at +3.0 s total
    """
    BONUS_CAP_S = 3.0
    try:
        from langchain_core.messages import AIMessage
        from agent_backend.llm_agent.conversation import get_conversation

        history = get_conversation(conversation_id).recent(n=4)
        for msg in reversed(history):
            if isinstance(msg, AIMessage):
                content = msg.content or ""
                text = content if isinstance(content, str) else str(content)
                word_count = len(text.split())
                bonus = 2.0 if word_count > 40 else 0.0
                return min(bonus, BONUS_CAP_S)
        return 0.0
    except Exception:  # noqa: BLE001
        return 0.0


async def _listen(bus: EventBus, state: _MonitorState, conversation_id: str) -> None:
    """Update state from event bus until bus closes."""
    types_we_care_about = (TurnEvent, BotSpeakingEvent, BargeInEvent)
    async for ev in bus.subscribe(types=types_we_care_about):
        now = time.monotonic()

        if isinstance(ev, BotSpeakingEvent):
            if ev.speaking:
                state.bot_speaking = True
                state.armed = False
                state.last_activity = now
                log.debug("[meeting-silence] agent started speaking -> pause")
            else:
                state.bot_speaking = False
                state.last_activity = now
                state.armed = not state.user_speaking
                base_bonus = _compute_adaptive_bonus(conversation_id)
                # A brief mumble during the reply ("ah", "mm") earns a small extra
                # grace before the first check-in.
                engagement_bonus = 2.0 if state.engagement_pending else 0.0
                state.threshold_bonus_s = base_bonus + engagement_bonus
                log.debug(
                    "[meeting-silence] agent stopped -> armed=%s base=%.1fs eng=%.1fs total=%.1fs",
                    state.armed, base_bonus, engagement_bonus, state.threshold_bonus_s,
                )

        elif isinstance(ev, TurnEvent):
            if ev.state == "speaking":
                state.user_speaking = True
                state.armed = False
                state.fired.clear()
                state.last_activity = now
                state.threshold_bonus_s = 0.0
                state.engagement_pending = False
                log.debug("[meeting-silence] human speaking -> pause + reset")
            elif ev.state in ("turn_complete", "abandoned"):
                state.user_speaking = False
                state.last_activity = now
                state.fired.clear()
                state.engagement_pending = False
                state.armed = not state.bot_speaking
                log.debug("[meeting-silence] human turn complete -> armed=%s", state.armed)
            elif ev.state in ("brief_pause", "thinking"):
                state.last_activity = now

        elif isinstance(ev, BargeInEvent):
            if ev.phase == "confirmed":
                state.armed = False
                state.fired.clear()
                state.last_activity = now
                state.threshold_bonus_s = 0.0
                state.engagement_pending = False
            elif ev.phase in ("detected", "rejected"):
                state.engagement_pending = True
                state.last_activity = now


async def _tick(
    bus: EventBus,
    state: _MonitorState,
    thresholds: list[tuple[str, float]],
    conversation_id: str,
) -> None:
    """Fire SilenceTickEvents when armed AND elapsed crosses a threshold."""
    _dbg_last = 0.0
    try:
        while True:
            await asyncio.sleep(0.25)

            if not state.armed or state.bot_speaking or state.user_speaking:
                # DIAGNOSTIC (once/sec): if follow-ups never fire, this reveals
                # which gate is blocking — armed False (TurnDetector never sent
                # turn_complete), or agent/human still flagged as speaking.
                _now = time.monotonic()
                if _now - _dbg_last >= 1.0:
                    _dbg_last = _now
                    log.debug(
                        "[meeting-silence] idle (not firing)",
                        armed=state.armed,
                        bot_speaking=state.bot_speaking,
                        user_speaking=state.user_speaking,
                        engagement_pending=state.engagement_pending,
                        elapsed_s=round(time.monotonic() - state.last_activity, 1),
                    )
                continue

            elapsed = time.monotonic() - state.last_activity
            bonus = state.threshold_bonus_s
            for label, secs in thresholds:
                effective = secs + bonus
                if elapsed >= effective and label not in state.fired:
                    bus.publish(SilenceTickEvent(threshold=label, elapsed_s=elapsed))
                    state.fired.add(label)
                    log.info(
                        "[meeting-silence] %s fired",
                        label,
                        elapsed_s=round(elapsed, 1),
                        threshold_s=round(effective, 1),
                        bonus_s=round(bonus, 1),
                        engagement=state.engagement_pending,
                        conv=conversation_id[:12],
                    )
                    # ONE nudge per tick, and RESTART the silence clock from this
                    # nudge. The nudge is the agent taking its turn — the NEXT
                    # check-in must be measured from AFTER it, so the candidate
                    # gets the full inter-threshold gap to respond. Without this
                    # reset, a stale last_activity makes T2→T3→T4→T5 all cross in
                    # the same ~1s window and the agent machine-guns every nudge.
                    # armed stays True so the cadence is robust even if a
                    # bot-stopped signal doesn't land; if the agent IS still
                    # speaking on the next tick, the bot_speaking gate holds fire.
                    state.last_activity = time.monotonic()
                    break
    except asyncio.CancelledError:
        raise


__all__ = ["run_silence_monitor"]
