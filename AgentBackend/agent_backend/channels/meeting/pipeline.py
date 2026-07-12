"""Meeting pipeline — LiveKit room ↔ the one brain.

    LiveKitTransport.input()
        ↓  UserAudioRawFrame (per participant track, user_id = participant SID)
    STT stage (per-speaker MeetingSTTRouter, or a single shared make_stt())
        ↓  TranscriptionFrame (user_id propagated → speaker attribution)
    InputAudioSink   (drop raw room audio once STT has it)
        ↓
    [human-simulation chain — flag-driven, mirrors avatar_video's composer]
      LatencyStamper(in) → TurnDetector → MeetingBargeInManager
        ↓
    MeetingAgentBridge(session, gate, speaker_resolver, bus)
        ↓  TextFrame  (THE ONE BRAIN — gated by mode / addressee)
      SentenceStreamer → LatencyStamper(out)
        ↓
    make_tts(strip_markdown=True)
        ↓  TTSAudioRawFrame
    [Simli/SoulX avatar]
        ↓  OutputImageRawFrame + lip-synced avatar audio
    LiveKitTransport.output()
        → publishes the agent's reply (voice + avatar video) into the room.

This is the avatar_video channel's seamless voice stack, wired for LiveKit:
the per-session EventBus ties the turn detector, barge-in manager, silence
monitor and metrics together, exactly like avatar_video/composer.py. The
meeting-specific parts (addressee gate, per-speaker STT, speaker attribution,
diarised transcript) are unchanged. Background tasks (silence monitor, metrics
sink) are returned as factories for the runner to start/stop with the session.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pipecat.pipeline.pipeline import Pipeline

from agent_backend.channels.meeting.audio_sink import InputAudioSink
from agent_backend.channels.meeting.barge_in import MeetingBargeInManager
from agent_backend.channels.meeting.bridge import (
    MeetingAgentBridge,
    SpeakerResolver,
    TranscriptSink,
    TurnSink,
)
from agent_backend.channels.meeting.events import EventBus
from agent_backend.channels.meeting.stt_router import MeetingSTTRouter
from agent_backend.channels.pipecat.services import make_stt, make_tts
from agent_backend.config import get_settings
from agent_backend.infra import get_logger
from agent_backend.llm_agent.addressee import AddresseeGate, extract_name_and_aliases
from agent_backend.llm_agent.identity import get_identity
from agent_backend.llm_agent.session import Session

log = get_logger(__name__)

# No-arg coroutine factory the runner turns into asyncio.create_task(...).
BackgroundTask = Callable[[], Any]


def _build_addressee_gate(*, mode: str) -> AddresseeGate:
    """Gate from the active persona JSON.

    The meeting agent is a warm, ACTIVE career counsellor who answers EVERY turn
    (the student shouldn't have to say its name before each question), so the
    gate is OFF by default:

    - solo (1:1 agent+candidate): the agent IS the counsellor → gate OFF,
      answers everything (always, regardless of MEETING_REQUIRE_ADDRESS).
    - panel (agent + 2+ humans): still answers everything by DEFAULT
      (MEETING_REQUIRE_ADDRESS defaults False). Only if an operator explicitly
      sets MEETING_REQUIRE_ADDRESS=true does the legacy "quiet co-pilot that
      waits to be named" behaviour come back — then the agent stays out of the
      human↔human talk and speaks only when addressed.

    The gate is still built (with the bot name + aliases) so the mute / unmute
    verbal controls in the bridge keep working even when require_address is off.

    Falls back to an inert gate (require_address=False) if identity load fails,
    so a misconfigured persona never leaves the agent silent forever."""
    s = get_settings()
    solo = (mode or s.meeting_mode).strip().lower() == "solo"
    try:
        name, aliases = extract_name_and_aliases(get_identity())
    except Exception as e:  # noqa: BLE001
        log.warning("[meeting] identity load failed — gate inert", err=str(e))
        return AddresseeGate(require_address=False)

    gate = AddresseeGate(
        bot_name=name,
        aliases=aliases,
        # Active counsellor: answer everything in solo AND panel. Only the
        # explicit opt-in flag (default False) restores the gated panel co-pilot.
        require_address=False if solo else s.meeting_require_address,
    )
    log.info(
        "[meeting] addressee gate built",
        mode=mode,
        bot_name=gate.bot_name,
        aliases=list(gate.aliases),
        require_address=gate.require_address,
    )
    return gate


def build_meeting_pipeline(
    *,
    transport: object,  # LiveKitTransport
    session: Session,
    mode: str,
    speaker_resolver: SpeakerResolver | None = None,
    name_resolver: "Callable[[str], str] | None" = None,
    transcript_sink: TranscriptSink | None = None,
    turn_sink: TurnSink | None = None,
    avatar_service: object | None = None,  # AgentSimliVideoService | None
    human_count_fn=None,  # Callable[[], int] | None — live human count for gating
    agent_user_id: str = "",  # the agent's own participant id (its track is not transcribed)
    bus: EventBus | None = None,  # per-meeting event bus (human-sim modules)
) -> tuple[Pipeline, MeetingAgentBridge, object | None, list[BackgroundTask]]:
    """Compose the meeting graph: LiveKit in → STT → sink → [human-sim chain] →
    gated bridge → [SentenceStreamer] → TTS → [Simli avatar] → LiveKit out.
    `mode` ('solo' | 'panel') decides the addressee gate behaviour;
    `speaker_resolver` maps a participant SID to its role so the bridge can
    attribute each turn; `transcript_sink` records the diarised transcript for
    the end-of-meeting dual analysis; `turn_sink` durably persists each turn;
    `avatar_service`, when provided, turns the agent's TTS audio into a
    lip-synced Simli video+audio track published into the room (else audio-only);
    `bus` ties the turn detector / barge manager / silence monitor / metrics
    together (None → those modules are skipped, mirroring avatar's composer).

    Returns (pipeline, bridge, stt_router, background_tasks) — the router is
    handed back (None in single-shared-STT mode) so the runner can wire the SFU
    active-speaker gate to it from the rtc room; the bridge so the runner can
    fire the solo opener + T5 close; background_tasks (silence monitor, metrics
    sink factories) for the runner to start alongside the pipeline and tear down
    on session end."""
    s = get_settings()
    log.info(
        "[meeting] building LiveKit pipeline",
        session=session.short(), mode=mode, avatar=avatar_service is not None,
        barge_in=s.meeting_enable_barge_in,
    )
    needs_bus = (
        s.meeting_enable_turn_detector
        or s.meeting_enable_barge_in
        or s.meeting_enable_silence_manager
        or s.meeting_enable_silence_responder
        or s.meeting_enable_metrics
    )
    if needs_bus and bus is None:
        log.warning(
            "[meeting] flags need event bus but none provided — "
            "modules requiring it will be skipped",
            session=session.short(),
        )
    # ONE gate, shared by the barge-in manager (panel addressee scope) and the
    # bridge (who-the-agent-answers). Built once so both see identical behaviour.
    gate = _build_addressee_gate(mode=mode)
    bridge = MeetingAgentBridge(
        session=session,
        gate=gate,
        speaker_resolver=speaker_resolver,
        name_resolver=name_resolver,
        transcript_sink=transcript_sink,
        turn_sink=turn_sink,
        human_count_fn=human_count_fn,
        bus=bus,
        # Audio-only agent → the bridge publishes BotSpeakingEvent off the
        # transport's Bot frames. Avatar on → the render service publishes the
        # authoritative signal (TTSStarted/TTSStopped + playout drain) instead.
        publish_bot_speaking=(avatar_service is None),
    )
    # PER-SPEAKER STT (the meeting accuracy/hallucination fix). A LiveKit room
    # interleaves every participant's audio onto one processor; feeding that into
    # a single Azure recognizer byte-mixes two voices into one stream (~50% acc +
    # hallucination). The router gives each speaker their OWN recognizer and drops
    # the agent's own track. `is_agent` reuses the speaker_resolver so the agent's
    # SID is recognised by ROLE even before its id is known here.
    def _is_agent(uid: str) -> bool:
        if not uid or speaker_resolver is None:
            return False
        try:
            return (speaker_resolver(uid) or "").strip().lower() == "agent"
        except Exception:  # noqa: BLE001
            return False

    # Meeting STT factory: a LONGER end-of-phrase silence than the phone default
    # so far-field/laptop-mic speech is finalised on whole phrases (better word
    # accuracy), not chopped fragments. Each recognizer is built from this factory.
    def _meeting_make_stt():
        return make_stt(segmentation_silence_ms=s.meeting_stt_segmentation_silence_ms)

    # STT stage. PER-SPEAKER (default) → MeetingSTTRouter: one recognizer per
    # participant (needed in a PANEL so two humans aren't interleaved into one
    # recognizer). SINGLE → exactly the voice channel's wiring (one shared STT as
    # a direct pipeline processor, `transport.input() → stt`), which is the
    # cleanest match to the accurate phone path and best for solo 1:1 meetings.
    if s.meeting_stt_per_speaker:
        stt_stage: object = MeetingSTTRouter(
            make_stt=_meeting_make_stt,
            agent_user_id=agent_user_id,
            is_agent=_is_agent,
            active_speaker_gate=s.meeting_active_speaker_gate,
            active_speaker_factor=s.meeting_active_speaker_factor,
            active_speaker_floor=s.meeting_active_speaker_floor,
            sfu_gate=s.meeting_active_speaker_sfu_gate,
            hangover_ms=s.meeting_active_speaker_hangover_ms,
            stale_s=s.meeting_active_speaker_stale_s,
            lookback_ms=s.meeting_active_speaker_lookback_ms,
            segmentation_silence_ms=s.meeting_stt_segmentation_silence_ms,
            watchdog_s=s.meeting_active_speaker_watchdog_s,
        )
        log.info(
            "[meeting] STT mode = per-speaker (router)",
            session=session.short(), sfu_gate=s.meeting_active_speaker_sfu_gate,
        )
    else:
        stt_stage = _meeting_make_stt()
        log.info(
            "[meeting] STT mode = single shared (voice-channel-identical)",
            session=session.short(), stt=type(stt_stage).__name__,
        )
    summary: list[str] = []
    processors: list[object] = [
        transport.input(),    # type: ignore[attr-defined]
        stt_stage,
        # Drop raw room audio once STT has consumed it. Without this, EVERY
        # participant's 10ms audio frames keep flowing through the barge manager
        # → bridge → streamer → TTS → Simli → output transport, hammering the
        # event loop the WebRTC media is paced on (choppy agent voice). Same
        # position + reason as avatar_video's InputAudioSink (right after STT).
        # The barge manager's acoustic RMS gate degrades gracefully without raw
        # audio — identical contract to the avatar channel.
        InputAudioSink(),
    ]
    if s.meeting_enable_metrics and bus is not None:
        from agent_backend.channels.meeting.latency_stamper import LatencyStamper

        processors.append(LatencyStamper(bus=bus))
        summary.append("latency-stamper(in)")
    # Turn detector: multi-signal turn-state FSM feeding the bus (drives the
    # silence monitor's armed/reset logic + metrics). Observe-only, no swallow.
    if s.meeting_enable_turn_detector and bus is not None:
        from agent_backend.channels.meeting.turn_detector import TurnDetector

        processors.append(TurnDetector(bus=bus))
        summary.append("turn-detector")
    # Smart barge-in (ACK/ANSWER/INTERRUPT/CONFUSED) sits BETWEEN STT and the
    # bridge so it can suppress ack/answer turns before the brain sees them and
    # emit its own InterruptionFrame (→ TTS + Simli clearBuffer) on a real
    # interrupt. In panel mode it only barges on turns addressed to the agent.
    if s.meeting_enable_barge_in:
        processors.append(
            MeetingBargeInManager(
                session=session, gate=gate, human_count_fn=human_count_fn, bus=bus
            )
        )
        summary.append("barge-in-manager")
    processors.append(bridge)
    # SENTENCE STREAMER (avatar seamlessness fix). Sits BETWEEN the bridge and TTS
    # so the bridge's per-token TextFrames are coalesced into whole sentences
    # before synthesis — TTS speaks clean phrases (better prosody, fewer calls)
    # and the SoulX/Simli avatar lip-syncs per sentence, not per micro-chunk. This
    # is the same processor + position the avatar_video channel uses; it's what
    # makes that channel's voice + lip-sync seamless. First sentence still ships
    # immediately, so first-audio latency is unchanged. Gated ON by default.
    if s.meeting_sentence_streaming:
        from agent_backend.channels.meeting.sentence_streamer import SentenceStreamer

        processors.append(SentenceStreamer())
        summary.append("sentence-streamer")
    if s.meeting_enable_metrics and bus is not None:
        from agent_backend.channels.meeting.latency_stamper import LatencyStamper

        processors.append(LatencyStamper(bus=bus))
        summary.append("latency-stamper(out)")
    # strip_markdown=True: safety net behind the meeting style's "no markdown"
    # rule so the agent never speaks symbols aloud into the room.
    processors.append(make_tts(strip_markdown=True))
    # Simli avatar: same processor + position as the avatar_video channel — it
    # consumes the TTS audio and emits OutputImageRawFrame (avatar video) +
    # TTSAudioRawFrame (lip-synced avatar voice), which the LiveKit output then
    # publishes as the agent's camera + mic tracks. Omitted → audio-only agent.
    if avatar_service is not None:
        processors.append(avatar_service)  # type: ignore[arg-type]
    processors.append(transport.output())  # type: ignore[attr-defined]

    # ----- background tasks (NOT in the Pipecat chain) -----
    # Started by the runner alongside the pipeline; they subscribe to the bus
    # and end cleanly when the runner closes the bus on teardown.
    bg: list[BackgroundTask] = []
    if s.meeting_enable_silence_manager and bus is not None:
        from agent_backend.channels.meeting.silence_monitor import run_silence_monitor

        bg.append(lambda: run_silence_monitor(bus, session.conversation_id))
        summary.append("silence-monitor")
    if s.meeting_enable_metrics and bus is not None:
        from agent_backend.channels.meeting.metrics import run_metrics_sink

        bg.append(lambda: run_metrics_sink(bus, session.conversation_id))
        summary.append("metrics-sink")

    log.info(
        "[meeting] pipeline composed",
        session=session.short(),
        modules=summary or ["(default chain)"],
    )
    # Hand back the router instance (only in per-speaker mode) so the runner can
    # feed it the SFU active-speaker set; None in single-shared-STT mode.
    router_out = stt_stage if s.meeting_stt_per_speaker else None
    return Pipeline(processors), bridge, router_out, bg


__all__ = ["build_meeting_pipeline"]
