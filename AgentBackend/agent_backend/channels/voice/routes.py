"""Voice channel HTTP surface.

POST /api/voice/dial            — kick off an outbound call by lead_id
POST /api/voice/acs/events      — ACS call-lifecycle webhook (CallConnected, ...)
POST /api/voice/acs/incoming    — ACS IncomingCall webhook (Event Grid)

Leads are resolved by `lead_id` against `agent_backend.data.LeadRepo`, which
reads `test-data/leads.json` today and will read Postgres later — same API.

The media WebSocket lives in `media_ws.py`. The provider tells ACS where
to open it (the `media_ws_url` we pass into DialRequest / AnswerRequest).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from agent_backend.channels.voice.providers import get_voice_provider
from agent_backend.channels.voice.providers.base import AnswerRequest, DialRequest
from agent_backend.config import get_settings
from agent_backend.data import LeadRepo
from agent_backend.infra import get_logger

log = get_logger(__name__)

router = APIRouter(tags=["voice"])

# Active-calls registry: maps `lead_id` → `call_id` returned by the provider's
# dial(). Populated by POST /dial; read by the media WS handler when ACS
# opens the audio stream (which happens AFTER dial returns). Cleaned up
# in the WS handler's finally block.
_ACTIVE_CALLS: dict[str, str] = {}


def take_active_call(lead_id: str) -> str | None:
    """One-shot lookup: returns the call_id and clears the entry so a
    subsequent dial doesn't accidentally re-use it. Called from media_ws.py
    when ACS opens the WebSocket for this lead."""
    return _ACTIVE_CALLS.pop(lead_id, None)


# Pending-call context registry: maps `lead_id` → the dial-time payload
# (phone / name / language). POST /dial now takes the candidate's details from
# the request body — NOT from leads.json — and stashes them here so the media-WS
# handler can build the Session for the call WITHOUT depending on leads.json.
# One-shot (popped on connect), exactly like _ACTIVE_CALLS.
_PENDING_CONTEXT: dict[str, dict[str, Any]] = {}


def set_pending_context(lead_id: str, ctx: dict[str, Any]) -> None:
    if lead_id:
        _PENDING_CONTEXT[lead_id] = ctx


def take_pending_context(lead_id: str) -> dict[str, Any] | None:
    return _PENDING_CONTEXT.pop(lead_id, None)


# Call DIRECTION registry: maps `lead_id` → "inbound" | "outbound" for the
# in-flight call. Populated by `resolve_lead_for_call` (which pops the pending
# context, so the direction inside it would otherwise be lost) and read ONCE by
# the media-WS handler when it builds the Session. One-shot like the others; a
# miss defaults to "outbound" (the historical behaviour), so nothing breaks if a
# path never set it.
_PENDING_DIRECTION: dict[str, str] = {}


def take_pending_direction(lead_id: str) -> str:
    """Pop the recorded direction for this call's lead_id. Defaults to
    'outbound' when unknown (the prior, only behaviour)."""
    return _PENDING_DIRECTION.pop(lead_id, "outbound")


def resolve_lead_for_call(lead_id: str) -> Any | None:
    """Resolve the Lead for an outbound/inbound call's media WS, WITHOUT relying
    on leads.json:

      1. If the lead is already in the in-memory repo (inbound created it, or a
         seed exists) → use it.
      2. Else, if POST /dial stashed a pending context for this lead_id (the
         payload-driven outbound path) → synthesize an in-memory Lead from it and
         register it (in-memory only; _persist is a no-op).
      3. Else → None (call proceeds with a minimal Session; BusinessLayer memory
         still hydrates by lead_id).

    Durable facts/summary are layered on afterwards by `business.hydrate_memory`
    using `lead_id` — so the lead's history comes from the BusinessLayer, the
    call's identity from the dial payload, and leads.json is not consulted.
    """
    ctx = take_pending_context(lead_id)  # always pop (cleanup), even if unused
    # Carry the call direction (set by the answer webhook in the ctx) over to a
    # one-shot registry the media-WS handler reads when building the Session —
    # the ctx itself is popped here, so without this the direction would be lost.
    if lead_id and ctx and ctx.get("direction"):
        _PENDING_DIRECTION[lead_id] = str(ctx["direction"])
    lead = LeadRepo.get().get_by_id(lead_id)
    if lead is not None:
        return lead
    if ctx and ctx.get("phone"):
        from agent_backend.data.leads import Lead

        lead = Lead(
            lead_id=lead_id,
            full_name=ctx.get("full_name") or "Unknown",
            phone_e164=ctx.get("phone"),
            language_preference=ctx.get("language") or "en",
            source=ctx.get("source") or "outbound_dial",
        )
        LeadRepo.get().upsert(lead)  # in-memory only
        return lead
    return None


# Live-pipeline registry: maps `call_id` (ACS callConnectionId) → the running
# Pipecat `PipelineTask` for that call's media WS. The ACS event webhook uses
# it to tear a pipeline down the instant a call ends.
#
# Why this is needed: on hangup ACS interrupts the media stream, but the
# server-side WebSocket doesn't always observe the close (half-open TCP), so
# `runner.run()` can keep blocking — and the silence monitor keeps firing,
# calling the LLM long after the caller is gone. Cancelling the task on the
# CallDisconnected event unblocks the media-WS handler's `finally` (which
# cancels background tasks and closes the event bus).
_LIVE_PIPELINES: dict[str, Any] = {}


def register_live_pipeline(call_id: str, task: Any) -> None:
    """Record the running pipeline task for `call_id` so the event webhook
    can cancel it on hangup. No-op if call_id is falsy."""
    if call_id:
        _LIVE_PIPELINES[call_id] = task


def unregister_live_pipeline(call_id: str) -> None:
    """Drop the registry entry (called from the media-WS handler's finally)."""
    if call_id:
        _LIVE_PIPELINES.pop(call_id, None)


async def cancel_live_pipeline(call_id: str) -> None:
    """Cancel + drop the running pipeline task for `call_id` (idempotent).

    For providers whose hangup terminates the carrier leg out-of-band (e.g.
    Plivo's REST `calls.hangup`, which doesn't reliably close our media WS),
    this unblocks the Pipecat runner immediately instead of letting it keep
    running — and the silence monitor keep firing — into a dead socket. ACS
    gets the same effect via its CallDisconnected webhook above; this is the
    equivalent hook for providers without a lifecycle webhook."""
    if not call_id:
        return
    task = _LIVE_PIPELINES.pop(call_id, None)
    if task is None:
        return
    try:
        await task.cancel()
    except Exception:  # noqa: BLE001
        pass


class DialRequestBody(BaseModel):
    # The caller (e.g. the BusinessLayer dialer) owns the lead data and passes it
    # in the body — no dependency on leads.json. BOTH fields are required:
    #   - lead_id: correlates the call AND is the key BusinessLayer memory is
    #     fetched under on pickup. Without it we can't load the candidate's
    #     history, so we refuse to dial.
    #   - to_e164: the number to dial.
    lead_id: str = Field(..., min_length=1, examples=["test-lead-1"])
    to_e164: str = Field(..., min_length=1, examples=["+919999999999"], description="Number to dial.")
    full_name: str | None = Field(default=None)
    language: str | None = Field(default=None)


@router.post("/dial")
async def dial(body: DialRequestBody) -> dict[str, Any]:
    """Dial a candidate through the configured provider.

    The number + identity come from the request body; `lead_id` (required) keys
    the BusinessLayer memory fetched on pickup. leads.json is NOT consulted — a
    missing `lead_id`/`to_e164` is rejected (422) rather than dialed blind.
    """
    s = get_settings()
    if not s.public_base_url:
        raise HTTPException(500, "PUBLIC_BASE_URL not set — provider needs an https callback URL")

    lead_id = body.lead_id
    to_e164 = body.to_e164.strip()
    if not to_e164 or to_e164.startswith("+910000"):
        raise HTTPException(400, "missing/placeholder phone — provide a real `to_e164` in the payload")

    # Stash the dial-time context so the media-WS handler can build the Session
    # for this call without touching leads.json (see resolve_lead_for_call).
    set_pending_context(
        lead_id,
        {"phone": to_e164, "full_name": body.full_name, "language": body.language,
         "source": "outbound_dial", "direction": "outbound"},
    )

    # ACS reaches our server at these URLs. PUBLIC_BASE_URL must be HTTPS
    # and reachable from Azure (ngrok during dev; Azure App Service / Front
    # Door in production). Media WS URL is the wss equivalent of the same host.
    base = s.public_base_url.rstrip("/")
    https = base if base.startswith("http") else f"https://{base}"
    wss = "wss://" + https.split("://", 1)[1]

    callback_url = f"{https}/api/voice/acs/events"

    # ACS opens the media WebSocket from the URL we hand to the provider
    # below. We need the WS handler to know BOTH the lead_id (for Lead lookup)
    # and the call_id (so the end_call tool can hang up). lead_id we have now;
    # call_id is only assigned AFTER provider.dial() returns, so we mint the
    # URL in two stages: first stage carries lead_id, then we re-mint with
    # call_id and update the provider request before it actually places the
    # call. Because `provider.dial()` is the call that triggers ACS to open
    # the WS, we must have both query params on it BEFORE that call.
    provider = get_voice_provider()

    # Stage 1: dial without call_id. ACS gives us back the call_id; if WS
    # has already opened by then we're racing, so we want call_id available
    # BEFORE ACS picks up the URL. Trick: ACS uses our URL only after we tell
    # it to via create_call — and create_call IS the dial() call. So we have
    # one shot: pre-mint a placeholder lead_id-only URL, dial, then ACS will
    # open the WS knowing only lead_id. We hand the call_id over to the WS
    # handler via a registry (active_calls).
    media_ws_url = f"{wss}/api/voice/acs/media?lead_id={lead_id}"
    req = DialRequest(
        to_e164=to_e164,
        from_e164=s.acs_from_number,
        callback_url=callback_url,
        media_ws_url=media_ws_url,
        correlation={"lead_id": lead_id},
    )
    # Wrap the ACS dial in clean error handling. The Azure SDK's own retry
    # policy already covers transient HTTP errors, but if the connection
    # never returns a response (e.g. RemoteDisconnected — Azure-side blip
    # or our network), it raises ServiceResponseError after exhausting
    # retries. We surface that as a clean 502 so the caller can retry,
    # instead of dumping a 500-line stack trace in uvicorn logs.
    try:
        res = await provider.dial(req)
    except Exception as e:  # noqa: BLE001
        # Distinguish carrier/network errors from code bugs in the message.
        err_name = type(e).__name__
        err_msg = str(e) or "<no message>"
        log.warning(
            "[voice] dial failed",
            provider=provider.name,
            lead_id=lead_id,
            err_type=err_name,
            err=err_msg[:200],
        )
        # ServiceResponseError / ConnectionError-shaped → transient. Tell the
        # caller to retry. Anything else → 500 (config / code issue).
        is_transient = (
            "ServiceResponse" in err_name
            or "Connection" in err_name
            or "RemoteDisconnected" in err_msg
            or "Timeout" in err_name
        )
        status = 502 if is_transient else 500
        raise HTTPException(
            status,
            detail={
                "ok": False,
                "stage": "provider.dial",
                "provider": provider.name,
                "error": err_name,
                "message": err_msg[:200],
                "hint": (
                    "Transient carrier/network error. Just retry the dial — "
                    "this is not a code or config problem."
                    if is_transient
                    else "Check ACS connection string, from-number, and PUBLIC_BASE_URL."
                ),
            },
        )

    # Register the call_id keyed by lead_id so the WS handler can pick it up
    # when ACS opens the media stream a few seconds later.
    _ACTIVE_CALLS[lead_id] = res.call_id
    log.info(
        "[voice] dialing",
        provider=provider.name,
        lead_id=lead_id,
        to_e164=to_e164,
        call_id=res.call_id,
    )
    return {
        "ok": True,
        "provider": provider.name,
        "lead_id": lead_id,
        "to_e164": to_e164,
        "call_id": res.call_id,
        "status": res.status,
    }


@router.post("/acs/events")
async def acs_events(request: Request) -> dict[str, Any]:
    """ACS call-lifecycle webhook.

    ACS POSTs CloudEvent-shaped envelopes here: CallConnected, ParticipantUpdated,
    MediaStreamingStarted, CallDisconnected, etc. We log and ack 200; richer
    handling (status transitions, hangup → session_summary) lands as we
    iterate on real calls.
    """
    try:
        payload = await request.json()
    except Exception:
        payload = None
    log.info("[voice] acs event", payload=payload)

    # On CallDisconnected, tear down the running pipeline for that call. Without
    # this the silence monitor keeps firing and the brain keeps generating
    # replies into a dead socket after the caller hangs up (ACS interrupts the
    # media stream but the half-open server-side WS isn't reliably detected).
    events = payload if isinstance(payload, list) else ([payload] if payload else [])
    for ev in events:
        if not isinstance(ev, dict):
            continue
        # CloudEvent schema uses `type`; Event Grid schema uses `eventType`.
        ev_type = ev.get("type") or ev.get("eventType")
        if ev_type == "Microsoft.Communication.CallDisconnected":
            call_id = ((ev.get("data") or {}).get("callConnectionId")) or ""
            task = _LIVE_PIPELINES.get(call_id)
            if task is not None:
                log.info("[voice] call disconnected — tearing down pipeline", call_id=call_id)
                try:
                    await task.cancel()
                except Exception as e:  # noqa: BLE001
                    log.warning("[voice] pipeline cancel failed", call_id=call_id, err=str(e))

    return {"ok": True}


# ---------------------------------------------------------------------------
# Inbound voice — ACS Event Grid IncomingCall webhook.
# ---------------------------------------------------------------------------
# ACS routes inbound PSTN calls through Event Grid (NOT the Call Automation
# events endpoint above). When a candidate calls our ACS-owned number, Azure
# fires `Microsoft.Communication.IncomingCall` to whatever Event Grid topic
# we subscribed.
#
# Setup (one-time per environment):
#   Portal → ACS resource → Events → + Event Subscription
#     Event Type: Incoming Call
#     Endpoint type: Web Hook
#     Endpoint:   <PUBLIC_BASE_URL>/api/voice/acs/incoming
#
# On first save, Event Grid POSTs a SubscriptionValidationEvent and expects
# us to echo `validationCode` back as `validationResponse`. After that, every
# inbound call POSTs an IncomingCall event.
#
# Payload shape (per Azure docs):
#   [
#     {
#       "eventType": "Microsoft.Communication.IncomingCall",
#       "data": {
#         "from": {"phoneNumber": {"value": "+91..."}},
#         "to":   {"phoneNumber": {"value": "+1..."}},
#         "incomingCallContext": "<opaque base64>",
#         "correlationId": "...",
#         "callerDisplayName": "..."
#       }
#     }
#   ]
#
# We resolve/create the lead by `from.phoneNumber.value`, then call
# `provider.answer(...)` with the same MediaStreamingOptions outbound uses,
# so the media WS handler downstream is provider-agnostic and lead-keyed
# exactly like outbound.
# ---------------------------------------------------------------------------
@router.post("/acs/incoming")
async def acs_incoming(request: Request) -> Any:
    """ACS Event Grid → IncomingCall handler. Resolves lead, answers the call."""
    s = get_settings()
    if not s.public_base_url:
        # Without a public URL the provider can't open the media WS back to us;
        # safer to refuse the call than to answer-and-fail mid-bridge.
        log.error("[voice] inbound rejected — PUBLIC_BASE_URL not set")
        return Response(status_code=500)

    try:
        payload = await request.json()
    except Exception as e:  # noqa: BLE001
        log.warning("[voice] inbound bad JSON: %s", e)
        return Response(status_code=200)

    # Event Grid delivers events as an array (may be 1 or many in one POST).
    events: list[dict] = payload if isinstance(payload, list) else [payload]

    # First pass: subscription validation handshake. Event Grid sends this
    # exactly once when the subscription is saved; we echo the validation
    # code so Azure knows we own the URL. After that, this branch never fires.
    for ev in events:
        if ev.get("eventType") == "Microsoft.EventGrid.SubscriptionValidationEvent":
            code = (ev.get("data") or {}).get("validationCode") or ""
            log.info("[voice] event-grid subscription validation OK")
            return {"validationResponse": code}

    base = s.public_base_url.rstrip("/")
    https = base if base.startswith("http") else f"https://{base}"
    wss = "wss://" + https.split("://", 1)[1]
    callback_url = f"{https}/api/voice/acs/events"

    provider = get_voice_provider()
    if not provider.capabilities.supports_inbound:
        # Configuration error — wrong VOICE_PROVIDER for an inbound deployment.
        log.warning(
            "[voice] inbound POST but provider doesn't support inbound",
            provider=provider.name,
        )
        return Response(status_code=200)

    answered = 0
    for ev in events:
        if ev.get("eventType") != "Microsoft.Communication.IncomingCall":
            # Other event types (CallStarted, etc.) ack-and-skip.
            continue

        data = ev.get("data") or {}
        from_phone = (((data.get("from") or {}).get("phoneNumber") or {}).get("value")) or ""
        incoming_ctx = data.get("incomingCallContext") or ""
        caller_name = data.get("callerDisplayName") or None

        if not incoming_ctx:
            log.warning("[voice] incoming event missing incomingCallContext", event_id=ev.get("id"))
            continue

        # Resolve / create the lead so the brain has a Lead to render.
        # `find_or_create_by_phone` returns an existing lead if we've seen this
        # number before, else mints `inb-<6hex>` with status=NEW.
        lead = LeadRepo.get().find_or_create_by_phone(from_phone, source="inbound_voice")
        # Stash the caller's spoken display name onto the lead if we don't
        # already know a full name; lets the brain say "Hi <name>" naturally.
        if caller_name and (not lead.full_name or lead.full_name == "Unknown"):
            lead.full_name = caller_name
            LeadRepo.get().upsert(lead)

        media_ws_url = f"{wss}/api/voice/acs/media?lead_id={lead.lead_id}"
        req = AnswerRequest(
            incoming_call_context=incoming_ctx,
            callback_url=callback_url,
            media_ws_url=media_ws_url,
            correlation={"lead_id": lead.lead_id},
        )

        try:
            res = await provider.answer(req)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "[voice] answer failed",
                lead_id=lead.lead_id,
                from_=from_phone,
                err_type=type(e).__name__,
                err=str(e)[:200],
            )
            # Don't 5xx — Event Grid would retry forever. Ack and move on.
            continue

        # Register call_id so the media-WS handler can pick it up exactly
        # like outbound (same active-calls registry pattern).
        _ACTIVE_CALLS[lead.lead_id] = res.call_id
        answered += 1
        log.info(
            "[voice] inbound answered",
            provider=provider.name,
            lead_id=lead.lead_id,
            from_=from_phone,
            call_id=res.call_id,
            caller_name=caller_name,
        )

    return {"ok": True, "answered": answered}
