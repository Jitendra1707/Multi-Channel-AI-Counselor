"""Meeting event bus (self-contained copy) — per-session pub/sub decoupling the turn detector,
barge-in manager, silence monitor, and metrics sink.

ISOLATION NOTE
--------------
This is a SELF-CONTAINED copy of the voice channel's event bus. It deliberately
imports nothing from `channels.voice` or `channels.pipecat` so that editing the
avatar's human-simulation logic can NEVER break the voice channel (and vice
versa). The two stacks evolve independently — that's the whole point.

Scope
-----
ONE BUS PER WEBRTC SESSION, keyed by `pc_id` (the SmallWebRTC peer-connection
id). Created when the avatar pipeline is built (runner.py); closed when the
connection tears down. Subscribers are async generators that shut down cleanly
when the bus closes (a sentinel `_END` is pushed down each queue).

Why per-session, not global?
----------------------------
Two concurrent avatar tabs share NOTHING through the bus. A barge-in on tab A
must never affect tab B. Sessions are short-lived; a global bus would need
per-event routing. Per-session buses are simpler and faster.

Performance
-----------
Each event is a tiny frozen dataclass; queues are bounded (maxsize=128 per
subscriber). If a subscriber is too slow, `publish()` drops the OLDEST event
for that subscriber (with a warning) rather than blocking the realtime audio
pipeline.
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
# Event types — minimal, evolvable, frozen (cheaper than pydantic; no
# validation needed for in-process messages).
# ---------------------------------------------------------------------------
TurnState = Literal[
    "speaking",         # user actively speaking (or interim transcripts flowing)
    "brief_pause",      # short silence — likely mid-thought
    "thinking",         # filler ("uhh") detected OR semantic incompletion
    "turn_complete",    # user finished — brain may take the turn
    "abandoned",        # very long silence after a partial
]


@dataclass(frozen=True)
class TurnEvent:
    state: TurnState
    confidence: float                 # 0.0 — 1.0
    source: str                       # "vad" | "stt-interim" | "stt-final" | "fusion" | "watchdog"
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class BotSpeakingEvent:
    """Fired when the avatar's TTS output state changes.

    Published by the avatar AgentBridge on BotStartedSpeakingFrame /
    BotStoppedSpeakingFrame. SilenceMonitor uses it to GATE silence
    thresholds: silence only counts when BOTH user_speaking=False AND
    bot_speaking=False — so a re-engagement nudge never fires while the
    avatar is still mid-explanation.
    """
    speaking: bool
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class BargeInEvent:
    """Phases of a barge-in interaction the manager exposes for observers."""
    phase: Literal[
        "detected",         # user speech started while avatar speaking
        "confirmed",        # classifier confirmed a real interrupt
        "rejected",         # acoustic gate / ACK classifier overruled
        "completed",        # ready for new turn
    ]
    bot_said_partial: str | None = None
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class SilenceTickEvent:
    """Fired by SilenceMonitor when a configured threshold elapses.

    T2/T3/T4 are escalating re-engagement check-ins; T5 is the final
    goodbye-and-hang-up. (T1 is a reserved no-op marker.)
    """
    threshold: Literal["T1", "T2", "T3", "T4", "T5"]
    elapsed_s: float
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
    | LatencyEvent
)


# ---------------------------------------------------------------------------
# The bus.
# ---------------------------------------------------------------------------
_END = object()  # sentinel pushed into every queue on aclose()


class EventBus:
    """In-process pub/sub for one avatar-video session."""

    def __init__(self, session_id: str, *, queue_max: int = 128) -> None:
        self._session_id = session_id
        self._queue_max = queue_max
        self._subscribers: list[asyncio.Queue] = []
        self._closed = False
        self._lock = asyncio.Lock()

    # ----- publish (sync — safe to call from FrameProcessor.process_frame) ----
    def publish(self, event: Event) -> None:
        """Push an event to every subscriber. Non-blocking.

        If a subscriber's queue is full, drop the OLDEST event for that
        subscriber so a slow consumer can't backpressure the audio pipeline.
        """
        if self._closed:
            return
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                with _suppress(asyncio.QueueFull):
                    q.put_nowait(event)
                log.warning(
                    "[meeting-events] subscriber slow; dropped oldest",
                    session_id=self._session_id,
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
# Per-session registry — runner.py / composer.py use this to give every
# processor the SAME bus instance for a given session without threading it
# through ten constructors.
# ---------------------------------------------------------------------------
_BUSES: dict[str, EventBus] = {}
_BUSES_LOCK = asyncio.Lock()


async def get_or_create_bus(session_id: str) -> EventBus:
    """Return the bus for `session_id`, creating it if absent."""
    async with _BUSES_LOCK:
        bus = _BUSES.get(session_id)
        if bus is None or bus._closed:  # noqa: SLF001
            bus = EventBus(session_id)
            _BUSES[session_id] = bus
        return bus


async def close_bus(session_id: str) -> None:
    """Tear down the bus when the session ends. Called from runner teardown."""
    async with _BUSES_LOCK:
        bus = _BUSES.pop(session_id, None)
    if bus is not None:
        await bus.aclose()


# ---------------------------------------------------------------------------
# tiny ctx manager — local copy so we don't import contextlib at module level.
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
    "LatencyEvent",
    "Event",
    "EventBus",
    "get_or_create_bus",
    "close_bus",
]
