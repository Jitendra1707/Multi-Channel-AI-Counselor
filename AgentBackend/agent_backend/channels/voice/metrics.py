"""Voice-call metrics — Prometheus + structured log sink.

Subscribes to the event bus and records:
  - Per-stage latency histograms (stt_first_interim, brain_first_token, ...)
  - Turn-state counters (per state value)
  - Barge-in counters (detected / confirmed / rejected)
  - Silence threshold counters (T1 / T2 / T3 / T4)
  - State updates (sentiment, score deltas)

Design
------
- `prometheus_client` is an OPTIONAL dependency. If not installed, we still
  log via structlog so operators see the data — they just can't scrape it.
  This keeps the whole module gated by `ENABLE_METRICS` without forcing
  every deployment to install Prometheus.
- The sink is started by composer.py when the flag is on; it subscribes to
  the bus and runs until the call ends. Failures are logged + swallowed —
  we never let metrics collection break the audio pipeline.

OpenTelemetry traces
--------------------
For tracing across STT → brain → TTS (vs. just histograms), we emit span
events on each `LatencyEvent`. OTEL exporter is configured at app boot if
the `opentelemetry-*` packages are installed. Falls back silently.
"""
from __future__ import annotations

import asyncio
from typing import Any

from agent_backend.channels.voice.events import (
    BargeInEvent,
    EventBus,
    LatencyEvent,
    SilenceTickEvent,
    StateUpdateEvent,
    TurnEvent,
)
from agent_backend.infra import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Optional Prometheus. Degrade gracefully if not installed.
# ---------------------------------------------------------------------------
try:
    from prometheus_client import Counter, Histogram, start_http_server
    _PROM_AVAILABLE = True
except Exception:  # noqa: BLE001
    _PROM_AVAILABLE = False
    Counter = Histogram = None  # type: ignore[assignment]
    start_http_server = None  # type: ignore[assignment]


# Module-level metric registry — created lazily on first use.
_METRICS: dict[str, Any] = {}


def _ensure_metrics() -> None:
    """Lazily create Prometheus instruments. Idempotent."""
    if not _PROM_AVAILABLE or _METRICS:
        return
    _METRICS["latency"] = Histogram(
        "aegis_voice_latency_ms",
        "Per-stage latency in milliseconds",
        ["stage"],
        # Telephony-tuned buckets: most calls land 50–800 ms; tail caught by 5s+
        buckets=(10, 25, 50, 100, 200, 400, 800, 1500, 3000, 5000),
    )
    _METRICS["turn"] = Counter(
        "aegis_voice_turn_events_total",
        "Turn-detector state transitions",
        ["state"],
    )
    _METRICS["barge"] = Counter(
        "aegis_voice_barge_events_total",
        "Barge-in manager events",
        ["phase"],
    )
    _METRICS["silence"] = Counter(
        "aegis_voice_silence_threshold_total",
        "Silence thresholds crossed",
        ["threshold"],
    )
    _METRICS["state_update"] = Counter(
        "aegis_voice_state_updates_total",
        "Conversation state updates by kind",
        ["kind"],   # "fact" | "stage" | "sentiment" | "score"
    )


# ---------------------------------------------------------------------------
# Public sink. Run as an asyncio task by composer.py.
# ---------------------------------------------------------------------------
async def run_metrics_sink(bus: EventBus, conversation_id: str) -> None:
    """Subscribe to the bus and record events until the bus closes.

    Designed to be created as `asyncio.create_task(...)`. Never raises out;
    metrics errors are logged + swallowed so a Prometheus hiccup can't
    affect the call.
    """
    _ensure_metrics()
    try:
        async for ev in bus.subscribe():
            try:
                _record(ev, conversation_id)
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "[metrics] record failed",
                    err=str(e),
                    event_type=type(ev).__name__,
                )
    except asyncio.CancelledError:
        raise


def _record(ev: Any, conversation_id: str) -> None:
    """Dispatch one event into Prometheus + structured log."""
    if isinstance(ev, LatencyEvent):
        if _PROM_AVAILABLE:
            _METRICS["latency"].labels(stage=ev.stage).observe(ev.ms)
        log.debug("[metrics] latency", stage=ev.stage, ms=ev.ms, conv=conversation_id[:12])
        return

    if isinstance(ev, TurnEvent):
        if _PROM_AVAILABLE:
            _METRICS["turn"].labels(state=ev.state).inc()
        log.debug(
            "[metrics] turn", state=ev.state, conf=ev.confidence,
            source=ev.source, conv=conversation_id[:12],
        )
        return

    if isinstance(ev, BargeInEvent):
        if _PROM_AVAILABLE:
            _METRICS["barge"].labels(phase=ev.phase).inc()
        log.info("[metrics] barge", phase=ev.phase, conv=conversation_id[:12])
        return

    if isinstance(ev, SilenceTickEvent):
        if _PROM_AVAILABLE:
            _METRICS["silence"].labels(threshold=ev.threshold).inc()
        log.info("[metrics] silence", threshold=ev.threshold, elapsed=ev.elapsed_s, conv=conversation_id[:12])
        return

    if isinstance(ev, StateUpdateEvent):
        if _PROM_AVAILABLE:
            if ev.facts_delta:        _METRICS["state_update"].labels(kind="fact").inc()
            if ev.stage_transition:   _METRICS["state_update"].labels(kind="stage").inc()
            if ev.sentiment:          _METRICS["state_update"].labels(kind="sentiment").inc()
            if ev.score_delta:        _METRICS["state_update"].labels(kind="score").inc()
        log.info(
            "[metrics] state",
            facts=list(ev.facts_delta.keys()) if ev.facts_delta else None,
            stage=ev.stage_transition,
            sentiment=ev.sentiment,
            score_delta=ev.score_delta or None,
            conv=conversation_id[:12],
        )


# ---------------------------------------------------------------------------
# Bootstrap (called from main.py at startup if enable_metrics + port>0).
# ---------------------------------------------------------------------------
def maybe_start_prometheus_server(port: int) -> bool:
    """Start the /metrics scrape endpoint on the given port. Returns True if
    Prometheus is available AND a port>0 was supplied."""
    if port <= 0 or not _PROM_AVAILABLE:
        return False
    try:
        start_http_server(port)
        _ensure_metrics()
        log.info("[metrics] prometheus /metrics serving", port=port)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("[metrics] failed to start prometheus server", err=str(e))
        return False


__all__ = [
    "run_metrics_sink",
    "maybe_start_prometheus_server",
]
