"""Avatar video session manager — Simli avatar over SmallWebRTC.

Connection lifecycle (one per browser tab)
-------------------------------------------
handle_offer(sdp, type, pc_id, lead_id):
  1. Reuse an existing connection if pc_id matches (WebRTC renegotiation),
     otherwise create a new SmallWebRTCConnection and initialize() it with the
     browser's SDP offer.
  2. Build the Pipecat pipeline (SmallWebRTC → STT → AgentBridge → TTS →
     SimliVideoService → SmallWebRTC) and run it as a background task.
  3. Return the SDP answer {sdp, type, pc_id} to the browser.

The browser then sets the answer as its remote description; ICE completes and
media flows. The SimliVideoService connects to Simli on the pipeline StartFrame
and streams the avatar video back through the WebRTC peer.

No per-session TCP port is bound (WebRTC uses aiortc's UDP/ICE), so the previous
port-collision race is structurally impossible.

Teardown:
  - The connection's "closed" event (browser disconnect / tab close) cancels
    the pipeline task.
  - end_session(pc_id) / shutdown() do the same explicitly.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
# simli-ai 2.0.x: SimliConfig no longer accepts apiKey; pass it to SimliClient directly
from simli import SimliConfig

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams

from agent_backend.channels.avatar_video.events import close_bus, get_or_create_bus
from agent_backend.channels.avatar_video.pipeline import build_avatar_pipeline_v2
from agent_backend.channels.avatar_video.processors import (
    AgentSimliVideoService,
    AgentSoulXVideoService,
)
from agent_backend.config import get_settings
from agent_backend.infra import get_logger
from agent_backend.llm_agent.session import Session

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Active connection bookkeeping (keyed by WebRTC pc_id).
# ---------------------------------------------------------------------------

@dataclass
class _ActiveConnection:
    pc_id: str
    lead_id: str | None
    connection: SmallWebRTCConnection
    render_service: AgentSimliVideoService | AgentSoulXVideoService
    pipeline_task: PipelineTask
    run_task: asyncio.Task
    # Human-simulation background tasks (silence monitor, metrics sink) and the
    # per-session event bus that ties the turn detector / barge-in / silence
    # modules together. Empty / None when all AVATAR_ENABLE_* flags are off.
    background_tasks: list[asyncio.Task]
    session_id: str


# ---------------------------------------------------------------------------
# Event-loop-lag monitor (Stage 0.6 — observe-only).
# ---------------------------------------------------------------------------

async def _run_loop_lag_monitor(conversation_id: str) -> None:
    """Measure asyncio scheduling lag on the pipeline's event loop — the SAME loop
    aiortc paces media on. A healthy loop wakes ~on time (lag ≈ 0); a blocked loop
    (LLM token-gen / RAG / JPEG decode) wakes late by ~the block duration.

    Spikes here that line up in time with the browser's rtt_ms / freeze events confirm
    the aiortc sender is being STARVED (cause B) rather than the network failing.
    Gated by AVATAR_ENABLE_METRICS; appended to background_tasks so teardown cancels it.
    """
    loop = asyncio.get_running_loop()
    interval = 0.25
    max_lag = 0.0
    stalls = 0
    last_report = loop.time()
    try:
        while True:
            t0 = loop.time()
            await asyncio.sleep(interval)
            lag_ms = (loop.time() - t0 - interval) * 1000.0
            if lag_ms > max_lag:
                max_lag = lag_ms
            if lag_ms > 200.0:
                stalls += 1
                log.warning(
                    "[avatar-loop-lag] STALL",
                    lag_ms=round(lag_ms),
                    conv=conversation_id[:12],
                )
            now = loop.time()
            if now - last_report >= 2.0:
                log.info(
                    "[avatar-loop-lag]",
                    max_lag_ms=round(max_lag),
                    stalls_gt_200ms=stalls,
                    conv=conversation_id[:12],
                )
                max_lag = 0.0
                last_report = now
    except asyncio.CancelledError:
        return


# ---------------------------------------------------------------------------
# Session manager.
# ---------------------------------------------------------------------------

class AvatarVideoSessionManager:
    """Process-scoped registry of live WebRTC avatar sessions, keyed by pc_id."""

    def __init__(self) -> None:
        self._connections: dict[str, _ActiveConnection] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def handle_offer(
        self,
        *,
        sdp: str,
        type_: str,
        pc_id: str | None = None,
        lead_id: str | None = None,
    ) -> dict[str, str]:
        """Process a browser WebRTC SDP offer and return the SDP answer.

        New pc_id → create connection + pipeline. Existing pc_id → renegotiate.
        """
        s = get_settings()
        if s.avatar_renderer == "soulx":
            if not s.ditto_service_url:
                raise RuntimeError(
                    "DITTO_SERVICE_URL must be set in .env for the SoulX renderer"
                )
        elif not s.simli_api_key or not s.simli_face_id:
            raise RuntimeError(
                "SIMLI_API_KEY and SIMLI_FACE_ID must be set in .env for the Simli renderer"
            )

        # ── Renegotiation of an existing connection ─────────────────────
        if pc_id and pc_id in self._connections:
            conn = self._connections[pc_id].connection
            log.info("[avatar-video] renegotiating", pc_id=pc_id)
            await conn.renegotiate(sdp=sdp, type=type_, restart_pc=False)
            return conn.get_answer()  # type: ignore[return-value]

        # ── New connection ──────────────────────────────────────────────
        ice_servers = [
            IceServer(urls=u.strip())
            for u in s.webrtc_ice_servers.split(",")
            if u.strip()
        ]
        connection = SmallWebRTCConnection(ice_servers=ice_servers)
        await connection.initialize(sdp=sdp, type=type_)

        answer = connection.get_answer()
        new_pc_id: str = answer["pc_id"]

        # Build + launch the pipeline bound to this connection.
        await self._spawn_pipeline(
            connection=connection,
            pc_id=new_pc_id,
            lead_id=lead_id,
        )

        # Tear down when the browser disconnects / tab closes.
        @connection.event_handler("closed")
        async def _on_closed(_c: SmallWebRTCConnection) -> None:
            log.info("[avatar-video] WebRTC connection closed", pc_id=new_pc_id)
            await self._teardown(new_pc_id)

        log.info(
            "[avatar-video] session ready",
            pc_id=new_pc_id,
            lead_id=lead_id,
        )
        return answer  # type: ignore[return-value]

    async def end_session(self, pc_id: str) -> None:
        """Explicitly end a session by pc_id (DELETE endpoint / manual close)."""
        await self._teardown(pc_id)

    async def shutdown(self) -> None:
        """Tear down every live connection — called on app shutdown."""
        for pc_id in list(self._connections.keys()):
            await self._teardown(pc_id)
        log.info("[avatar-video] all sessions cleaned up")

    def active_sessions(self) -> list[dict[str, str]]:
        """Summary of live connections (for /health and debug)."""
        return [
            {
                "pc_id": c.pc_id,
                "lead_id": c.lead_id or "",
                "pipeline_running": str(not c.run_task.done()),
            }
            for c in self._connections.values()
        ]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _spawn_pipeline(
        self,
        *,
        connection: SmallWebRTCConnection,
        pc_id: str,
        lead_id: str | None,
    ) -> None:
        s = get_settings()

        pipecat_session = Session(
            channel="avatar_video",
            # MUST be unique per session. pc_id is "SmallWebRTCConnection#<n>", so
            # pc_id[:12] is always "SmallWebRTCC" — every session would collide on one
            # conversation_id and SHARE the event bus, LLM conversation memory, mute
            # registry, and UI emitter (all keyed by conversation_id), cross-wiring
            # concurrent sessions. A fresh uuid keeps each session fully isolated.
            conversation_id=f"avatar-{uuid.uuid4().hex[:12]}",
            lead_id=lead_id,
            # Stash the FULL WebRTC pc_id in call_id so the end_call tool can
            # tear down THIS avatar session (the silence-timeout hang-up at T4,
            # or a natural-conclusion goodbye). The avatar has no telephony leg;
            # end_call routes pc_id → avatar manager.end_session() instead.
            call_id=pc_id,
        )

        # Render service: SoulX (GPU talking head over WebSocket) or Simli (cloud
        # API), selected by AVATAR_RENDERER. Both are FrameProcessors satisfying the
        # same render-seam contract (consume TTSAudioRawFrame → push OutputImageRawFrame
        # + avatar audio), so the rest of the pipeline is renderer-agnostic.
        soulx = s.avatar_renderer == "soulx"
        if soulx:
            render_service: AgentSimliVideoService | AgentSoulXVideoService = (
                AgentSoulXVideoService(
                    service_url=s.ditto_service_url,
                    image_path=s.ditto_reference_image_path,
                    out_sample_rate=s.avatar_audio_out_sample_rate,
                )
            )
        else:
            # AgentSimliVideoService: 2.0.x-compatible wrapper.
            # SimliConfig in 2.0.x no longer has an apiKey field — the key is passed
            # directly to SimliClient via AgentSimliVideoService(api_key=...).
            simli_config = SimliConfig(
                faceId=s.simli_face_id,
                maxSessionLength=s.simli_max_session_length,
                maxIdleTime=s.simli_max_idle_time,
            )
            render_service = AgentSimliVideoService(
                api_key=s.simli_api_key,
                simli_config=simli_config,
                is_trinity_avatar=s.simli_is_trinity_avatar,
                # Emit avatar audio at the transport's output rate (48 kHz) so there
                # is no resample between Simli (48 kHz native) and the WebRTC wire.
                out_sample_rate=s.avatar_audio_out_sample_rate,
            )

        # SmallWebRTC transport bound to this browser connection.
        transport = SmallWebRTCTransport(
            webrtc_connection=connection,
            params=TransportParams(
                audio_in_enabled=True,
                # MUST be True. The STT service is a downstream FrameProcessor and
                # receives microphone audio ONLY via the input transport's
                # passthrough push (BaseInputTransport pushes InputAudioRawFrame
                # downstream iff audio_in_passthrough is set). With it False, STT
                # gets NO audio → no transcripts → the avatar can't hear the user.
                # (The transient "StartFrame not received yet" warnings during the
                # ~2-5s Simli connect window are cosmetic — those frames are dropped
                # harmlessly until the output transport starts; verified non-fatal.)
                audio_in_passthrough=True,
                audio_out_enabled=True,
                # INPUT stays 16k (Azure STT + Silero tuned for it; SmallWebRTC
                # downsamples the 48k mic for us). OUTPUT is 48k to match WebRTC's
                # native rate AND Simli's 48k avatar audio — eliminates the lossy
                # 48k→16k→48k round-trip that caused choppy/metallic playback.
                audio_in_sample_rate=s.pipecat_audio_sample_rate,        # 16000
                audio_out_sample_rate=s.avatar_audio_out_sample_rate,    # 48000
                # Deepen the output audio buffer (default 4 = 40ms → 100ms). This
                # gives the clock-paced WebRTC audio track slack so a transient
                # producer/event-loop hiccup can't drain the queue and emit
                # silence mid-speech (the "broken-radio" underrun). Pairs with the
                # video RGB-conversion thread offload in simli_service.py.
                audio_out_10ms_chunks=s.avatar_audio_out_10ms_chunks,
                video_out_enabled=True,
                video_out_is_live=True,
                # Drive Pipecat's live video pacer at Simli's REAL framerate, not
                # the default 30. (The aiortc output track is ALSO patched to this
                # fps in on_client_connected below — both pacers must agree or the
                # lips drift ahead of the audio.)
                video_out_framerate=s.avatar_video_fps,
                video_out_width=s.avatar_video_width,
                video_out_height=s.avatar_video_height,
                # Silero VAD tuned for a snappy 1-on-1 avatar call. stop_secs is
                # the dead-air gap after the user stops before the turn ends and
                # the LLM starts — the single biggest "feels slow" lever. We use
                # avatar_vad_stop_secs (0.5) rather than the shared
                # VOICE_VAD_STOP_SECS (0.8, conservative shared default).
                vad_analyzer=SileroVADAnalyzer(
                    params=VADParams(
                        start_secs=s.voice_vad_start_secs,
                        stop_secs=s.avatar_vad_stop_secs,
                        confidence=s.voice_vad_confidence,
                        min_volume=s.voice_vad_min_volume,
                    )
                ),
            ),
        )

        # LIP-SYNC FIX — SOURCE TIMESTAMP PASSTHROUGH (the correct, drift-free way):
        # aiortc's stock RawVideoTrack fabricates a fixed-fps pts and OVERWRITES
        # the frame's real timestamp, so the video RTP clock drifts vs the audio
        # (which rides real sample-count pts). No fps constant ever fixes it —
        # forcing 30 makes video run ahead, forcing 25 makes it run behind. The
        # fix is to forward Simli's REAL pts/time_base (carried on the
        # OutputImageRawFrame by _consume_video). Then both A/V share one source
        # timeline and WebRTC's RTCP lip-sync holds them aligned with ZERO drift.
        # A/V sync strategy is renderer-specific:
        #   simli → source-timestamp PASSTHROUGH (Simli frames carry real pts).
        #   soulx → wall-clock RTP pacing (SoulX JPEG frames have NO source pts;
        #           applying passthrough would hit its fabricated-fps fallback and
        #           re-introduce A/V drift). SoulX also needs signal_client_connected()
        #           to release its held first speak_start once the peer is ready.
        @transport.event_handler("on_client_connected")
        async def _enable_av_passthrough(_t: object, _c: object) -> None:
            try:
                from agent_backend.channels.avatar_video.processors import (
                    enable_timestamp_passthrough,
                    pace_and_resync_video_track,
                )

                track = getattr(transport._output._client, "_video_output_track", None)  # type: ignore[attr-defined]
                if soulx:
                    pace_and_resync_video_track(track, fps=s.avatar_video_fps)
                    render_service.signal_client_connected()  # type: ignore[union-attr]
                else:
                    enable_timestamp_passthrough(track)
            except Exception as e:  # noqa: BLE001
                log.warning("[avatar-video] A/V sync setup failed (using stock)", err=str(e))

        # MUTE: the FE sends {type:"mute", muted:bool} over the data channel when
        # the user toggles the mic button. Flip the server-authoritative mute
        # flag so the AgentBridge drops transcripts while muted (track.enabled=
        # false alone can let comfort-noise through).
        @transport.event_handler("on_app_message")
        async def _on_app_message(_t: object, message: object, _sender: object = None) -> None:
            try:
                if not isinstance(message, dict):
                    return
                mtype = message.get("type")
                if mtype == "mute":
                    from agent_backend.channels.avatar_video.agent_bridge import set_muted

                    set_muted(pipecat_session.conversation_id, bool(message.get("muted")))
                    log.info(
                        "[avatar-video] mute toggled",
                        pc_id=pc_id, muted=bool(message.get("muted")),
                    )
                elif mtype == "chat":
                    # Typed chat = a normal user turn (mic-less setups). The
                    # bridge replies through the same brain → TTS → avatar path
                    # as speech; an armed knowledge capture consumes a typed
                    # statement exactly like a spoken one.
                    from agent_backend.channels.avatar_video.agent_bridge import handle_typed

                    handle_typed(
                        pipecat_session.conversation_id, str(message.get("text") or "")
                    )
                elif mtype in ("arm_knowledge_capture", "disarm_knowledge_capture"):
                    # Knowledge capture is ARM-FIRST: the click arms; the NEXT
                    # director utterance is consumed by agent_bridge as the
                    # knowledge statement. All candidate ACTIONS (approve /
                    # supersede / reject / edit) happen on the Knowledge Review
                    # screen via the REST surface, not this channel.
                    from agent_backend.channels.avatar_video import knowledge as kc

                    cid = pipecat_session.conversation_id
                    if mtype == "arm_knowledge_capture":
                        kc.arm(cid)
                    else:
                        kc.disarm(cid)
            except Exception as e:  # noqa: BLE001
                log.debug("[avatar-video] on_app_message error", err=str(e))

        # Per-session event bus ties the human-simulation modules (turn
        # detector, barge-in manager, silence monitor, metrics) together.
        # Keyed by pc_id so two concurrent tabs share nothing.
        bus = await get_or_create_bus(pipecat_session.conversation_id)

        # Give the render service the bus so it can publish the AUTHORITATIVE
        # bot-speaking signal (BotSpeakingEvent) to the SilenceMonitor. The
        # transport's audio-derived BotStoppedSpeaking never fires here (both
        # renderers stream continuous audio), so this is what ARMS the silence
        # follow-up / auto-hangup timers. Both AgentSimliVideoService and
        # AgentSoulXVideoService expose `_bus`. (Service is built before the bus
        # exists, so we attach it here.)
        render_service._bus = bus

        # Transcript sink → browser over the WebRTC data channel. The bridge
        # calls this with (role, text); we ship a small JSON envelope the
        # frontend renders in its live transcript panel. Best-effort: if the
        # data channel isn't open yet (early greeting), send_app_message just
        # discards — the panel simply misses that one line, never errors.
        def _transcript_sink(role: str, text: str) -> None:
            try:
                connection.send_app_message(
                    {"type": "transcript", "role": role, "text": text}
                )
            except Exception:  # noqa: BLE001
                pass

        # UI directive emitter → browser over the SAME data channel. The
        # director's present_analytics tool looks this up by conversation_id and
        # calls it to render a chart/report beside the avatar. Best-effort, like
        # the transcript sink. Registered now; cleared on session teardown.
        from agent_backend.channels.avatar_video.ui_emitter import register_ui_emitter

        def _ui_sink(envelope: dict) -> None:
            try:
                connection.send_app_message(envelope)
            except Exception:  # noqa: BLE001
                pass

        register_ui_emitter(pipecat_session.conversation_id, _ui_sink)

        # Knowledge-capture sink → browser over the SAME data channel. The capture
        # flow looks this up by conversation_id to push knowledge_candidate /
        # knowledge_resolved envelopes. Cleared on teardown (like the UI emitter).
        try:
            from agent_backend.channels.avatar_video.knowledge import register_knowledge_sink

            register_knowledge_sink(pipecat_session.conversation_id, _ui_sink)
        except Exception as e:  # noqa: BLE001
            log.debug("[avatar-video] knowledge sink register failed", err=str(e))

        composed = build_avatar_pipeline_v2(
            transport=transport,
            session=pipecat_session,
            simli_service=render_service,
            bus=bus,
            transcript_sink=_transcript_sink,
        )
        pipeline = Pipeline(composed.processors)

        # INTERRUPTION OWNERSHIP:
        # The avatar's own BargeInManager (composed into the pipeline upstream of
        # AgentBridge) now owns ALL barge-in decisions — it runs the 5-intent
        # classifier (ACK / ANSWER / INTERRUPT / CONFUSED / AMBIGUOUS), only
        # emits an InterruptionFrame on a CONFIRMED interrupt, and that frame
        # flows downstream to AgentSimliVideoService → Simli clearBuffer().
        #
        # We therefore DO NOT install Pipecat's MinWordsInterruptionStrategy
        # anymore — it would double-gate (a blunt word-count filter on top of
        # the classifier) and could swallow the manager's own decisions. echo /
        # false-trip rejection is handled inside the classifier + the manager's
        # holding/awaiting-final state machine instead. allow_interruptions stays
        # True so the framework still raises the underlying VAD/interruption
        # frames the manager consumes.
        #
        # If AVATAR_ENABLE_BARGE_IN_MANAGER is turned OFF, the pipeline falls
        # back to the framework's default interruption behaviour (no strategy).
        pipeline_task = PipelineTask(
            pipeline,
            params=PipelineParams(
                allow_interruptions=True,
                audio_in_sample_rate=s.pipecat_audio_sample_rate,        # 16000
                audio_out_sample_rate=s.avatar_audio_out_sample_rate,    # 48000
                # Metrics add per-frame overhead on the realtime audio path —
                # off in production, flip on only when profiling latency.
                enable_metrics=False,
                enable_usage_metrics=False,
                enable_heartbeats=True,
            ),
            idle_timeout_secs=None,
            cancel_on_idle_timeout=False,
        )

        runner = PipelineRunner(handle_sigint=False)
        run_task = asyncio.create_task(
            runner.run(pipeline_task),
            name=f"avatar-pipeline-{pc_id[:8]}",
        )
        run_task.add_done_callback(
            lambda t, pid=pc_id: self._on_pipeline_done(t, pid)
        )

        # Start the human-simulation background tasks (silence monitor, metrics
        # sink). These are NOT part of the Pipecat frame chain — they subscribe
        # to the event bus and run until the bus closes on teardown. Empty when
        # all relevant AVATAR_ENABLE_* flags are off.
        background_tasks: list[asyncio.Task] = []
        for i, make_task in enumerate(composed.background_tasks):
            background_tasks.append(
                asyncio.create_task(
                    make_task(),
                    name=f"avatar-bg-{i}-{pc_id[:8]}",
                )
            )

        # Stage 0.6 (observe-only): event-loop-lag monitor to confirm/refute that the
        # aiortc sender is being starved by a blocked loop. Appended here so _teardown's
        # background-task cancellation cleans it up.
        if s.avatar_enable_metrics:
            background_tasks.append(
                asyncio.create_task(
                    _run_loop_lag_monitor(pipecat_session.conversation_id),
                    name=f"avatar-loop-lag-{pc_id[:8]}",
                )
            )

        self._connections[pc_id] = _ActiveConnection(
            pc_id=pc_id,
            lead_id=lead_id,
            connection=connection,
            render_service=render_service,
            pipeline_task=pipeline_task,
            run_task=run_task,
            background_tasks=background_tasks,
            session_id=pipecat_session.conversation_id,
        )
        log.info(
            "[avatar-video] pipeline spawned",
            pc_id=pc_id,
            bg_tasks=len(background_tasks),
            modules=composed.composition_summary or ["(default chain)"],
        )

    async def _teardown(self, pc_id: str) -> None:
        active = self._connections.pop(pc_id, None)
        if active is None:
            return

        log.info("[avatar-video] tearing down session", pc_id=pc_id)

        # Clear the per-conversation mute flag + bridge registration so a future
        # session reusing the same conversation_id doesn't inherit stale state
        # and a stale bridge closure can't outlive the connection.
        try:
            from agent_backend.channels.avatar_video.agent_bridge import clear_bridge, clear_muted

            clear_muted(active.session_id)  # session_id == conversation_id
            clear_bridge(active.session_id)
        except Exception:  # noqa: BLE001
            pass

        # Drop the UI-directive emitter so a stale closure can't outlive the
        # WebRTC connection.
        try:
            from agent_backend.channels.avatar_video.ui_emitter import clear_ui_emitter

            clear_ui_emitter(active.session_id)
        except Exception:  # noqa: BLE001
            pass

        # Drop the knowledge-capture sink + any pending candidates for this session.
        try:
            from agent_backend.channels.avatar_video.knowledge import clear_knowledge

            clear_knowledge(active.session_id)
        except Exception:  # noqa: BLE001
            pass

        # Cancel the pipeline (CancelFrame → SimliVideoService._stop → Simli stop).
        try:
            await active.pipeline_task.cancel()
        except Exception as e:  # noqa: BLE001
            log.warning("[avatar-video] pipeline cancel error", pc_id=pc_id, err=str(e))

        if not active.run_task.done():
            active.run_task.cancel()
            try:
                await active.run_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        # Close the event bus FIRST — this makes every subscriber generator
        # (silence monitor, metrics sink) return cleanly, so the background
        # tasks below finish on their own rather than needing a hard cancel.
        try:
            await close_bus(active.session_id)
        except Exception as e:  # noqa: BLE001
            log.debug("[avatar-video] bus close error", pc_id=pc_id, err=str(e))

        # Cancel + await any human-simulation background tasks still running.
        for t in active.background_tasks:
            if not t.done():
                t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        # Close the WebRTC peer connection.
        try:
            await active.connection.disconnect()
        except Exception as e:  # noqa: BLE001
            log.debug("[avatar-video] connection disconnect error", pc_id=pc_id, err=str(e))

        log.info("[avatar-video] session torn down", pc_id=pc_id)

    def _on_pipeline_done(self, task: asyncio.Task, pc_id: str) -> None:
        if task.cancelled():
            log.info("[avatar-video] pipeline cancelled", pc_id=pc_id)
        else:
            exc = task.exception()
            if exc:
                log.warning(
                    "[avatar-video] pipeline crashed",
                    pc_id=pc_id,
                    err_type=type(exc).__name__,
                    err=str(exc),
                )
            else:
                log.info("[avatar-video] pipeline exited cleanly", pc_id=pc_id)
        self._connections.pop(pc_id, None)


# ---------------------------------------------------------------------------
# Module-level singleton.
# ---------------------------------------------------------------------------

_manager: AvatarVideoSessionManager | None = None


def get_avatar_manager() -> AvatarVideoSessionManager:
    global _manager
    if _manager is None:
        _manager = AvatarVideoSessionManager()
    return _manager
