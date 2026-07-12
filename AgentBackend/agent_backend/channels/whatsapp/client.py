"""Plivo WhatsApp — outbound send (text + media).

Sends WhatsApp messages through Plivo's Messages API (`type="whatsapp"`) using
the synchronous `plivo` SDK wrapped in a worker thread so it never blocks the
event loop. Process-singleton client. Reuses PLIVO_AUTH_ID / PLIVO_AUTH_TOKEN
(the same credentials as Plivo voice); the sender is PLIVO_WHATSAPP_FROM.

The SDK is lazy-imported so the channel mounts cleanly even if `plivo` isn't
installed — the first send logs a clear error and returns None (callers degrade
to a logged warning, never a 500), mirroring the voice provider.

Note the WhatsApp 24-hour customer-care window: free-form text/media only
delivers within 24h of the candidate's last inbound message. For cold outbound
(e.g. after a voice call) WhatsApp requires a pre-approved TEMPLATE — that path
isn't wired here yet; a free-form send outside the window will be rejected by
Plivo/Meta (surfaced as a failed send, not a crash).
"""

from __future__ import annotations

import asyncio
from typing import Any

from agent_backend.config import get_settings
from agent_backend.infra import get_logger

log = get_logger(__name__)


_client: Any = None


def _get_client() -> Any | None:
    """Get-or-create the process-wide Plivo RestClient. Returns None (never
    raises) when creds are unset or the SDK isn't installed."""
    global _client
    if _client is not None:
        return _client

    s = get_settings()
    if not (s.plivo_auth_id and s.plivo_auth_token):
        log.warning("[whatsapp] PLIVO_AUTH_ID / PLIVO_AUTH_TOKEN not set — outbound disabled")
        return None
    try:
        from plivo import RestClient
    except ModuleNotFoundError:
        log.error("[whatsapp] plivo SDK not installed — pip install plivo")
        return None

    _client = RestClient(s.plivo_auth_id, s.plivo_auth_token)
    log.info("[whatsapp] Plivo RestClient initialised")
    return _client


def _digits(number: str) -> str:
    """Plivo addresses numbers as digits (no '+', spaces or hyphens)."""
    return (number or "").strip().lstrip("+").replace(" ", "").replace("-", "")


async def send_text(*, to: str, body: str) -> dict[str, Any]:
    """Send a plaintext WhatsApp message via Plivo. `to` is the recipient's
    number (E.164, with or without '+').

    ALWAYS returns a dict: `{"message_uuid": ...}` on success, `{"error": ...}`
    on failure. Use `sent_ok(result)` to test success — the error string is
    surfaced to callers (and ultimately the task's last_error) so failures are
    diagnosable instead of a silent None."""
    s = get_settings()
    if not s.plivo_whatsapp_from:
        log.warning("[whatsapp] PLIVO_WHATSAPP_FROM not set — outbound disabled")
        return {"error": "outbound disabled: PLIVO_WHATSAPP_FROM not set"}
    client = _get_client()
    if client is None:
        return {"error": "outbound disabled: Plivo client unavailable (check PLIVO_AUTH_ID/TOKEN + SDK)"}

    dst, src = _digits(to), _digits(s.plivo_whatsapp_from)
    if not dst:
        log.warning("[whatsapp] empty recipient — send skipped")
        return {"error": "no recipient"}

    def _send() -> Any:
        return client.messages.create(src=src, dst=dst, text=body, type_="whatsapp")

    try:
        resp = await asyncio.to_thread(_send)
    except Exception as e:  # noqa: BLE001
        log.warning("[whatsapp] Plivo send failed", to=_redact(dst), err=str(e)[:200])
        return {"error": f"provider error: {str(e)[:200]}"}

    message_uuid = _message_uuid(resp)
    log.info("[whatsapp] sent", to=_redact(dst), message_uuid=message_uuid)
    return {"message_uuid": message_uuid}


async def send_media(*, to: str, media_url: str, caption: str | None = None) -> dict[str, Any]:
    """Send a media (image/document) WhatsApp message via Plivo. `media_url`
    must be a publicly reachable HTTPS URL. ALWAYS returns a dict (see
    `send_text`): `{"message_uuid": ...}` on success, `{"error": ...}` on
    failure. Same 24h-window / template rules as `send_text`."""
    s = get_settings()
    if not s.plivo_whatsapp_from:
        log.warning("[whatsapp] PLIVO_WHATSAPP_FROM not set — media send disabled")
        return {"error": "outbound disabled: PLIVO_WHATSAPP_FROM not set"}
    client = _get_client()
    if client is None:
        return {"error": "outbound disabled: Plivo client unavailable (check PLIVO_AUTH_ID/TOKEN + SDK)"}

    dst, src = _digits(to), _digits(s.plivo_whatsapp_from)
    if not dst:
        return {"error": "no recipient"}

    def _send() -> Any:
        return client.messages.create(
            src=src,
            dst=dst,
            type_="whatsapp",
            media_urls=[media_url],
            text=caption or None,
        )

    try:
        resp = await asyncio.to_thread(_send)
    except Exception as e:  # noqa: BLE001
        log.warning("[whatsapp] Plivo media send failed", to=_redact(dst), err=str(e)[:200])
        return {"error": f"provider error: {str(e)[:200]}"}

    message_uuid = _message_uuid(resp)
    log.info("[whatsapp] media sent", to=_redact(dst), message_uuid=message_uuid)
    return {"message_uuid": message_uuid}


async def send_template(
    *,
    to: str,
    template_name: str,
    language: str = "en_US",
    body_params: list[str] | None = None,
    header_doc_url: str | None = None,
    header_doc_filename: str | None = None,
) -> dict[str, Any]:
    """Send an approved WhatsApp TEMPLATE via Plivo — the ONLY way to initiate
    outside the 24-hour window. Optionally carries a media header (e.g. the
    brochure PDF link) and positional body params ({{1}}, {{2}}, …).

    `template_name`/`language` must match an APPROVED Meta template; an
    unapproved/empty name fails (logged), it doesn't crash. A media-header link
    must be a public HTTPS URL pointing at the actual FILE (a web page won't
    pass Meta's media fetch). ALWAYS returns a dict (see `send_text`):
    `{"message_uuid": ...}` on success, `{"error": ...}` on failure.

    Plivo's template payload uses `{"type": "media", "media": <url>}` for the
    header media parameter (document/image/video alike) — NOT Meta's raw
    `{"type": "document", "document": {...}}` shape.
    """
    s = get_settings()
    if not s.plivo_whatsapp_from:
        log.warning("[whatsapp] PLIVO_WHATSAPP_FROM not set — template send disabled")
        return {"error": "outbound disabled: PLIVO_WHATSAPP_FROM not set"}
    if not template_name:
        log.warning("[whatsapp] no template name — cannot send out-of-window")
        return {"error": "no template name configured"}
    client = _get_client()
    if client is None:
        return {"error": "outbound disabled: Plivo client unavailable (check PLIVO_AUTH_ID/TOKEN + SDK)"}

    dst, src = _digits(to), _digits(s.plivo_whatsapp_from)
    if not dst:
        return {"error": "no recipient"}

    components: list[dict[str, Any]] = []
    if header_doc_url:
        # Plivo media-header format (document/image/video): a single `media`
        # parameter carrying the public file URL. `header_doc_filename` has no
        # slot in Plivo's shape, so it's intentionally unused here.
        components.append(
            {
                "type": "header",
                "parameters": [{"type": "media", "media": header_doc_url}],
            }
        )
    if body_params:
        components.append(
            {
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)} for p in body_params],
            }
        )

    # The plivo SDK strictly validates `template`: the top level must be a
    # plivo.utils.template.Template (a raw dict is rejected with "template
    # should be of type: ['plivo.utils.template.Template']"), while the
    # components must stay PLAIN DICTS — the SDK validates dict items by
    # constructing Component(**item) but keeps the dict, and Component/Parameter
    # instances would later break json serialization (`template.__dict__` is
    # shallow). Safe to import here: `client` being non-None above proves the
    # plivo package is installed.
    from plivo.utils.template import Template

    template = Template(name=template_name, language=language, components=components)

    def _send() -> Any:
        return client.messages.create(src=src, dst=dst, type_="whatsapp", template=template)

    try:
        resp = await asyncio.to_thread(_send)
    except Exception as e:  # noqa: BLE001
        log.warning("[whatsapp] Plivo template send failed", to=_redact(dst), template=template_name, err=str(e)[:200])
        return {"error": f"provider error: {str(e)[:200]}"}

    message_uuid = _message_uuid(resp)
    log.info("[whatsapp] template sent", to=_redact(dst), template=template_name, message_uuid=message_uuid)
    return {"message_uuid": message_uuid}


def sent_ok(result: dict[str, Any] | None) -> bool:
    """True iff a send_* result represents a delivered message (has a
    message_uuid). Failures carry an `error` key instead."""
    return bool(result and result.get("message_uuid"))


def _message_uuid(resp: Any) -> str | None:
    """Plivo's create() returns an object/dict whose `message_uuid` is usually a
    list. Normalise to a single string for logging/return."""
    if resp is None:
        return None
    val = resp.get("message_uuid") if isinstance(resp, dict) else getattr(resp, "message_uuid", None)
    if isinstance(val, (list, tuple)):
        return str(val[0]) if val else None
    return str(val) if val else None


def _redact(num: str) -> str:
    """Mask middle digits — phone numbers are PII."""
    if len(num) <= 6:
        return "***"
    return f"{num[:3]}***{num[-3:]}"
