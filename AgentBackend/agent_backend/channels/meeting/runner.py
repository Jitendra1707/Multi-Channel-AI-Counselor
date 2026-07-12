"""Meeting session manager — the agent joins/leaves LiveKit rooms.

One `MeetingSessionManager` (process singleton) tracks every room the listening
agent is currently sitting in, keyed by room name. Per room it:

  1. Mints the agent's OWN join token (server-side identity, distinct from the
     two humans) and builds a `LiveKitTransport`.
  2. Builds + runs the meeting pipeline (LiveKit in → STT → name-gated,
     speaker-aware bridge → TTS → LiveKit out) as a background task.
  3. Maintains a SID/identity → role map for speaker attribution (M4), refreshed
     as participants join and audio tracks subscribe. The map is indexed by BOTH
     participant.sid and participant.identity because the LiveKit SDK keys
     `remote_participants` by identity while audio frames are tagged by sid —
     indexing both makes the bridge's `user_id` lookup robust either way.
  4. On first human join, optionally speaks a one-line consent notice.
  5. On the LAST human leaving, tears down + flushes the diarised transcript to
     the BusinessLayer for the DUAL (candidate + counsellor) analysis (M5).

Mirrors `channels/avatar_video/runner.py` in shape (PipelineTask + PipelineRunner
+ done-callback bookkeeping); the differences are LiveKit (room, multi-party) vs
SmallWebRTC (single browser offer) and the speaker-attribution map.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.transports.livekit.transport import LiveKitParams, LiveKitTransport

from agent_backend.channels.meeting.avatar_transport import AvatarLiveKitTransport

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams

from agent_backend.channels.meeting.events import (
    BotSpeakingEvent,
    EventBus,
    SilenceTickEvent,
    close_bus,
    get_or_create_bus,
)
from agent_backend.channels.meeting.pipeline import build_meeting_pipeline
from agent_backend.channels.meeting.scheduler import (
    MeetingConfigError,
    mint_token_async,
    sfu_url,
)
from agent_backend.config import get_settings
from agent_backend.infra import get_logger
from agent_backend.llm_agent.session import Session

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Event-loop-lag monitor (observe-only, ported from avatar_video). Measures
# asyncio scheduling lag on the pipeline's event loop — the SAME loop the
# LiveKit media is paced on. Spikes here that line up with choppy agent audio
# confirm the sender is being STARVED (blocked loop) rather than the network
# failing. Gated by MEETING_ENABLE_METRICS.
# ---------------------------------------------------------------------------
async def _run_loop_lag_monitor(conversation_id: str) -> None:
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
                    "[meeting-loop-lag] STALL",
                    lag_ms=round(lag_ms),
                    conv=conversation_id[:12],
                )
            now = loop.time()
            if now - last_report >= 2.0:
                log.info(
                    "[meeting-loop-lag]",
                    max_lag_ms=round(max_lag),
                    stalls_gt_200ms=stalls,
                    conv=conversation_id[:12],
                )
                max_lag = 0.0
                last_report = now
    except asyncio.CancelledError:
        return


@dataclass
class _ActiveMeeting:
    room: str
    transport: LiveKitTransport
    pipeline_task: PipelineTask
    run_task: asyncio.Task
    session: Session
    candidate_lead_id: str | None
    # "solo" (1:1 agent+candidate, greet + answer all) or "panel" (multi-party
    # co-pilot, gated, no opener). Decided at join time.
    mode: str = "solo"
    # The bridge processor, kept so we can fire the solo opener on candidate-join.
    bridge: object | None = None
    # The per-speaker STT router (None in single-shared-STT mode), kept so the
    # on_connected handler can wire LiveKit's active-speaker signal into it.
    stt_router: object | None = None
    # SID/identity → role label ("candidate" | "counsellor"). Indexed by both
    # keys (see module docstring). Populated as participants join.
    speaker_roles: dict[str, str] = field(default_factory=dict)
    # SID/identity → the participant's DISPLAY NAME (the name they joined with,
    # e.g. "Rahul"). Indexed by both keys, same as speaker_roles. Used purely for
    # human-readable logging/diarisation — the gate still keys off the ROLE.
    speaker_names: dict[str, str] = field(default_factory=dict)
    # Diarised transcript: [(role, text)] in arrival order. Drives M5 analysis.
    transcript: list[tuple[str, str]] = field(default_factory=list)
    consent_spoken: bool = False
    opener_spoken: bool = False
    # Pending teardown after the last human left — held for MEETING_EMPTY_GRACE_S
    # so a transient WebRTC reconnect doesn't kill an active meeting. Cancelled if
    # a human (re)joins within the grace window.
    grace_task: asyncio.Task | None = None
    # Watchdog that tears the room down if NO candidate ever arrives (Flow B
    # no-show). Cancelled on first human join.
    wait_task: asyncio.Task | None = None
    # Per-meeting event bus tying the human-simulation modules together (turn
    # detector, barge manager, silence monitor, metrics) — None when unused.
    bus: EventBus | None = None
    # Human-simulation background tasks (silence monitor, metrics sink, loop-lag
    # monitor, T5 silence-close watcher). Cancelled + awaited on teardown.
    background_tasks: list[asyncio.Task] = field(default_factory=list)


class MeetingSessionManager:
    """Process-scoped registry of rooms the agent is sitting in, keyed by room."""

    def __init__(self) -> None:
        self._meetings: dict[str, _ActiveMeeting] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def join_room(
        self,
        *,
        room: str,
        candidate_lead_id: str | None = None,
        mode: str | None = None,
    ) -> None:
        """Dispatch the agent into `room`. Idempotent — a re-join of a room the
        agent is already in is a no-op.

        `mode` ('solo' | 'panel') controls 1:1-vs-co-pilot behaviour; defaults to
        MEETING_MODE when omitted."""
        if room in self._meetings:
            log.info("[meeting] agent already in room", room=room)
            return

        s = get_settings()
        mode = (mode or s.meeting_mode).strip().lower()
        if mode not in ("solo", "panel"):
            mode = "solo"

        # Agent join token + SFU URL — SERVICE mode fetches both from the live-kit/
        # service (single source of truth, Cloud↔OSS decided there); DIRECT mode
        # mints locally. The agent still connects to the SFU over WebRTC itself —
        # only the *coordinates* (url + token) flow through the seam.
        agent_token = await self._mint_agent_token(room)
        url = await sfu_url()
        if not url or not agent_token:
            raise MeetingConfigError(
                "LiveKit is not configured. Set LIVEKIT_SERVICE_URL (service mode) "
                "or LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET (direct mode)."
            )

        # Build the Simli avatar service (when enabled + configured). When present
        # the agent publishes a lip-synced video+audio track instead of audio-only,
        # and the LiveKit transport must enable video output to carry it.
        avatar_service = self._build_avatar_service(room)
        avatar_on = avatar_service is not None

        # When the avatar is on, use the transport that can PUBLISH a video track
        # (stock pipecat LiveKit output only writes audio). Audio-only meetings use
        # the unmodified transport so their behaviour is unchanged.
        transport_cls = AvatarLiveKitTransport if avatar_on else LiveKitTransport
        transport = transport_cls(
            url=url,
            token=agent_token,
            room_name=room,
            params=LiveKitParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                # PIN the input rate + mono, exactly like the working voice channel
                # (build_voice_transport). The LiveKit transport resamples each
                # incoming track to this rate; Azure STT opens its recognizer at
                # the same 16 kHz, so they MUST match. Being explicit (not relying
                # on the StartFrame default) removes any rate/channel ambiguity —
                # a mismatch here is what garbles the transcript.
                audio_in_sample_rate=s.pipecat_audio_sample_rate,  # 16000, == STT
                audio_in_channels=1,                               # mono, == STT stream
                # OUTPUT at 48 kHz to match Simli's native avatar audio + WebRTC,
                # avoiding a lossy resample round-trip. Only matters when the avatar
                # is on; harmless otherwise.
                audio_out_sample_rate=(
                    s.avatar_audio_out_sample_rate if avatar_on else None
                ),
                # Deepen the output audio buffer (pipecat default 4 = 40ms → ~150ms)
                # so the clock-paced LiveKit AudioSource has slack and a transient
                # event-loop hiccup (GIL-heavy per-frame RGB work on Windows) can't
                # drain the queue and emit silence mid-speech — the "broken-radio"
                # TTS stutter. This is the SAME anti-underrun knob the working
                # avatar_video channel sets (avatar_video/runner.py); the meeting
                # channel was missing it, which is why its Simli TTS broke up.
                audio_out_10ms_chunks=s.avatar_audio_out_10ms_chunks,
                # Publish the Simli avatar as the agent's camera track. Mirrors the
                # avatar_video channel's SmallWebRTC video-out config.
                video_out_enabled=avatar_on,
                video_out_is_live=avatar_on,
                video_out_framerate=s.avatar_video_fps,
                video_out_width=s.avatar_video_width,
                video_out_height=s.avatar_video_height,
                # The agent listens to every human track; STT tags each frame
                # with the speaker's participant id (→ attribution in the bridge).
                vad_analyzer=SileroVADAnalyzer(
                    params=VADParams(
                        start_secs=s.voice_vad_start_secs,
                        # Conservative stop_secs: multi-party meetings have natural
                        # cross-talk; the agent must not cut in on a human's pause.
                        stop_secs=s.meeting_vad_stop_secs,
                        # Meeting-tuned (more permissive than the phone handset
                        # defaults) so a laptop/room mic's quieter speech isn't
                        # gated and fed to STT as fragments. See config.
                        confidence=s.meeting_vad_confidence,
                        min_volume=s.meeting_vad_min_volume,
                    )
                ),
            ),
        )

        session = Session(
            channel="meeting",
            conversation_id=f"meeting-{room}",
            lead_id=candidate_lead_id,
        )

        # Per-meeting event bus — ties the human-simulation modules (turn
        # detector, barge-in manager, silence monitor, metrics) together.
        # Keyed by conversation_id so two concurrent rooms share nothing.
        bus = await get_or_create_bus(session.conversation_id)

        # Give the render service the bus so it can publish the AUTHORITATIVE
        # bot-speaking signal (BotSpeakingEvent) to the SilenceMonitor — the
        # transport's audio-derived BotStoppedSpeaking is unreliable under the
        # renderer's continuous audio stream (same fix as avatar_video). Both
        # AgentSimliVideoService and AgentSoulXVideoService expose `_bus`.
        if avatar_service is not None:
            avatar_service._bus = bus  # noqa: SLF001

        active = _ActiveMeeting(
            room=room,
            transport=transport,
            pipeline_task=None,  # type: ignore[arg-type]
            run_task=None,       # type: ignore[arg-type]
            session=session,
            candidate_lead_id=candidate_lead_id,
            mode=mode,
            bus=bus,
        )

        # --- avatar render gate: release SoulX's client-connected wait ----------
        # SoulX (AgentSoulXVideoService) holds its FIRST speak_start behind an
        # internal `_client_connected` Event until signal_client_connected() is
        # called — in the avatar_video channel that's driven by SmallWebRTC's
        # on_client_connected. LiveKit has no such event, so without this the
        # gate never releases: SoulX emits no video → the avatar_transport audio
        # gate never opens → the agent is SILENT. Fire it on LiveKit's
        # `on_connected` (the agent's room connection is up — the analogue of
        # "client ready"). getattr-guarded so it's a no-op for Simli (which has
        # no such gate). This makes SoulX start its idle/speaking video, which in
        # turn opens the audio gate naturally — preserving "video first, then
        # voice" (human-like: the face appears, then it speaks).
        @transport.event_handler("on_connected")
        async def _on_connected(_t) -> None:  # noqa: ANN001
            sig = getattr(avatar_service, "signal_client_connected", None)
            if callable(sig):
                sig()
                log.info("[meeting] avatar client-connected signalled (render gate released)")

            # --- SFU active-speaker STT gate wiring (the production duplicate fix) ---
            # Subscribe to LiveKit's server-side active_speakers_changed on the RAW
            # rtc.Room (the pipecat transport doesn't forward it). The callback runs
            # INLINE on the pipeline loop, so it just builds the allow-set and calls
            # the router's sync setter — no await, no lock. Only participants the SFU
            # reports actively speaking are transcribed → the silent participants'
            # re-captured copies never reach STT, killing the N-duplicate bug.
            router = active.stt_router
            if router is None or not s.meeting_active_speaker_sfu_gate:
                return
            try:
                room = active.transport._client.room  # noqa: SLF001
            except Exception as e:  # noqa: BLE001
                log.warning("[meeting] room not ready for active-speaker gate", err=str(e))
                return
            # Agent exclusion BY SID (frames are SID-keyed) AND identity (what the
            # event actually carries) — the agent's own TTS track can show as active.
            agent_sid = ""
            try:
                agent_sid = getattr(room.local_participant, "sid", "") or ""
            except Exception:  # noqa: BLE001
                pass
            agent_ident = s.livekit_agent_identity

            def _on_active_speakers(speakers) -> None:  # plain def — emitter rejects coroutines
                try:
                    allow: set[str] = set()
                    raw = []
                    for p in speakers:
                        sid = getattr(p, "sid", "") or ""
                        ident = getattr(p, "identity", "") or ""
                        raw.append((sid, ident))
                        if ident == agent_ident or (agent_sid and sid == agent_sid):
                            continue  # never gate-in / transcribe the agent itself
                        if sid:
                            allow.add(sid)
                        if ident:
                            allow.add(ident)
                    router.set_active_speakers(allow)  # type: ignore[attr-defined]
                    log.debug("[meeting-asc]", n=len(speakers), members=raw, allow=sorted(allow))
                except Exception as e:  # noqa: BLE001
                    log.warning("[meeting-asc] callback error", err=str(e))

            try:
                room.on("active_speakers_changed", _on_active_speakers)
                log.info(
                    "[meeting] active-speaker SFU gate registered",
                    agent_sid=agent_sid, agent_ident=agent_ident,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("[meeting] could not register active-speaker handler", err=str(e))

        # --- speaker attribution: refresh the role map on join / track sub ---
        async def _refresh_roles(participant_id: str) -> None:
            await self._refresh_participant_roles(active, participant_id)

        @transport.event_handler("on_participant_connected")
        async def _on_join(_t, participant_id: str) -> None:  # noqa: ANN001
            await _refresh_roles(participant_id)
            # A (re)join cancels any pending grace teardown — the meeting lives.
            self._cancel_grace(active)

        @transport.event_handler("on_audio_track_subscribed")
        async def _on_track(_t, participant_id: str) -> None:  # noqa: ANN001
            await _refresh_roles(participant_id)

        @transport.event_handler("on_first_participant_joined")
        async def _on_first(_t, participant_id: str) -> None:  # noqa: ANN001
            await _refresh_roles(participant_id)
            # Candidate arrived → cancel the no-show watchdog + any grace timer.
            self._cancel_wait(active)
            self._cancel_grace(active)
            await self._maybe_speak_consent(active)
            # Solo (1:1): greet the candidate so the conversation starts without
            # them having to address the agent by name.
            await self._maybe_speak_opener(active)

        @transport.event_handler("on_participant_disconnected")
        async def _on_leave(_t, participant_id: str) -> None:  # noqa: ANN001
            await self._on_participant_left(active, participant_id)

        # --- diarised transcript sink (drives end-of-meeting analysis) ---
        async def _transcript_sink(role: str, text: str) -> None:
            active.transcript.append((role, text))

        # --- durable per-turn sink (crash-safety) — best-effort to BusinessLayer
        async def _turn_sink(role: str, text: str) -> None:
            try:
                from agent_backend.integrations import business as biz

                await biz.append_turn(session, role=role, text=text)
            except Exception as e:  # noqa: BLE001
                log.debug("[meeting] append_turn failed", err=str(e))

        def _speaker_resolver(user_id: str) -> str:
            return active.speaker_roles.get(user_id, "candidate")

        # Resolve a participant id → their DISPLAY NAME for human-readable logs.
        # Empty when the name isn't known yet (bridge then falls back to the role).
        def _name_resolver(user_id: str) -> str:
            return active.speaker_names.get(user_id, "")

        # Live human count for dynamic gating: ≤1 human → answer-all (1:1),
        # 2+ humans → require address (panel). Bound to THIS meeting's `active`.
        def _human_count() -> int:
            return self._human_count(active)

        pipeline, bridge, stt_router, bg_factories = build_meeting_pipeline(
            transport=transport,
            session=session,
            mode=mode,
            speaker_resolver=_speaker_resolver,
            name_resolver=_name_resolver,
            transcript_sink=_transcript_sink,
            turn_sink=_turn_sink,
            avatar_service=avatar_service,
            human_count_fn=_human_count,
            # The agent's own track must never be transcribed (its TTS would be
            # re-recognised as "user speech"). Exclude it by its LiveKit identity.
            agent_user_id=s.livekit_agent_identity,
            bus=bus,
        )
        active.bridge = bridge
        active.stt_router = stt_router
        # Audio sample rates MUST be pinned when the Simli avatar is on: Simli emits
        # 48 kHz native, so the pipeline output clock must be 48 kHz too. Without
        # this the output-audio consumer stalls on a rate mismatch, back-pressures
        # the whole task, starves Simli's video iterator (→ ~0.4 FPS) and Simli
        # then times out the session (ENDFRAME). Mirrors avatar_video's PipelineTask.
        # idle_timeout disabled so a quiet panel meeting isn't cancelled mid-call.
        if avatar_on:
            pipeline_task = PipelineTask(
                pipeline,
                params=PipelineParams(
                    allow_interruptions=True,
                    audio_in_sample_rate=s.pipecat_audio_sample_rate,      # 16000
                    audio_out_sample_rate=s.avatar_audio_out_sample_rate,  # 48000
                    enable_metrics=False,
                    enable_usage_metrics=False,
                    enable_heartbeats=True,
                ),
                idle_timeout_secs=None,
                cancel_on_idle_timeout=False,
            )
        else:
            pipeline_task = PipelineTask(
                pipeline,
                params=PipelineParams(
                    allow_interruptions=True,
                    enable_metrics=False,
                    enable_usage_metrics=False,
                    enable_heartbeats=True,
                ),
            )
        runner = PipelineRunner(handle_sigint=False)
        run_task = asyncio.create_task(
            runner.run(pipeline_task), name=f"meeting-pipeline-{room}"
        )
        run_task.add_done_callback(lambda t, r=room: self._on_pipeline_done(t, r))

        active.pipeline_task = pipeline_task
        active.run_task = run_task

        # Start the human-simulation background tasks (silence monitor, metrics
        # sink). NOT part of the Pipecat frame chain — they subscribe to the bus
        # and end cleanly when close_bus() runs on teardown. Mirrors avatar_video.
        for i, make_task in enumerate(bg_factories):
            active.background_tasks.append(
                asyncio.create_task(make_task(), name=f"meeting-bg-{i}-{room}")
            )
        # T5 silence close: when the final silence threshold fires in an
        # effectively-1:1 meeting, speak a warm goodbye and leave the room —
        # the meeting analogue of the avatar channel's T5 end_call.
        if s.meeting_enable_silence_manager and s.meeting_enable_silence_responder:
            active.background_tasks.append(
                asyncio.create_task(
                    self._watch_silence_close(room, bus),
                    name=f"meeting-silence-close-{room}",
                )
            )
        # Observe-only loop-lag monitor (same diagnostic the avatar channel runs).
        if s.meeting_enable_metrics:
            active.background_tasks.append(
                asyncio.create_task(
                    _run_loop_lag_monitor(session.conversation_id),
                    name=f"meeting-loop-lag-{room}",
                )
            )
        self._meetings[room] = active

        # Open the BusinessLayer session so the post-meeting analysis can attach
        # to the lead (best-effort; no-op when BUSINESS_LAYER_URL is unset).
        await self._open_business_session(session)

        # Flow B no-show watchdog: if no candidate joins within MEETING_AGENT_WAIT_S,
        # tear the room down so the agent isn't left sitting in an empty room.
        active.wait_task = asyncio.create_task(
            self._no_show_watchdog(room, s.meeting_agent_wait_s),
            name=f"meeting-wait-{room}",
        )

        log.info(
            "[meeting] agent joined room",
            room=room, lead_id=candidate_lead_id, mode=mode,
            wait_s=s.meeting_agent_wait_s,
        )

    async def leave_room(self, room: str) -> None:
        """Make the agent leave + finalise analysis. Safe to call repeatedly."""
        await self._teardown(room, reason="manual")

    async def shutdown(self) -> None:
        for room in list(self._meetings.keys()):
            await self._teardown(room, reason="shutdown")
        log.info("[meeting] all meetings cleaned up")

    def active_sessions(self) -> list[dict[str, object]]:
        return [
            {
                "room": m.room,
                "lead_id": m.candidate_lead_id or "",
                "participants": sorted(set(m.speaker_roles.values())),
                "transcript_lines": len(m.transcript),
                "running": (m.run_task is not None and not m.run_task.done()),
            }
            for m in self._meetings.values()
        ]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _build_avatar_service(self, room: str = ""):
        """Build the avatar render service for the agent's published video track.

        Renderer is selected by AVATAR_RENDERER (shared with the avatar_video
        channel), so the meeting avatar matches the 1:1 one by default:
          - "soulx" → AgentSoulXVideoService (open-source GPU talking head over a
            WebSocket — DITTO_SERVICE_URL + optional DITTO_REFERENCE_IMAGE_PATH).
          - "simli" → AgentSimliVideoService (Simli cloud API — SIMLI_API_KEY +
            SIMLI_FACE_ID).
        Both are FrameProcessors that consume the TTS audio and emit RGB24
        OutputImageRawFrame, so the rest of the meeting pipeline + the avatar
        LiveKit transport are renderer-agnostic — no other code changes.

        Returns None (→ audio-only agent, blank/no tile) when MEETING_AVATAR_ENABLED
        is False or the SELECTED renderer's config is missing — same graceful-
        degrade contract as the rest of the channel (never crashes the meeting)."""
        s = get_settings()
        if not s.meeting_avatar_enabled:
            return None

        renderer = (s.avatar_renderer or "simli").strip().lower()
        try:
            if renderer == "soulx":
                if not s.ditto_service_url:
                    log.warning(
                        "[meeting] AVATAR_RENDERER=soulx but DITTO_SERVICE_URL unset "
                        "— agent joins audio-only."
                    )
                    return None
                from agent_backend.channels.meeting.processors import (
                    AgentSoulXVideoService,
                )

                service = AgentSoulXVideoService(
                    service_url=s.ditto_service_url,
                    image_path=s.ditto_reference_image_path,
                    out_sample_rate=s.avatar_audio_out_sample_rate,
                    # Unique GPU session per room so concurrent/stale meetings
                    # never collide on the same SoulX session (the frame-starve).
                    session_id=f"meeting-{room}" if room else "",
                )
                log.info(
                    "[meeting] SoulX avatar enabled",
                    service_url=s.ditto_service_url, session_id=f"meeting-{room}",
                )
                return service

            # default → Simli
            if not s.simli_api_key or not s.simli_face_id:
                log.warning(
                    "[meeting] AVATAR_RENDERER=simli but SIMLI_API_KEY / SIMLI_FACE_ID "
                    "unset — agent joins audio-only (blank tile)."
                )
                return None
            from simli import SimliConfig

            from agent_backend.channels.meeting.processors import (
                AgentSimliVideoService,
            )

            # Match avatar_video's SimliConfig exactly — maxIdleTime keeps the
            # session alive through quiet stretches (a panel meeting can be silent
            # for minutes); without it Simli idle-kills the connection (ENDFRAME).
            simli_config = SimliConfig(
                faceId=s.simli_face_id,
                maxSessionLength=s.simli_max_session_length,
                maxIdleTime=s.simli_max_idle_time,
            )
            service = AgentSimliVideoService(
                api_key=s.simli_api_key,
                simli_config=simli_config,
                is_trinity_avatar=s.simli_is_trinity_avatar,
                out_sample_rate=s.avatar_audio_out_sample_rate,
            )
            log.info("[meeting] Simli avatar enabled", face_id=s.simli_face_id)
            return service
        except Exception as e:  # noqa: BLE001
            log.warning(
                "[meeting] avatar build failed — falling back to audio-only",
                renderer=renderer, err=str(e),
            )
            return None

    async def _mint_agent_token(self, room: str) -> str:
        """The agent's own join token. SERVICE mode → ask the live-kit/ service
        (identity = LIVEKIT_AGENT_IDENTITY, role metadata "agent" so attribution
        never mistakes the agent's published TTS for a human). DIRECT mode →
        mint locally. Returns "" when neither mode is configured (caller raises)."""
        s = get_settings()
        try:
            return await mint_token_async(
                room=room,
                identity=s.livekit_agent_identity,
                # Human-visible name on the participant tile. Matches the meeting
                # persona (prompts/meeting.py) + the wake-word the addressee gate
                # listens for, so the tile, the voice, and the name people call
                # out are all "Aisha". `identity` stays the stable internal key
                # used for speaker attribution — only the label changes here.
                display_name="Aisha",
                role="agent",
                can_publish=True,    # publishes its TTS audio track
                can_subscribe=True,  # listens to the humans
            )
        except MeetingConfigError:
            return ""

    async def _refresh_participant_roles(
        self, active: _ActiveMeeting, participant_id: str
    ) -> None:
        """Read a participant's metadata → role and index it by BOTH sid and
        identity so the bridge's `user_id` lookup resolves regardless of which
        key the STT frame carries. The agent's own identity is skipped."""
        s = get_settings()
        # The pipecat transport's get_participant_metadata(pid) does
        # `room.remote_participants.get(pid)` — but that dict is keyed by IDENTITY
        # while our STT frames + join events use the SID, so a SID lookup returns
        # None → empty metadata → no name, role defaults to "candidate". So we
        # resolve by SCANNING all remote participants and matching EITHER sid OR
        # identity, then index the role/name under BOTH keys so any later lookup
        # (by sid from STT frames, or by identity) resolves.
        meta = self._resolve_participant(active, participant_id)
        log.info(
            "[meeting] participant metadata read",
            pid=participant_id,
            raw_sid=meta.get("sid") if meta else None,
            raw_identity=meta.get("identity") if meta else None,
            raw_name=meta.get("name") if meta else None,
            raw_metadata=meta.get("metadata") if meta else None,
        )
        if not meta:
            return
        role = (meta.get("metadata") or "").strip().lower()
        # Skip the agent itself; only humans get a candidate/counsellor role.
        if role == "agent" or meta.get("identity") == s.livekit_agent_identity:
            return
        if role not in ("candidate", "counsellor"):
            # Unknown / empty metadata → default to candidate so the turn is at
            # least attributed rather than dropped.
            role = role or "candidate"
        # The participant's display name (the name they joined with). Used only
        # for readable logs/diarisation; falls back to nothing when unset.
        name = (meta.get("name") or "").strip()
        # Index under sid, identity, AND the event's participant_id so any later
        # lookup key resolves to the same role/name.
        for key in (meta.get("sid"), meta.get("identity"), participant_id):
            if key:
                active.speaker_roles[str(key)] = role
                if name:
                    active.speaker_names[str(key)] = name
        log.info(
            "[meeting] participant role mapped",
            room=active.room, pid=participant_id, role=role, name=name or "(unnamed)",
            keys=[k for k in (meta.get("sid"), meta.get("identity")) if k],
            known=sorted(set(active.speaker_roles.values())),
        )

    def _resolve_participant(self, active: _ActiveMeeting, participant_id: str) -> dict:
        """Find a remote participant by SID or identity and return its
        {sid, identity, name, metadata}. Works around the transport's
        identity-keyed `.get(sid)` returning None for a SID. Best-effort; {} on
        any failure."""
        try:
            room = active.transport._client.room  # noqa: SLF001
            participants = list(room.remote_participants.values())
        except Exception as e:  # noqa: BLE001
            log.debug("[meeting] room access failed", err=str(e))
            return {}
        pid = str(participant_id)
        for p in participants:
            if str(getattr(p, "sid", "")) == pid or str(getattr(p, "identity", "")) == pid:
                return {
                    "sid": getattr(p, "sid", ""),
                    "identity": getattr(p, "identity", ""),
                    "name": getattr(p, "name", ""),
                    "metadata": getattr(p, "metadata", ""),
                }
        return {}

    async def _maybe_speak_consent(self, active: _ActiveMeeting) -> None:
        """Speak the one-line consent/AI-presence notice once, if configured."""
        s = get_settings()
        line = (s.meeting_consent_line or "").strip()
        if not line or active.consent_spoken:
            return
        active.consent_spoken = True
        try:
            from pipecat.frames.frames import (
                LLMFullResponseEndFrame,
                LLMFullResponseStartFrame,
                TextFrame,
            )

            await active.pipeline_task.queue_frames(
                [
                    LLMFullResponseStartFrame(),
                    TextFrame(text=line),
                    LLMFullResponseEndFrame(),
                ]
            )
            active.transcript.append(("agent", line))
            log.info("[meeting] consent line spoken", room=active.room)
        except Exception as e:  # noqa: BLE001
            log.debug("[meeting] consent speak failed", err=str(e))

    async def _maybe_speak_opener(self, active: _ActiveMeeting) -> None:
        """Greet the student once when they join so the meeting starts naturally.
        The meeting agent is an ACTIVE career counsellor (solo or panel), so it
        opens warmly and leads — the student never has to address it first.

        Suppressed only for a legacy GATED panel (MEETING_REQUIRE_ADDRESS=true),
        where the agent is a quiet co-pilot that must wait to be named — greeting
        there would talk over the human counsellor running the room."""
        s = get_settings()
        gated_panel = active.mode == "panel" and s.meeting_require_address
        if gated_panel or active.opener_spoken or active.bridge is None:
            return
        active.opener_spoken = True
        # MEETING_SOLO_OPENER (legacy name) is the configured opener line for
        # either mode; blank → the persona-aware career-counsellor default.
        line = (s.meeting_solo_opener or "").strip() or self._default_opener()
        try:
            # WARM-UP: don't greet the instant the candidate joins — their
            # browser is still subscribing to the agent's audio/video tracks, so
            # speaking immediately clips the greeting's first words ("...lo, I'm
            # Aisha"). Same fix + default as AVATAR_OPENER_WARMUP_S; also feels
            # natural (a beat before speaking).
            if s.meeting_opener_warmup_s > 0:
                await asyncio.sleep(s.meeting_opener_warmup_s)
            await active.bridge.speak_opener(line)  # type: ignore[attr-defined]
            log.info(
                "[meeting] opener spoken",
                room=active.room, mode=active.mode, warmup_s=s.meeting_opener_warmup_s,
            )
        except Exception as e:  # noqa: BLE001
            log.debug("[meeting] opener speak failed", room=active.room, err=str(e))

    def _default_opener(self) -> str:
        """Persona-aware default greeting when MEETING_SOLO_OPENER is unset."""
        from agent_backend.llm_agent.addressee import extract_name_and_aliases
        from agent_backend.llm_agent.identity import get_identity

        name = ""
        try:
            name, _ = extract_name_and_aliases(get_identity())
        except Exception:  # noqa: BLE001
            pass
        who = f"I'm {name}, your career counsellor" if name else "I'm your career counsellor"
        return f"Hi! {who}. How can I help you today?"

    def _cancel_wait(self, active: _ActiveMeeting) -> None:
        t = active.wait_task
        active.wait_task = None
        if t is not None and not t.done():
            t.cancel()

    def _cancel_grace(self, active: _ActiveMeeting) -> None:
        t = active.grace_task
        active.grace_task = None
        if t is not None and not t.done():
            t.cancel()
            log.info("[meeting] reconnect within grace — teardown cancelled", room=active.room)

    async def _no_show_watchdog(self, room: str, wait_s: int) -> None:
        """Tear the room down if no candidate ever joins (Flow B no-show).
        Cancelled by `_cancel_wait` on the first human join."""
        try:
            await asyncio.sleep(wait_s)
        except asyncio.CancelledError:
            return
        active = self._meetings.get(room)
        if active is not None and self._human_count(active) <= 0:
            log.info("[meeting] no candidate joined within wait window", room=room, wait_s=wait_s)
            await self._teardown(room, reason="no-show")

    async def _watch_silence_close(self, room: str, bus: EventBus) -> None:
        """On the T5 silence threshold (candidate present but silent through all
        the T2–T4 check-ins), speak a warm brain-generated goodbye, wait for it
        to finish playing, then leave the room — the meeting analogue of the
        avatar channel's T5 goodbye + end_call. Only acts when the meeting is
        effectively 1:1; a quiet PANEL is the humans' business, never closed.
        Ends cleanly when the bus closes on teardown."""
        fired = False
        try:
            async for ev in bus.subscribe(types=(SilenceTickEvent,)):
                if not isinstance(ev, SilenceTickEvent) or ev.threshold != "T5":
                    continue
                active = self._meetings.get(room)
                if active is None:
                    return
                if self._human_count(active) > 1:
                    log.debug("[meeting] T5 in panel — not closing", room=room)
                    continue
                fired = True
                break
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("[meeting] silence-close watcher crashed", room=room, err=str(e))
            return
        if not fired:
            return  # bus closed (normal teardown) without a T5

        active = self._meetings.get(room)
        if active is None:
            return
        log.info("[meeting] T5 silence — speaking goodbye, then leaving", room=room)

        # Subscribe for the goodbye's bot-speaking lifecycle BEFORE speaking so
        # the started/stopped events can't be missed, then wait (bounded) for
        # the goodbye to fully play out. GOODBYE-FIRST, hang up only after the
        # speech finishes — same contract as end_call on the other channels.
        async def _goodbye_played() -> None:
            started = False
            async for bev in bus.subscribe(types=(BotSpeakingEvent,)):
                if not isinstance(bev, BotSpeakingEvent):
                    continue
                if bev.speaking:
                    started = True
                elif started:
                    return

        waiter = asyncio.create_task(
            _goodbye_played(), name=f"meeting-goodbye-wait-{room}"
        )
        await asyncio.sleep(0.05)  # let the subscription register first
        try:
            if active.bridge is not None:
                await active.bridge.speak_silence_close()  # type: ignore[attr-defined]
            await asyncio.wait_for(waiter, timeout=45.0)
            await asyncio.sleep(1.0)  # small playout pad after the stop signal
        except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
            log.debug("[meeting] goodbye wait ended early", room=room, err=str(e)[:120])
        finally:
            if not waiter.done():
                waiter.cancel()
        # Schedule (not await) the teardown: teardown cancels+awaits this very
        # watcher task, so awaiting it from here would self-deadlock.
        asyncio.create_task(
            self._teardown(room, reason="silence-timeout"),
            name=f"meeting-silence-teardown-{room}",
        )

    async def _on_participant_left(
        self, active: _ActiveMeeting, participant_id: str
    ) -> None:
        """When a participant leaves, if no HUMANS remain, end the meeting AFTER a
        short grace window — a transient WebRTC reconnect (candidate drops for a
        few seconds) must not kill an active meeting. A (re)join cancels the grace
        timer (see `on_participant_connected`)."""
        humans_left = self._human_count(active)
        log.info("[meeting] participant left", room=active.room, humans_left=humans_left)
        if humans_left > 0:
            return

        s = get_settings()
        grace = s.meeting_empty_grace_s
        if grace <= 0:
            await self._teardown(active.room, reason="all-humans-left")
            return

        # Schedule a graced teardown (idempotent — replace any existing timer).
        self._cancel_grace(active)

        async def _graced() -> None:
            try:
                await asyncio.sleep(grace)
            except asyncio.CancelledError:
                return
            # Re-check: a human may have reconnected during the grace window.
            live = self._meetings.get(active.room)
            if live is not None and self._human_count(live) <= 0:
                await self._teardown(active.room, reason="all-humans-left")

        active.grace_task = asyncio.create_task(
            _graced(), name=f"meeting-grace-{active.room}"
        )
        log.info("[meeting] last human left — grace window started", room=active.room, grace_s=grace)

    def _human_count(self, active: _ActiveMeeting) -> int:
        """Number of non-agent participants currently in the room."""
        s = get_settings()
        try:
            ids = active.transport.get_participants()  # remote participant sids
        except Exception:  # noqa: BLE001
            return 0
        # remote_participants excludes the local (agent) participant, so every
        # entry here is a human.
        return len(ids)

    async def _teardown(self, room: str, *, reason: str) -> None:
        active = self._meetings.pop(room, None)
        if active is None:
            return
        log.info("[meeting] tearing down", room=room, reason=reason)

        # Cancel the no-show + grace timers so neither fires after teardown.
        self._cancel_wait(active)
        self._cancel_grace(active)

        # Finalise analysis BEFORE cancelling the pipeline so the transcript is
        # complete. Best-effort; never blocks teardown on a failure.
        try:
            await self._finalize_analysis(active, end_reason=reason)
        except Exception as e:  # noqa: BLE001
            log.warning("[meeting] finalize analysis failed", room=room, err=str(e))

        try:
            await active.pipeline_task.cancel()
        except Exception as e:  # noqa: BLE001
            log.debug("[meeting] pipeline cancel error", room=room, err=str(e))

        if active.run_task is not None and not active.run_task.done():
            active.run_task.cancel()
            try:
                await active.run_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        # Close the event bus FIRST — this makes every subscriber generator
        # (silence monitor, metrics sink, silence-close watcher, the bridge's
        # responder listener) return cleanly, so the background tasks below
        # finish on their own rather than needing a hard cancel.
        try:
            await close_bus(active.session.conversation_id)
        except Exception as e:  # noqa: BLE001
            log.debug("[meeting] bus close error", room=room, err=str(e))

        # Cancel + await any human-simulation background tasks still running.
        for t in active.background_tasks:
            if t is asyncio.current_task():
                continue  # never await ourselves (guard if teardown ever runs inside a bg task)
            if not t.done():
                t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        # Delete the LiveKit room (best-effort, service-aware) so a finished
        # meeting doesn't linger on the SFU until the empty-timeout. No-op in
        # direct mode if LiveKit creds aren't set.
        try:
            from agent_backend.integrations import livekit_service as svc

            if svc.enabled():
                await svc.delete_room(room)
        except Exception as e:  # noqa: BLE001
            log.debug("[meeting] room delete non-fatal", room=room, err=str(e))

        log.info("[meeting] torn down", room=room)

    async def _finalize_analysis(self, active: _ActiveMeeting, *, end_reason: str) -> None:
        """Flush the diarised transcript to the BusinessLayer for DUAL analysis.

        Reuses the existing close-session contract; the BusinessLayer analyzer's
        new meeting mode (M5) reads the speaker-tagged transcript and scores both
        the candidate and the counsellor. Best-effort + no-op when BusinessLayer
        is unconfigured."""
        from agent_backend.channels.meeting.analysis import submit_meeting_for_analysis

        await submit_meeting_for_analysis(
            session=active.session,
            room=active.room,
            transcript=active.transcript,
            candidate_lead_id=active.candidate_lead_id,
            end_reason=end_reason,
        )

    async def _open_business_session(self, session: Session) -> None:
        try:
            from agent_backend.integrations import business as biz

            await biz.open_session(session, direction="meeting")
        except Exception as e:  # noqa: BLE001
            log.debug("[meeting] business open_session failed", err=str(e))

    def _on_pipeline_done(self, task: asyncio.Task, room: str) -> None:
        if task.cancelled():
            log.info("[meeting] pipeline cancelled", room=room)
        else:
            exc = task.exception()
            if exc:
                log.warning(
                    "[meeting] pipeline crashed",
                    room=room, err_type=type(exc).__name__, err=str(exc),
                )
            else:
                log.info("[meeting] pipeline exited cleanly", room=room)
        # If the pipeline died on its own (not via teardown), make sure analysis
        # still runs by routing through teardown once.
        if room in self._meetings:
            asyncio.create_task(self._teardown(room, reason="pipeline-exit"))


# ---------------------------------------------------------------------------
_manager: MeetingSessionManager | None = None


def get_meeting_manager() -> MeetingSessionManager:
    global _manager
    if _manager is None:
        _manager = MeetingSessionManager()
    return _manager
