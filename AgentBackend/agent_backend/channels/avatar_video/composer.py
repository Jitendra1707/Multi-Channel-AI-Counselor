"""Avatar-video pipeline composer — flag-driven assembly of the Pipecat graph.

ISOLATION NOTE
--------------
Self-contained for avatar_video. Imports only this channel's own processors +
its `events` module. Nothing from `channels.voice`. (`AgentSimliVideoService`,
`InputAudioSink`, `make_stt`, `make_tts` come from the avatar's own processors /
the shared services factory, which is channel-agnostic by design.)

The composer is the SINGLE place that knows the processor order and which flags
gate which insertions. `pipeline.py` calls it.

Order (flags all OFF) — identical to the previous baseline avatar pipeline:

    transport.input → STT → InputAudioSink → AgentBridge → TTS → Simli → transport.output

Order (flags all ON — the default):

    transport.input
      → STT
      → InputAudioSink                  (drop raw mic audio after STT)
      → LatencyStamper(in)              ★ AVATAR_ENABLE_METRICS
      → TurnDetector                    ★ AVATAR_ENABLE_TURN_DETECTOR
      → BargeInManager                  ★ AVATAR_ENABLE_BARGE_IN_MANAGER  ◀ upstream of bridge
      → AgentBridge                     (opener + silence responder + always-clear)
      → SentenceStreamer                ★ AVATAR_ENABLE_STREAMING_OPTIMIZATIONS
      → LatencyStamper(out)             ★ AVATAR_ENABLE_METRICS
      → TTS
      → Simli                           (TTS audio → lip-synced avatar video+audio)
      → transport.output

Position notes
--------------
- InputAudioSink stays immediately after STT (drops InputAudioRawFrame). The
  barge manager therefore can't read input RMS — its acoustic gate degrades
  gracefully (see barge_classifier.acoustic_gate_passed).
- BargeInManager sits UPSTREAM of AgentBridge so it can SUPPRESS the user-
  started + transcript frames for ACK / ANSWER intents (avatar keeps speaking).
  For INTERRUPT / CONFUSED it emits InterruptionFrame which flows downstream
  THROUGH AgentBridge + TTS to AgentSimliVideoService → Simli clearBuffer().

Background tasks (NOT in the Pipecat chain):
  - SilenceMonitor  ★ AVATAR_ENABLE_SILENCE_MANAGER
  - MetricsSink     ★ AVATAR_ENABLE_METRICS

Returns ComposedPipeline (processor list + background tasks). The runner starts
each background task and tears it down on session end; the composer stays
side-effect-free.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pipecat.processors.frame_processor import FrameProcessor

from agent_backend.channels.avatar_video.agent_bridge import AgentBridge
from agent_backend.channels.avatar_video.events import EventBus
from agent_backend.channels.avatar_video.metrics import run_metrics_sink
from agent_backend.channels.avatar_video.processors import (
    AgentSimliVideoService,
    InputAudioSink,
)
from agent_backend.channels.avatar_video.processors.barge_in import BargeInManager
from agent_backend.channels.avatar_video.processors.latency_stamper import LatencyStamper
from agent_backend.channels.avatar_video.processors.sentence_streamer import SentenceStreamer
from agent_backend.channels.avatar_video.processors.silence_monitor import run_silence_monitor
from agent_backend.channels.avatar_video.processors.turn_detector import TurnDetector
from agent_backend.config import Settings, get_settings
from agent_backend.infra import get_logger
from agent_backend.llm_agent.session import Session

log = get_logger(__name__)


# No-arg coroutine factory the runner turns into asyncio.create_task(...).
BackgroundTask = Callable[[], Any]


@dataclass(frozen=True)
class ComposedPipeline:
    """Output of `compose_avatar_pipeline` — used by runner.py."""

    processors: list[FrameProcessor]
    background_tasks: list[BackgroundTask] = field(default_factory=list)
    bus: EventBus | None = None
    composition_summary: list[str] = field(default_factory=list)


def compose_avatar_pipeline(
    *,
    transport_input: FrameProcessor,
    transport_output: FrameProcessor,
    stt: FrameProcessor,
    tts: FrameProcessor,
    simli_service: AgentSimliVideoService,
    session: Session,
    bus: EventBus | None = None,
    settings: Settings | None = None,
    transcript_sink: "Callable[[str, str], None] | None" = None,
) -> ComposedPipeline:
    """Assemble the avatar FrameProcessor chain based on active feature flags."""
    s = settings or get_settings()
    summary: list[str] = []

    needs_bus = (
        s.avatar_enable_turn_detector
        or s.avatar_enable_barge_in_manager
        or s.avatar_enable_silence_manager
        or s.avatar_enable_silence_responder
        or s.avatar_enable_metrics
    )
    if needs_bus and bus is None:
        log.warning(
            "[avatar-composer] flags need event bus but none provided — "
            "modules requiring it will be skipped",
            session=session.short(),
        )

    # ----- inbound chain -----
    # InputAudioSink MUST stay right after STT (drops raw mic audio once STT
    # has consumed it; see audio_sink.py).
    chain: list[FrameProcessor] = [transport_input, stt, InputAudioSink()]

    if s.avatar_enable_metrics and bus is not None:
        chain.append(LatencyStamper(bus=bus))
        summary.append("latency-stamper(in)")

    if s.avatar_enable_turn_detector and bus is not None:
        chain.append(TurnDetector(bus=bus))
        summary.append("turn-detector")

    if s.avatar_enable_barge_in_manager and bus is not None:
        chain.append(BargeInManager(session=session, bus=bus))
        summary.append("barge-in-manager")

    # AgentBridge — always present. Owns the opener + silence responder.
    chain.append(
        AgentBridge(
            session=session,
            bus=bus,
            speak_opener=s.avatar_speak_opener,
            transcript_sink=transcript_sink,
        )
    )

    # ----- outbound chain -----
    if s.avatar_enable_streaming_optimizations:
        chain.append(SentenceStreamer())
        summary.append("sentence-streamer")

    if s.avatar_enable_metrics and bus is not None:
        chain.append(LatencyStamper(bus=bus))
        summary.append("latency-stamper(out)")

    # TTS → Simli → output. strip_markdown is applied by the caller's make_tts.
    chain.extend([tts, simli_service, transport_output])

    # ----- background tasks -----
    bg: list[BackgroundTask] = []
    if s.avatar_enable_silence_manager and bus is not None:
        bg.append(lambda: run_silence_monitor(bus, session.conversation_id))
        summary.append("silence-monitor")

    if s.avatar_enable_metrics and bus is not None:
        bg.append(lambda: run_metrics_sink(bus, session.conversation_id))
        summary.append("metrics-sink")

    log.info(
        "[avatar-composer] pipeline composed",
        session=session.short(),
        flags_on=summary or ["(none — default chain)"],
    )

    return ComposedPipeline(
        processors=chain,
        background_tasks=bg,
        bus=bus,
        composition_summary=summary,
    )


__all__ = ["compose_avatar_pipeline", "ComposedPipeline"]
