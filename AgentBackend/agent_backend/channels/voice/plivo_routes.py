"""Plivo voice — Answer-URL XML + media-streaming WebSocket.

  Plivo call ─► POST /api/voice/plivo/answer ──► returns <Stream> XML
                                                      │
  Plivo opens ─► WS  /api/voice/plivo/media   ◄───────┘
                      │  PlivoFrameSerializer ↔ Pipecat pipeline (same brain)

Additive to the ACS path — `routes.py` and `media_ws.py` are untouched. This
module is only mounted/used when `VOICE_PROVIDER=plivo`. Plivo streams μ-law
8 kHz; the `PlivoFrameSerializer` + pipeline resample to/from the canonical
16 kHz, so the brain/STT/TTS are identical to the ACS path.

Outbound: `PlivoVoiceProvider.dial()` sets `answer_url=…/plivo/answer?lead_id=X`,
so the answer route already knows the lead.
Inbound:  point the Plivo number's Application Answer URL at `…/plivo/answer`
(no lead_id); the route resolves/creates the lead by caller number.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from urllib.parse import parse_qs

from fastapi import APIRouter, Request, Response, WebSocket
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask

from agent_backend.channels.voice.events import close_bus, get_or_create_bus
from agent_backend.channels.voice.pipeline import (
    build_voice_pipeline_v2,
    build_voice_transport,
)
from agent_backend.channels.voice.routes import (
    cancel_live_pipeline,
    register_live_pipeline,
    resolve_lead_for_call,
    set_pending_context,
    take_pending_direction,
    unregister_live_pipeline,
)
from agent_backend.config import get_settings
from agent_backend.data import LeadRepo
from agent_backend.infra import get_logger
from agent_backend.llm_agent.session import Session

log = get_logger(__name__)

plivo_router = APIRouter(tags=["voice-plivo"])


def _wss_media_url(lead_id: str) -> str:
    """wss:// URL Plivo's <Stream> connects to, carrying the lead_id."""
    s = get_settings()
    base = (s.public_base_url or "").rstrip("/")
    https = base if base.startswith("http") else f"https://{base}"
    wss = "wss://" + https.split("://", 1)[1]
    return f"{wss}/api/voice/plivo/media?lead_id={lead_id}"


@plivo_router.post("/plivo/answer")
async def plivo_answer(request: Request) -> Response:
    """Plivo Answer URL → returns XML telling Plivo to open a bidirectional
    audio stream to our media WS.

    Used for BOTH outbound (lead_id arrives on the query string, set by
    PlivoVoiceProvider.dial) and inbound (no lead_id → resolve by caller number).
    """
    lead_id = request.query_params.get("lead_id")
    if not lead_id:
        # Inbound. Plivo POSTs From/To/CallUUID form-encoded — but request.form()
        # comes back EMPTY for Plivo's callbacks (needs python-multipart), so we
        # parse the raw body ourselves (same approach as the hangup route).
        from_num = ""
        try:
            raw = await request.body()
            if raw:
                parsed = parse_qs(raw.decode("utf-8", "ignore"))
                from_num = (parsed.get("From", [""])[0] or "").strip()
        except Exception:  # noqa: BLE001
            from_num = ""
        if not from_num:  # query-string fallback
            from_num = (request.query_params.get("From") or "").strip()

        # Identify the caller by phone against the BusinessLayer (system of
        # record) BEFORE answering, so the opener greets by name and memory
        # hydrates by the real lead_id. Best-effort: on miss/down, fall back to
        # an in-memory inbound lead so the call still connects.
        from agent_backend.integrations import business as _biz

        biz = await _biz.find_lead_by_phone(from_num) if from_num else None
        if biz and biz.get("lead_id"):
            lead_id = str(biz["lead_id"])
            set_pending_context(
                lead_id,
                {
                    "phone": from_num,
                    "full_name": biz.get("full_name"),
                    "language": biz.get("language_preference") or "en",
                    "source": "inbound_voice_plivo",
                    "direction": "inbound",
                },
            )
            log.info("[plivo] inbound matched lead", from_=from_num, lead_id=lead_id,
                     name=biz.get("full_name"))
        else:
            lead = LeadRepo.get().find_or_create_by_phone(
                from_num or "unknown", source="inbound_voice_plivo"
            )
            lead_id = lead.lead_id
            set_pending_context(
                lead_id,
                {"phone": from_num, "full_name": lead.full_name,
                 "language": lead.language_preference, "source": "inbound_voice_plivo",
                 "direction": "inbound"},
            )
            log.info("[plivo] inbound unknown caller", from_=from_num, lead_id=lead_id)
    else:
        log.info("[plivo] outbound answer", lead_id=lead_id)

    ws_url = _wss_media_url(lead_id)
    # contentType MUST match what PlivoFrameSerializer speaks on the wire:
    # μ-law 8 kHz. bidirectional=true so we can both receive and play audio.
    # keepCallAlive="true" is REQUIRED — without it Plivo doesn't establish the
    # bidirectional stream / never sends the `start` event, so the call is cut
    # on pickup. The "pipeline keeps running after hangup" problem is handled
    # by cancelling the pipeline on hangup (see PlivoVoiceProvider.hangup +
    # register_live_pipeline below), NOT by touching this XML.
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        '<Stream bidirectional="true" keepCallAlive="true" '
        f'contentType="audio/x-mulaw;rate=8000">{ws_url}</Stream>'
        "</Response>"
    )
    return Response(content=xml, media_type="text/xml")


@plivo_router.post("/plivo/hangup")
async def plivo_hangup(request: Request) -> Response:
    """Plivo Hangup-URL callback → tear the live pipeline down the instant the
    call ends, especially when the *candidate* hangs up.

    Why this is needed: the <Stream> uses keepCallAlive="true" (required for the
    bidirectional stream to establish), so Plivo keeps the media WebSocket open
    after the human disconnects. Pipecat never observes the close, the runner
    keeps blocking, and the silence monitor keeps firing the brain into a dead
    line until T4's end_call eventually hangs up (~60-80 s of dead air). This
    callback fires on the real hangup and cancels the registered pipeline task
    immediately (~1 s) — the Plivo equivalent of ACS's CallDisconnected webhook.

    Wiring: set as `hangup_url` on outbound calls (see PlivoVoiceProvider.dial);
    for inbound numbers, set the same URL as the Plivo Application's Hangup URL.
    Plivo POSTs form-encoded CDR fields; we only need CallUUID. cancel is
    idempotent, so a hangup we initiated ourselves (end_call → provider.hangup,
    which already cancels) re-firing here is a harmless no-op.
    """
    # Plivo POSTs CDR fields as application/x-www-form-urlencoded. Parse the raw
    # body ourselves — `request.form()` came back EMPTY in testing (so call_uuid
    # was blank and the pipeline never got cancelled), and parsing the bytes
    # avoids the python-multipart dependency request.form() needs for multipart.
    # Query params are a fallback. We log content-type + keys so the payload
    # shape is visible if a field name ever differs.
    raw = b""
    try:
        raw = await request.body()
    except Exception:  # noqa: BLE001
        pass
    ctype = request.headers.get("content-type", "")
    params: dict[str, str] = {}
    if raw and "json" in ctype.lower():
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                params = {str(k): str(v) for k, v in data.items()}
        except Exception:  # noqa: BLE001
            pass
    elif raw:
        for k, vs in parse_qs(raw.decode("utf-8", "ignore")).items():
            if vs:
                params[k] = vs[0]
    for k, v in request.query_params.items():
        params.setdefault(k, v)

    def _get(name: str) -> str:
        # Case-insensitive lookup — Plivo uses CallUUID, but be defensive.
        for k, v in params.items():
            if k.lower() == name.lower():
                return (v or "").strip()
        return ""

    call_uuid = _get("CallUUID")
    # NB: `event` is structlog's reserved positional (the message), so the
    # Plivo "Event" field is logged under `plivo_event`.
    log.info(
        "[plivo] hangup callback",
        call_uuid=call_uuid,
        plivo_event=_get("Event"),
        cause=_get("HangupCause"),
        source=_get("HangupSource"),
        ctype=ctype,
        body_len=len(raw),
        keys=sorted(params.keys()),
    )
    if call_uuid:
        await cancel_live_pipeline(call_uuid)
    else:
        log.warning(
            "[plivo] hangup callback had no CallUUID — pipeline NOT cancelled; "
            "see `keys` above for what Plivo actually sent",
        )

    # Missed OUTBOUND call (busy / rejected / never answered) → WhatsApp
    # outreach via the approved template, so the candidate still hears from us.
    # Answered calls (AnswerTime set + CallStatus 'completed') are untouched:
    # their follow-ups flow through the post-call analyzer as before. Fired as a
    # background task (it does BusinessLayer + Plivo round-trips) so the 200 ack
    # below stays instant; the helper itself never raises and enforces consent
    # + a per-number cooldown.
    direction = _get("Direction").lower()
    call_status = _get("CallStatus").lower()
    to_num = _get("To")
    answered = bool(_get("AnswerTime")) and call_status in ("", "completed")
    if direction == "outbound" and to_num and not answered:
        from agent_backend.channels.whatsapp.send_routes import send_missed_call_outreach

        log.info(
            "[plivo] outbound call missed — queueing whatsapp outreach",
            call_uuid=call_uuid, call_status=call_status,
            cause=_get("HangupCauseName") or _get("HangupCause"),
        )
        _spawn_background(
            send_missed_call_outreach(
                phone=to_num,
                cause=_get("HangupCauseName") or _get("HangupCause") or call_status,
            )
        )

    # Plivo ignores the body of a hangup callback — ack 200 fast.
    return Response(status_code=200)


# Strong refs so fire-and-forget tasks aren't garbage-collected mid-flight.
_bg_tasks: set[asyncio.Task] = set()


def _spawn_background(coro) -> None:  # noqa: ANN001
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


@plivo_router.websocket("/plivo/media")
async def plivo_media(ws: WebSocket) -> None:
    """Plivo opens this WS per call (from the <Stream> in the answer XML).

    Unlike ACS, the Plivo serializer needs the per-stream identity (stream_id +
    call_id), which arrives in the first `start` event — so we read it off the
    socket before building the serializer + pipeline.
    """
    await ws.accept()
    lead_id = ws.query_params.get("lead_id") or "unknown"

    # Read Plivo's opening message(s) until the 'start' event carries the
    # stream identity. (Plivo sends JSON text frames.)
    stream_id: str | None = None
    plivo_call_id: str | None = None
    for _ in range(10):
        try:
            raw = await ws.receive_text()
        except Exception:  # noqa: BLE001
            break
        try:
            msg = json.loads(raw)
        except Exception:  # noqa: BLE001
            continue
        if msg.get("event") == "start":
            start = msg.get("start") or {}
            stream_id = start.get("streamId") or msg.get("streamId")
            plivo_call_id = start.get("callId") or msg.get("callId")
            break

    if not stream_id:
        log.warning("[plivo-media] no start event received — closing", lead_id=lead_id)
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
        return

    # Build the Plivo serializer with the stream identity + creds (creds let it
    # auto-hang-up the Plivo call when the pipeline emits End/Cancel).
    from pipecat.serializers.plivo import PlivoFrameSerializer

    s = get_settings()
    serializer = PlivoFrameSerializer(
        stream_id,
        call_id=plivo_call_id,
        auth_id=s.plivo_auth_id,
        auth_token=s.plivo_auth_token,
        params=PlivoFrameSerializer.InputParams(
            plivo_sample_rate=8000,                  # Plivo wire rate (μ-law 8 kHz)
            sample_rate=s.pipecat_audio_sample_rate,  # canonical pipeline rate
            auto_hang_up=True,
        ),
    )

    # ----- Identity resolution (same shape as the ACS media handler) -----
    # From the dial-time payload (outbound) or in-memory repo (inbound) — not
    # leads.json. BusinessLayer memory is layered on by lead_id in setup_session.
    lead = resolve_lead_for_call(lead_id)
    # Call direction (set by the answer webhook, carried via the pending context
    # that resolve_lead_for_call just popped). Defaults to "outbound" on a miss.
    direction = take_pending_direction(lead_id)
    session = Session(
        channel="voice",
        conversation_id=f"call-{uuid.uuid4().hex[:12]}",
        lead_id=lead_id if lead else None,
        lead_status=lead.status.value if lead else None,
        language=lead.language_preference if lead else "en",
        display_name=lead.full_name if lead else None,
        call_id=plivo_call_id,
        direction=direction,  # type: ignore[arg-type]  ("inbound"|"outbound")
    )
    log.info(
        "[plivo-media] connected",
        lead_id=lead_id,
        call_id=plivo_call_id,
        direction=direction,
        session=session.short(),
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
                session, lead=lead, direction=direction, provider_call_id=plivo_call_id
            ),
            name=f"biz-{session.short()}",
        ),
        asyncio.create_task(
            asyncio.to_thread(_prewarm_graph, session),
            name=f"warm-graph-{session.short()}",
        ),
    ]

    # ----- Build + run the pipeline (provider-agnostic from here) -----
    transport = build_voice_transport(ws, serializer=serializer)
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

    bg_tasks: list[asyncio.Task] = [
        asyncio.create_task(factory(), name=f"bg-{session.short()}-{i}")
        for i, factory in enumerate(composed.background_tasks)
    ]
    # Track the warmups too so a dropped call cancels/awaits them on teardown.
    bg_tasks.extend(warmup_tasks)

    runner = PipelineRunner(handle_sigint=False)
    # Register so PlivoVoiceProvider.hangup() can cancel this task on end_call
    # (Plivo's REST hangup won't reliably close the WS, so without this the
    # runner blocks and the silence monitor keeps firing into a dead call).
    register_live_pipeline(plivo_call_id, task)
    log.info(
        "[plivo-media] pipeline running",
        session=session.short(),
        modules=composed.composition_summary or ["(default chain)"],
        bg_tasks=len(bg_tasks),
    )
    try:
        await runner.run(task)
    except Exception as e:  # noqa: BLE001
        log.warning("[plivo-media] pipeline crashed session=%s err=%s", session.short(), e)
    finally:
        unregister_live_pipeline(plivo_call_id)
        for bgt in bg_tasks:
            if not bgt.done():
                bgt.cancel()
        for bgt in bg_tasks:
            try:
                await bgt
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await close_bus(session.conversation_id)
        try:
            await _biz.close_session(session, end_reason="call_ended")
        except Exception as e:  # noqa: BLE001
            log.debug("[plivo-media] business close failed", err=str(e))
        log.info("[plivo-media] closed lead_id=%s session=%s", lead_id, session.short())
