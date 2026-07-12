"""ACS media-streaming WebSocket — runs a Pipecat pipeline for each call.

  ACS  ──(WS, JSON)──►  ACSFrameSerializer  ──►  Pipecat Pipeline
                                                    │
                                                    │  transport.input
                                                    │     → Silero VAD
                                                    │     → STT (Azure / Deepgram)
                                                    │     → AgentBridge
                                                    │        → run_stream()
                                                    │     → TTS (Azure / ElevenLabs)
                                                    │     → transport.output
                                                    │
  ACS  ◄──(WS, JSON)──  ACSFrameSerializer  ◄──────┘

What this replaces: the previous custom asyncio loop in this file that did
Deepgram / ElevenLabs directly. The Pipecat pipeline gives us, for free:

  * **Continuous parallel STT** — audio is fed to STT as it arrives, in
    parallel with the user speaking; the STT service finalises turns when
    Silero VAD says end-of-speech.
  * **Real barge-in / interruption** — `UserStartedSpeakingFrame` cancels
    the brain task AND triggers `InterruptionFrame`, which the serializer
    turns into ACS `StopAudio` so the bot's already-queued audio is dropped
    at ACS too. Sub-frame-level interrupt.
  * **Streaming TTS** — Azure TTS chunks flow into the transport output
    queue as they're generated. No buffering / no first-words-clipping.
  * **Frame-level metrics + heartbeats** — Pipecat's built-in telemetry.

The handler itself is now tiny: build the session, build the transport,
build the pipeline, run it. Lifecycle is owned by the PipelineRunner; when
ACS closes the WS the pipeline drains and the handler returns.
"""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, WebSocket
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask

from agent_backend.channels.voice.events import close_bus, get_or_create_bus
from agent_backend.channels.voice.pipeline import (
    build_voice_pipeline_v2,
    build_voice_transport,
)
from agent_backend.channels.voice.routes import (
    register_live_pipeline,
    resolve_lead_for_call,
    take_active_call,
    unregister_live_pipeline,
)
from agent_backend.config import get_settings
from agent_backend.data import LeadRepo
from agent_backend.infra import get_logger
from agent_backend.llm_agent.session import Session

log = get_logger(__name__)

media_router = APIRouter(tags=["voice-media"])


@media_router.websocket("/acs/media")
async def acs_media(ws: WebSocket) -> None:
    """ACS opens this WebSocket per call. We accept it, build a Pipecat
    pipeline around it, and let Pipecat drive everything until ACS closes."""
    await ws.accept()

    # ----- Identity resolution ---------------------------------------
    # Resolve the Lead from the dial-time payload (outbound) or the in-memory
    # repo (inbound) — NOT from leads.json. BusinessLayer memory is layered on by
    # lead_id in setup_session below.
    lead_id = ws.query_params.get("lead_id") or "unknown"
    lead = resolve_lead_for_call(lead_id)
    call_id = take_active_call(lead_id)
    session = Session(
        channel="voice",
        conversation_id=f"call-{uuid.uuid4().hex[:12]}",
        lead_id=lead_id if lead else None,
        lead_status=lead.status.value if lead else None,
        language=lead.language_preference if lead else "en",
        display_name=lead.full_name if lead else None,
        call_id=call_id,
    )
    log.info(
        "[acs-media] connected lead_id=%s call_id=%s session=%s",
        lead_id, call_id, session.short(),
    )

    # ----- Warm up OFF the hot path (don't delay the opener) -------------
    # Backgrounded so neither the BusinessLayer round-trips nor the LangGraph
    # build stalls the greeting. (a) setup_session hydrates memory (mutates the
    # cached Lead in place → visible to the first turn's prompt) + opens the
    # session; (b) prewarm builds the ReAct graph so the candidate's first reply
    # isn't stalled ~4 s on a lazy build. Both land before the first user turn;
    # tracked in bg_tasks so a dropped call cleans them up.
    from agent_backend.integrations import business as _biz
    from agent_backend.llm_agent.agent import prewarm as _prewarm_graph

    warmup_tasks: list[asyncio.Task] = [
        asyncio.create_task(
            _biz.setup_session(
                session, lead=lead, direction="outbound", provider_call_id=call_id
            ),
            name=f"biz-{session.short()}",
        ),
        asyncio.create_task(
            asyncio.to_thread(_prewarm_graph, session),
            name=f"warm-graph-{session.short()}",
        ),
    ]

    # ----- Build the pipeline ----------------------------------------
    s = get_settings()
    transport = build_voice_transport(ws)

    # Event bus is needed when any production flag is on (turn detector,
    # barge-in mgr, silence mgr, metrics). With all flags off, the composer
    # ignores the bus and the chain reduces to the classic processor list.
    bus = await get_or_create_bus(session.conversation_id)
    composed = build_voice_pipeline_v2(transport=transport, session=session, bus=bus)
    pipeline = Pipeline(composed.processors)

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            audio_in_sample_rate=s.pipecat_audio_sample_rate,
            audio_out_sample_rate=s.pipecat_audio_sample_rate,
            enable_metrics=True,
            enable_usage_metrics=True,
            enable_heartbeats=True,
        ),
    )

    # Start any background tasks the composer asked for (silence monitor,
    # metrics sink). They're cancelled in the finally block alongside the
    # bus so a dropped call doesn't leak coroutines.
    bg_tasks: list[asyncio.Task] = []
    for factory in composed.background_tasks:
        bg_tasks.append(asyncio.create_task(
            factory(), name=f"bg-{session.short()}-{len(bg_tasks)}",
        ))
    # Track the warmups too so a dropped call cancels/awaits them on teardown.
    bg_tasks.extend(warmup_tasks)

    runner = PipelineRunner(handle_sigint=False)
    # Register so the ACS event webhook can cancel this task on CallDisconnected
    # (the hangup signal that the half-open media WS may not surface to us).
    register_live_pipeline(call_id, task)
    log.info(
        "[acs-media] pipeline running",
        session=session.short(),
        modules=composed.composition_summary or ["(default chain)"],
        bg_tasks=len(bg_tasks),
    )
    try:
        # Blocks until ACS closes the WS (or the pipeline cancels). Pipecat's
        # transport handles the receive loop, parses each ACS JSON envelope
        # via our serializer, and shuts down cleanly on EOF.
        await runner.run(task)
    except Exception as e:  # noqa: BLE001
        log.warning("[acs-media] pipeline crashed session=%s err=%s", session.short(), e)
    finally:
        # Drop the teardown-registry entry first so a late CallDisconnected
        # event can't try to cancel an already-finished task.
        unregister_live_pipeline(call_id)
        # Cancel background tasks before closing the bus so subscribers exit
        # cleanly through CancelledError, not on a closed-queue path.
        for bgt in bg_tasks:
            if not bgt.done():
                bgt.cancel()
        for bgt in bg_tasks:
            try:
                await bgt
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await close_bus(session.conversation_id)
        # BusinessLayer: flush the transcript + mark the session ended so the
        # post-call analyzer runs. Best-effort — never blocks teardown.
        try:
            await _biz.close_session(session, end_reason="call_ended")
        except Exception as e:  # noqa: BLE001
            log.debug("[acs-media] business close failed", err=str(e))
        log.info("[acs-media] closed lead_id=%s session=%s", lead_id, session.short())
