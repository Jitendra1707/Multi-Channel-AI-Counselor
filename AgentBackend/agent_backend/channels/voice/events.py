"""Voice-call event bus — decouples turn detector, barge-in, silence,
and conversation-state modules.

Why an event bus and not just method calls?
-------------------------------------------
The turn detector publishes `TurnEvent`s; the barge-in manager subscribes,
the silence monitor subscribes, the conversation-state extractor subscribes.
Each is independent — turning one off (its flag set false) doesn't break
the others. If we wired direct method calls we'd end up with a star graph
where every module knows every other module's interface.

Scope
-----
ONE BUS PER CALL. Keyed by `Session.conversation_id`. Created when the
pipeline is built; closed when the WS handler's finally block fires.
Subscribers are async generators — they shut down cleanly when the bus
closes (sends a sentinel `_END` value down each queue).

Why per-call, not global?
-------------------------
- Two concurrent calls share NOTHING through the bus. A barge-in on call A
  must never affect call B.
- Calls are short-lived (minutes); a global bus would need per-event
  routing keys + filtering. Per-call buses are simpler and faster.

Performance
-----------
Each event is a tiny dataclass; queues are bounded (maxsize=128 per
subscriber) so a slow consumer can't OOM the producer side. If a subscriber
is too slow, `publish()` drops the oldest event with a warning — better
than blocking the audio pipeline.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from agent_backend.infra import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Event types — minimal, evolvable. Keep them frozen dataclasses (cheaper
# than pydantic, no validation needed for in-process messages).
# ---------------------------------------------------------------------------
TurnState = Literal[
    "speaking",         # user is actively speaking (or interim transcripts flowing)
    "brief_pause",      # 200-700 ms silence — likely mid-thought
    "thinking",         # filler ("uhh") detected OR semantic incompletion
    "turn_complete",    # user finished — brain may take the turn
    "abandoned",        # >turn_abandoned_s of silence after a partial
]


@dataclass(frozen=True)
class TurnEvent:
    state: TurnState
    confidence: float                 # 0.0 — 1.0
    source: str                       # which signal fired the change ("vad" | "stt" | "fusion" | ...)
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class BotSpeakingEvent:
    """Fired when the bot's TTS output state changes.

    Two sources observe the underlying Pipecat frames and emit this:
      - AgentBridge          — observes BotStartedSpeakingFrame / BotStoppedSpeakingFrame
                              (default publisher; always installed)
      - BargeInManager       — could publish too, but doesn't to avoid double-pub

    Subscribers (SilenceMonitor in particular) use this to GATE silence
    thresholds. Silence only counts when BOTH user_speaking=False AND
    bot_speaking=False. Without this gate, T2 fires while the bot is mid-
    explanation — which is the bug we're fixing.
    """
    speaking: bool
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class BargeInEvent:
    """Phases of a barge-in interaction the manager exposes for observers."""
    phase: Literal[
        "detected",         # user speech started while bot speaking
        "confirmed",        # held past barge_confirmation_ms — real, not echo
        "rejected",         # echo classifier overruled
        "completed",        # TTS drained, ready for new turn
    ]
    bot_said_partial: str | None = None  # what the bot actually played before cut
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class SilenceTickEvent:
    """Fired by SilenceMonitor when a configured threshold elapses."""
    threshold: Literal["T1", "T2", "T3", "T4"]   # 2s / 5s / 10s / 20s
    elapsed_s: float
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class StateUpdateEvent:
    """Fired by the post-turn extractor when ConversationState changes."""
    facts_delta:        dict[str, Any] = field(default_factory=dict)
    score_delta:        dict[str, int] = field(default_factory=dict)
    stage_transition:   str | None = None
    sentiment:          str | None = None
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class LatencyEvent:
    """Per-stage timing — published by the latency stamper, consumed by metrics."""
    stage: Literal[
        "stt_first_interim",
        "stt_final",
        "brain_first_token",
        "brain_total",
        "tts_first_audio",
        "round_trip",
    ]
    ms: float
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


Event = (
    TurnEvent
    | BotSpeakingEvent
    | BargeInEvent
    | SilenceTickEvent
    | StateUpdateEvent
    | LatencyEvent
)


# ---------------------------------------------------------------------------
# The bus.
# ---------------------------------------------------------------------------
_END = object()  # sentinel pushed into every queue on aclose()


class EventBus:
    """In-process pub/sub for one voice call."""

    def __init__(self, conversation_id: str, *, queue_max: int = 128) -> None:
        self._conversation_id = conversation_id
        self._queue_max = queue_max
        self._subscribers: list[asyncio.Queue] = []
        self._closed = False
        self._lock = asyncio.Lock()

    # ----- publish (sync — safe to call from FrameProcessor.process_frame) ----
    def publish(self, event: Event) -> None:
        """Push an event to every subscriber. Non-blocking.

        If a subscriber's queue is full, drops the OLDEST event for that
        subscriber (with a warning) so a slow consumer can't backpressure
        the audio pipeline.
        """
        if self._closed:
            return
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop oldest to make room — keeps producer non-blocking.
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                with _suppress(asyncio.QueueFull):
                    q.put_nowait(event)
                log.warning(
                    "[events] subscriber slow; dropped oldest",
                    conversation_id=self._conversation_id,
                )

    # ----- subscribe ----------------------------------------------------------
    async def subscribe(
        self,
        types: tuple[type, ...] | None = None,
    ) -> AsyncIterator[Event]:
        """Async iterator yielding events of the given types (or all if None).

        Usage:
            async for ev in bus.subscribe(types=(TurnEvent,)):
                handle(ev)

        Cleans up automatically when the consumer task is cancelled or the
        bus is closed.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_max)
        async with self._lock:
            self._subscribers.append(q)

        try:
            while True:
                item = await q.get()
                if item is _END:
                    return
                if types is None or isinstance(item, types):
                    yield item
        finally:
            async with self._lock:
                if q in self._subscribers:
                    self._subscribers.remove(q)

    # ----- close --------------------------------------------------------------
    async def aclose(self) -> None:
        """Signal every subscriber to stop. Idempotent."""
        if self._closed:
            return
        self._closed = True
        async with self._lock:
            for q in self._subscribers:
                with _suppress(asyncio.QueueFull):
                    q.put_nowait(_END)
            self._subscribers.clear()


# ---------------------------------------------------------------------------
# Per-conversation registry — composer.py uses this to give every processor
# the same bus instance for a given call without threading it through ten
# constructors.
# ---------------------------------------------------------------------------
_BUSES: dict[str, EventBus] = {}
_BUSES_LOCK = asyncio.Lock()


async def get_or_create_bus(conversation_id: str) -> EventBus:
    """Return the bus for `conversation_id`, creating it if absent.

    media_ws.py / composer.py call this when building the pipeline.
    """
    async with _BUSES_LOCK:
        bus = _BUSES.get(conversation_id)
        if bus is None or bus._closed:  # noqa: SLF001
            bus = EventBus(conversation_id)
            _BUSES[conversation_id] = bus
        return bus


async def close_bus(conversation_id: str) -> None:
    """Tear down the bus when the call ends. Called from media_ws.py's finally."""
    async with _BUSES_LOCK:
        bus = _BUSES.pop(conversation_id, None)
    if bus is not None:
        await bus.aclose()


# ---------------------------------------------------------------------------
# tiny ctx manager — local copy so we don't import contextlib at module level
# for one usage.
# ---------------------------------------------------------------------------
class _suppress:
    def __init__(self, *exc): self.exc = exc
    def __enter__(self): return self
    def __exit__(self, t, v, tb): return t is not None and issubclass(t, self.exc)


__all__ = [
    "TurnState",
    "TurnEvent",
    "BotSpeakingEvent",
    "BargeInEvent",
    "SilenceTickEvent",
    "StateUpdateEvent",
    "LatencyEvent",
    "Event",
    "EventBus",
    "get_or_create_bus",
    "close_bus",
]
