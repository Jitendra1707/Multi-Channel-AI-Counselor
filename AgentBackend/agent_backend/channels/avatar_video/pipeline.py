"""Avatar video pipeline — one clean path, all platforms.

    SmallWebRTCTransport.input()
        ↓  InputAudioRawFrame  (browser microphone via WebRTC)
    make_stt()
        ↓  TranscriptionFrame
    InputAudioSink   (drop raw mic audio once STT has it)
        ↓
    [human-simulation chain — flag-driven, see composer.py]
      LatencyStamper → TurnDetector → BargeInManager
        ↓
    AgentBridge(session)  →  llm_agent.run_stream(channel="avatar_video")
        ↓  TextFrame  (THE ONE BRAIN — leads / RAG / playbook)
      SentenceStreamer → LatencyStamper(out)
        ↓
    make_tts()
        ↓  TTSAudioRawFrame
    SimliVideoService
        → sends TTS audio to Simli; Simli returns lip-synced avatar
        ↓  OutputImageRawFrame (avatar video) + TTSAudioRawFrame (avatar audio)
    SmallWebRTCTransport.output()
        → streams avatar video + audio back to the browser.

The human-simulation chain (turn detection, barge-in classification, silence
re-engagement, latency metrics) is assembled by `composer.py` and gated by the
AVATAR_ENABLE_* flags (all default ON). With every flag OFF the graph is
bit-identical to the previous baseline:

    transport.input → STT → InputAudioSink → AgentBridge → TTS → Simli → output

`SmallWebRTCTransport` and `SimliVideoService` both use aiortc (pure Python),
so this runs identically on Windows, Linux, and macOS.
"""

from __future__ import annotations

from pipecat.pipeline.pipeline import Pipeline

from agent_backend.channels.avatar_video.composer import (
    ComposedPipeline,
    compose_avatar_pipeline,
)
from agent_backend.channels.avatar_video.events import EventBus
from agent_backend.channels.avatar_video.processors import AgentSimliVideoService
from agent_backend.channels.pipecat.services import make_stt, make_tts
from agent_backend.infra import get_logger
from agent_backend.llm_agent.session import Session

log = get_logger(__name__)


def build_avatar_pipeline_v2(
    *,
    transport: object,              # SmallWebRTCTransport
    session: Session,
    simli_service: AgentSimliVideoService,
    bus: EventBus | None = None,
    transcript_sink: object | None = None,  # Callable[[str,str],None] | None
) -> ComposedPipeline:
    """Compose the full avatar graph + background tasks via the composer.

    Returns the ComposedPipeline so the runner can start the returned
    `background_tasks` (silence monitor, metrics) alongside the Pipeline and
    tear them down on session end.

    audio_in_passthrough=True on the transport so mic audio reaches make_stt();
    InputAudioSink then drops the raw audio so it doesn't flood downstream
    processors / the output transport (the avatar's audio comes from Simli).
    """
    log.info("[avatar-video] building Simli/WebRTC pipeline (composer)", session=session.short())
    composed = compose_avatar_pipeline(
        transport_input=transport.input(),    # type: ignore[attr-defined]
        transport_output=transport.output(),  # type: ignore[attr-defined]
        stt=make_stt(),
        # strip_markdown=True: safety net behind the "no markdown" prompt rule —
        # strips **, *, #, lists, links, code so the avatar never speaks symbols.
        tts=make_tts(strip_markdown=True),
        simli_service=simli_service,
        session=session,
        bus=bus,
        transcript_sink=transcript_sink,  # type: ignore[arg-type]
    )
    log.info(
        "[avatar-video] pipeline composed",
        session=session.short(),
        modules=composed.composition_summary or ["(default chain)"],
    )
    return composed


def build_avatar_pipeline(
    *,
    transport: object,              # SmallWebRTCTransport
    session: Session,
    simli_service: AgentSimliVideoService,
) -> Pipeline:
    """Backwards-compatible wrapper — returns only the Pipeline (no bus, no
    background tasks). Kept so any caller that doesn't need the human-sim
    background tasks keeps working. The runner uses `build_avatar_pipeline_v2`.
    """
    composed = build_avatar_pipeline_v2(
        transport=transport,
        session=session,
        simli_service=simli_service,
        bus=None,
    )
    return Pipeline(composed.processors)
