"""WhatsApp via Plivo — inbound message webhook.

  WhatsApp user ─► Meta WABA ─► Plivo ─► POST /channels/whatsapp/inbound
                                              │
                                              ▼  (background task)
                                          handler.handle_inbound
                                              │
                                              ▼  run_stream(channel="whatsapp") → client.send_text

Plivo delivers each inbound message as an HTTP POST (application/x-www-form-
urlencoded) to the Message URL you configure on the number/app in the Plivo
console:

    Messaging → WhatsApp (or the number's app) → Message URL =
        <PUBLIC_BASE_URL>/channels/whatsapp/inbound        (Method: POST)

Inbound params we use: `From`, `To`, `Type` ("whatsapp"), `Text` (the body),
`MessageUUID`. We ack 200 immediately and process in the background so a slow
LLM turn never trips Plivo's retry-on-timeout. Dedupe is by `MessageUUID`
(Plivo retries reuse the same id).

Mounted in main.py via `app.include_router(whatsapp_router)`. The router carries
its own `/channels/whatsapp` prefix.
"""

from __future__ import annotations

import asyncio
from collections import deque
from urllib.parse import parse_qs

from fastapi import APIRouter, Request, Response

from agent_backend.channels.whatsapp.handler import handle_inbound
from agent_backend.config import get_settings
from agent_backend.infra import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/channels/whatsapp", tags=["whatsapp"])


# Boot fingerprint — confirms creds reached the process without leaking them.
_s = get_settings()
log.info(
    "[whatsapp] channel mounted (provider=plivo)",
    auth_set=bool(_s.plivo_auth_id and _s.plivo_auth_token),
    from_set=bool(_s.plivo_whatsapp_from),
)


# In-memory dedupe by MessageUUID. Plivo retries when the webhook doesn't 2xx
# quickly; retries carry the same id. In-memory only — a restart mid-retry can
# produce at most one duplicate reply, acceptable here.
_RECENT_MAX = 5000
_recent_ids: deque[str] = deque(maxlen=_RECENT_MAX)
_recent_set: set[str] = set()

# Strong refs to in-flight background tasks (so the loop can't GC a live task).
_pending_tasks: set[asyncio.Task] = set()


def _seen(message_id: str) -> bool:
    if message_id in _recent_set:
        return True
    if len(_recent_ids) == _RECENT_MAX:
        _recent_set.discard(_recent_ids[0])
    _recent_ids.append(message_id)
    _recent_set.add(message_id)
    return False


def _parse_form(raw: bytes, ctype: str) -> dict[str, str]:
    """Parse Plivo's form-encoded body ourselves. `request.form()` needs
    python-multipart and came back empty for Plivo's callbacks in testing, so we
    decode the raw bytes (same approach as the Plivo hangup route)."""
    params: dict[str, str] = {}
    if raw and "json" not in ctype.lower():
        for k, vs in parse_qs(raw.decode("utf-8", "ignore")).items():
            if vs:
                params[k] = vs[0]
    return params


@router.post("/inbound")
async def inbound(request: Request) -> Response:
    """Plivo inbound WhatsApp message → schedule processing, ack 200 fast."""
    try:
        raw = await request.body()
    except Exception:  # noqa: BLE001
        raw = b""
    params = _parse_form(raw, request.headers.get("content-type", ""))
    # Query-param fallback (Plivo can be configured GET; be defensive).
    for k, v in request.query_params.items():
        params.setdefault(k, v)

    def _get(name: str) -> str:
        for k, v in params.items():
            if k.lower() == name.lower():
                return (v or "").strip()
        return ""

    msg_type = _get("Type").lower()
    from_number = _get("From")
    text = _get("Text") or _get("Body")
    message_id = _get("MessageUUID") or _get("MessageUUID0")

    # Only handle WhatsApp text inbounds. Other types (sms, mms, delivery
    # reports, media) are ack'd-and-skipped so Plivo doesn't retry them.
    if msg_type and msg_type != "whatsapp":
        log.debug("[whatsapp] ignoring non-whatsapp inbound", type=msg_type)
        return Response(status_code=200)
    if not from_number or not text:
        log.debug("[whatsapp] inbound with no sender/text — ack", keys=sorted(params.keys()))
        return Response(status_code=200)
    if message_id and _seen(message_id):
        log.debug("[whatsapp] duplicate inbound ignored", message_id=message_id)
        return Response(status_code=200)

    task = asyncio.create_task(handle_inbound(from_number=from_number, text=text))
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)

    # Plivo ignores the body; ack 200 immediately so work happens async.
    return Response(status_code=200)
