"""Voice pipeline — the canonical Pipecat graph for an ACS phone call.

   transport.input()  ──►  STT  ──►  AgentBridge  ──►  TTS  ──►  transport.output()

  - `transport.input()` and `transport.output()` are the FastAPI WebSocket
    transport's two halves. The ACSFrameSerializer (see `serializer.py`)
    translates ACS's lowercase JSON envelopes into Pipecat audio frames on
    the way in, and serialises Pipecat audio + interruption frames into
    ACS's PascalCase envelopes on the way out.
  - The transport's Silero VAD analyser owns turn detection. When the
    candidate starts speaking, Pipecat emits `UserStartedSpeakingFrame`
    upstream of AgentBridge; the bridge cancels the in-flight brain task,
    and Pipecat's `InterruptionFrame` flows out through the serializer as
    an ACS `StopAudio` envelope — making the bot stop talking instantly.
  - STT and TTS are provider-dispatched (`VOICE_STT_PROVIDER` /
    `VOICE_TTS_PROVIDER`); see `agent_backend.channels.pipecat.services`.
  - `AgentBridge` is the only place that touches the brain. It:
      * fires the bot-speaks-first opener on `StartFrame`
      * routes finalised `TranscriptionFrame` → `run_stream()` → `TextFrame`
      * cancels in-flight streaming on barge-in
"""
from __future__ import annotations

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from starlette.websockets import WebSocket

from agent_backend.channels.pipecat.services import make_stt, make_tts
from agent_backend.channels.voice.composer import ComposedPipeline, compose_voice_pipeline
from agent_backend.channels.voice.events import EventBus
from agent_backend.channels.voice.serializer import ACSFrameSerializer
from agent_backend.config import get_settings
from agent_backend.infra import get_logger
from agent_backend.llm_agent.session import Session

log = get_logger(__name__)


def build_voice_transport(ws: WebSocket, serializer=None) -> FastAPIWebsocketTransport:
    """Build the WebSocket transport for a telephony media stream.

    `serializer` selects the carrier's wire protocol. Defaults to
    `ACSFrameSerializer()` so existing ACS callers (media_ws.py) are unchanged;
    the Plivo media route passes a `PlivoFrameSerializer` instead. Everything
    else (VAD, sample rates, the pipeline downstream) is provider-agnostic.

    Silero VAD on the transport input is the single source of truth for
    turn detection — STT providers' own VAD events are disabled in the
    factories so we don't dual-trigger.
    """
    s = get_settings()
    rate = s.pipecat_audio_sample_rate
    params = FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        audio_in_sample_rate=rate,
        audio_out_sample_rate=rate,
        audio_in_channels=1,
        audio_out_channels=1,
        add_wav_header=False,
        serializer=serializer if serializer is not None else ACSFrameSerializer(),
        vad_analyzer=SileroVADAnalyzer(
            params=VADParams(
                # Snappy turn-taking on a real phone call.
                start_secs=0.10,   # react to speech onset fast
                stop_secs=0.60,    # 600 ms of silence ends a turn
                confidence=0.65,   # Silero posterior threshold
                min_volume=0.60,   # RMS floor — suppresses room noise
            )
        ),
        # Reap zombie connections if ACS drops without a clean close.
        session_timeout=300,
    )
    return FastAPIWebsocketTransport(websocket=ws, params=params)


def build_voice_pipeline(
    *,
    transport: FastAPIWebsocketTransport,
    session: Session,
    bus: EventBus | None = None,
) -> Pipeline:
    """Backwards-compatible wrapper — returns only the Pipeline.

    Existing callers (media_ws.py) keep working. The new flag-driven
    enhancements only activate when `build_voice_pipeline_v2` is used and
    the caller starts the returned background tasks.
    """
    composed = build_voice_pipeline_v2(transport=transport, session=session, bus=bus)
    return Pipeline(composed.processors)


def build_voice_pipeline_v2(
    *,
    transport: FastAPIWebsocketTransport,
    session: Session,
    bus: EventBus | None = None,
) -> ComposedPipeline:
    """Compose the full voice graph + background tasks via the composer.

    Callers that want flag-driven enhancements (turn detector, barge-in,
    silence, metrics) use this entry point so they can start the returned
    `background_tasks` alongside the Pipeline. The classic
    `build_voice_pipeline(...)` keeps working for legacy media_ws callers
    that don't need the new tasks.
    """
    stt = make_stt()
    tts = make_tts()
    composed = compose_voice_pipeline(
        transport_input=transport.input(),
        transport_output=transport.output(),
        stt=stt,
        tts=tts,
        session=session,
        bus=bus,
    )
    log.info(
        "[pipeline] built (composer)",
        session=session.short(),
        stt=type(stt).__name__,
        tts=type(tts).__name__,
        modules=composed.composition_summary or ["(default chain)"],
    )
    return composed
