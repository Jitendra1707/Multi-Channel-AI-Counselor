"""Voice pipeline composer — flag-driven assembly of the Pipecat graph.

The composer is the SINGLE place that knows the order of processors and
which flags gate which insertions. `pipeline.py` calls it; nothing else
needs to know about feature flags or insertion points.

Order (flags all OFF):

    transport.input → STT → AgentBridge → TTS → transport.output

Order (flags all ON):

    transport.input
      → STT
      → LatencyStamper(in)              ★ ENABLE_METRICS
      → TurnDetector                    ★ ENABLE_TURN_DETECTOR
      → BargeInManager                  ★ ENABLE_BARGE_IN_MANAGER  ◀ upstream of bridge
      → AgentBridge
      → SentenceStreamer                ★ ENABLE_STREAMING_OPTIMIZATIONS
      → LatencyStamper(out)             ★ ENABLE_METRICS
      → TTS
      → transport.output

Position note for BargeInManager
--------------------------------
Sits UPSTREAM of AgentBridge. That lets it SUPPRESS UserStartedSpeakingFrame
and TranscriptionFrame for ACK / ANSWER intents — AgentBridge never sees
them, never cancels its in-flight brain task, bot keeps speaking. For
INTERRUPT / CONFUSED it releases the held frames downstream so AgentBridge
runs the brain on the user's transcript. It pushes InterruptionFrame
downstream too so TTS cancels playback.

Removed in this revision
------------------------
The old `BackchannelFilter` (upstream vocab swallow) and `BackchannelEmitter`
(bot says "mm-hmm" on long user turns) — both replaced by the smarter
BargeClassifier the BargeInManager now uses. No more vocab-list patching.

Background tasks (NOT in the Pipecat chain):
  - SilenceMonitor  ★ ENABLE_SILENCE_MANAGER
  - MetricsSink     ★ ENABLE_METRICS

Returns: ComposedPipeline with processor list + background tasks. The WS
handler starts each task; the composer stays side-effect-free.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pipecat.processors.frame_processor import FrameProcessor

from agent_backend.channels.voice.events import EventBus
from agent_backend.channels.voice.metrics import run_metrics_sink
from agent_backend.channels.voice.processors.agent_bridge import AgentBridge
from agent_backend.channels.voice.processors.barge_in import BargeInManager
from agent_backend.channels.voice.processors.latency_stamper import LatencyStamper
from agent_backend.channels.voice.processors.sentence_streamer import SentenceStreamer
from agent_backend.channels.voice.processors.silence_monitor import run_silence_monitor
from agent_backend.channels.voice.processors.turn_detector import TurnDetector
from agent_backend.config import Settings, get_settings
from agent_backend.infra import get_logger
from agent_backend.llm_agent.session import Session

log = get_logger(__name__)


# `BackgroundTask` is a no-arg coroutine factory the WS handler will turn
# into an `asyncio.create_task(...)`. We don't create the task here so the
# composer stays pure (no side effects) and the WS handler controls the
# task lifecycle (cancel on call end, etc.).
BackgroundTask = Callable[[], Any]


@dataclass(frozen=True)
class ComposedPipeline:
    """Output of `compose_voice_pipeline` — used by media_ws.py."""

    processors: list[FrameProcessor]
    background_tasks: list[BackgroundTask] = field(default_factory=list)
    bus: EventBus | None = None
    composition_summary: list[str] = field(default_factory=list)


def compose_voice_pipeline(
    *,
    transport_input: FrameProcessor,
    transport_output: FrameProcessor,
    stt: FrameProcessor,
    tts: FrameProcessor,
    session: Session,
    bus: EventBus | None = None,
    settings: Settings | None = None,
) -> ComposedPipeline:
    """Assemble the FrameProcessor chain based on the active feature flags."""
    s = settings or get_settings()
    summary: list[str] = []

    needs_bus = (
        s.enable_turn_detector
        or s.enable_barge_in_manager
        or s.enable_silence_manager
        or s.enable_silence_responder
        or s.enable_metrics
    )
    if needs_bus and bus is None:
        log.warning(
            "[composer] flags need event bus but none provided — "
            "modules requiring it will be skipped",
            session=session.short(),
        )

    # ----- inbound chain -----
    chain: list[FrameProcessor] = [transport_input, stt]

    if s.enable_metrics and bus is not None:
        chain.append(LatencyStamper(bus=bus))
        summary.append("latency-stamper")

    if s.enable_turn_detector and bus is not None:
        chain.append(TurnDetector(bus=bus))
        summary.append("turn-detector")

    # BargeInManager UPSTREAM of AgentBridge so it can SUPPRESS the user-
    # started + transcript frames for ACK / ANSWER intents (the bot keeps
    # speaking, AgentBridge never sees a phantom turn). For INTERRUPT /
    # CONFUSED it releases the held frames downstream + emits InterruptionFrame
    # toward TTS.
    if s.enable_barge_in_manager and bus is not None:
        chain.append(BargeInManager(session=session, bus=bus))
        summary.append("barge-in-manager")

    # AgentBridge — always present.
    bridge = AgentBridge(
        session=session,
        # When the new BargeInManager is on, it owns barge-in fully. The
        # legacy `_bot_speaking_since` flat-grace logic in AgentBridge stays
        # inert (delegate_barge_in=True).
        delegate_barge_in=s.enable_barge_in_manager,
        bus=bus,
    )
    chain.append(bridge)

    # ----- outbound chain -----
    if s.enable_streaming_optimizations:
        chain.append(SentenceStreamer())
        summary.append("sentence-streamer")

    if s.enable_metrics and bus is not None:
        chain.append(LatencyStamper(bus=bus))
        summary.append("latency-stamper(out)")

    chain.extend([tts, transport_output])

    # ----- background tasks -----
    bg: list[BackgroundTask] = []
    if s.enable_silence_manager and bus is not None:
        bg.append(lambda: run_silence_monitor(bus, session.conversation_id))
        summary.append("silence-monitor")

    if s.enable_metrics and bus is not None:
        bg.append(lambda: run_metrics_sink(bus, session.conversation_id))
        summary.append("metrics-sink")

    log.info(
        "[composer] voice pipeline composed",
        session=session.short(),
        flags_on=summary or ["(none — default chain)"],
    )

    return ComposedPipeline(
        processors=chain,
        background_tasks=bg,
        bus=bus,
        composition_summary=summary,
    )


__all__ = ["compose_voice_pipeline", "ComposedPipeline"]
