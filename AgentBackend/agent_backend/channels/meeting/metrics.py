"""Meeting metrics — Prometheus + structured-log sink (fully self-contained).

ISOLATION NOTE
--------------
Self-contained copy for the meeting channel (ported from avatar_video).
Imports only its sibling `events` module. Distinct Prometheus metric names
(`aegis_meeting_*`) so it can run alongside the voice + avatar channels' sinks
in the same process without collisions.

Subscribes to the per-meeting event bus and records:
  - Per-stage latency histograms (stt_first_interim, brain_first_token, ...)
  - Turn-state counters
  - Barge-in counters (detected / confirmed / rejected)
  - Silence threshold counters (T2 / T3 / T4 / T5)

`prometheus_client` is OPTIONAL — if absent we still log via structlog.
Started by the runner only when MEETING_ENABLE_METRICS is on. Failures are
logged + swallowed; metrics never break the realtime audio path.
"""
from __future__ import annotations

import asyncio
from typing import Any

from agent_backend.channels.meeting.events import (
    BargeInEvent,
    EventBus,
    LatencyEvent,
    SilenceTickEvent,
    TurnEvent,
)
from agent_backend.infra import get_logger

log = get_logger(__name__)


try:
    from prometheus_client import Counter, Histogram
    _PROM_AVAILABLE = True
except Exception:  # noqa: BLE001
    _PROM_AVAILABLE = False
    Counter = Histogram = None  # type: ignore[assignment]


_METRICS: dict[str, Any] = {}


def _ensure_metrics() -> None:
    if not _PROM_AVAILABLE or _METRICS:
        return
    _METRICS["latency"] = Histogram(
        "aegis_meeting_latency_ms",
        "Per-stage meeting latency in milliseconds",
        ["stage"],
        buckets=(10, 25, 50, 100, 200, 400, 800, 1500, 3000, 5000),
    )
    _METRICS["turn"] = Counter(
        "aegis_meeting_turn_events_total",
        "Meeting turn-detector state transitions",
        ["state"],
    )
    _METRICS["barge"] = Counter(
        "aegis_meeting_barge_events_total",
        "Meeting barge-in manager events",
        ["phase"],
    )
    _METRICS["silence"] = Counter(
        "aegis_meeting_silence_threshold_total",
        "Meeting silence thresholds crossed",
        ["threshold"],
    )


async def run_metrics_sink(bus: EventBus, conversation_id: str) -> None:
    """Subscribe to the bus and record events until the bus closes."""
    _ensure_metrics()
    try:
        async for ev in bus.subscribe():
            try:
                _record(ev, conversation_id)
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "[meeting-metrics] record failed",
                    err=str(e),
                    event_type=type(ev).__name__,
                )
    except asyncio.CancelledError:
        raise


def _record(ev: Any, conversation_id: str) -> None:
    if isinstance(ev, LatencyEvent):
        if _PROM_AVAILABLE:
            _METRICS["latency"].labels(stage=ev.stage).observe(ev.ms)
        # info (not debug) so latency profiling stamps are visible in normal
        # logs. Observe-only; only emitted when MEETING_ENABLE_METRICS is on
        # (the sink isn't started otherwise).
        log.info("[meeting-metrics] latency", stage=ev.stage, ms=round(ev.ms, 1), conv=conversation_id[:12])
        return

    if isinstance(ev, TurnEvent):
        if _PROM_AVAILABLE:
            _METRICS["turn"].labels(state=ev.state).inc()
        log.debug(
            "[meeting-metrics] turn", state=ev.state, conf=ev.confidence,
            source=ev.source, conv=conversation_id[:12],
        )
        return

    if isinstance(ev, BargeInEvent):
        if _PROM_AVAILABLE:
            _METRICS["barge"].labels(phase=ev.phase).inc()
        log.info("[meeting-metrics] barge", phase=ev.phase, conv=conversation_id[:12])
        return

    if isinstance(ev, SilenceTickEvent):
        if _PROM_AVAILABLE:
            _METRICS["silence"].labels(threshold=ev.threshold).inc()
        log.info("[meeting-metrics] silence", threshold=ev.threshold, elapsed=ev.elapsed_s, conv=conversation_id[:12])
        return


__all__ = ["run_metrics_sink"]
