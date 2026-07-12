"""Outbound WhatsApp send endpoint — POST /api/whatsapp/send.

Single seamless entry point the BusinessLayer action/outbox worker calls to
deliver follow-ups (e.g. "send the programme brochure"). It encapsulates ALL
WhatsApp policy so upstream stays transport-agnostic:

  1. Resolve the document: a `doc_key` (exact or free text like "fee details")
     is matched against the catalog (data.documents) → title + public URL +
     approved template. Or pass `media_url`(=url)/`body` directly.
  2. Pick the mode by the 24-hour window:
       • window OPEN  → free-form text containing the link.
       • window CLOSED→ the document's registered template (general_document_
         template: a DOCUMENT header carrying the link + body {{1}} name,
         {{2}} link) — the only way to initiate out of window.
  3. Safety net: if the in-window text send fails, fall through to the template.

To send several documents, call this once per document (the action worker emits
one task per doc; the live tool calls once per doc).

Recipient: explicit `to_phone`, else resolved from `lead_id` via LeadRepo. The
Plivo client normalises the number itself.
"""

from __future__ import annotations

import threading
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent_backend.channels.whatsapp.client import send_template, send_text, sent_ok
from agent_backend.channels.whatsapp.window import window_open
from agent_backend.config import get_settings
from agent_backend.data import LeadRepo
from agent_backend.data.documents import get_document
from agent_backend.data.templates import get_template, resolve_language
from agent_backend.infra import get_logger

log = get_logger(__name__)

send_router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp-send"])


class SendRequest(BaseModel):
    body: str | None = Field(default=None, description="Message text / caption.")
    to_phone: str | None = Field(default=None, description="E.164 (with or without '+').")
    lead_id: str | None = Field(default=None, description="Resolve phone from this lead if to_phone absent.")
    media_url: str | None = Field(default=None, description="Explicit public HTTPS media/document URL.")
    doc_key: str | None = Field(default=None, description="Catalog key OR free text (e.g. 'fee details') → resolved to a document.")
    template_key: str | None = Field(
        default=None,
        description="Send this registered approved template (standalone, no document needed) — e.g. 'outreach' to reach a candidate who missed a call or to start a cold WhatsApp thread.",
    )
    template_params: dict[str, str] | None = Field(
        default=None,
        description="Named body-param values for template_key (keys match the template's `params`). A missing 'first_name' is auto-filled from the lead.",
    )


def _first_name(lead) -> str:  # noqa: ANN001
    if lead and lead.full_name and lead.full_name != "Unknown":
        return lead.full_name.split()[0]
    return "there"


def _build_template_params(tmpl: dict, lead, provided: dict | None) -> list[str]:  # noqa: ANN001
    """Order the template's body values to match its declared `params`. Pull each
    field from `provided`; auto-fill 'first_name' from the lead; warn (and emit an
    empty string, which Meta rejects loudly rather than us crashing) for any other
    field left unfilled."""
    provided = provided or {}
    out: list[str] = []
    for field in tmpl.get("params") or []:
        if provided.get(field) is not None:
            out.append(str(provided[field]))
        elif field == "first_name":
            out.append(_first_name(lead))
        else:
            log.warning(
                "[whatsapp] template param not supplied — sending empty",
                template=tmpl.get("name"), param=field,
            )
            out.append("")
    return out


async def _send_registered_template(
    *, key: str, phone: str, lead, provided_params: dict | None,  # noqa: ANN001
    header_doc_url: str | None = None, header_doc_filename: str | None = None,
) -> tuple[dict, str]:
    """Resolve a template_key in the registry and send it. Always returns
    (result, via): result is `{"message_uuid": ...}` on success or
    `{"error": ...}` on failure (unknown key, unconfigured name, or provider
    error) — use sent_ok(result) to test.

    If the template declares a media header ("document"/"image") and no explicit
    header_doc_url was passed, the 'url' param value doubles as the header
    attachment — matching general_document_template, where the same link is both
    the document header and body {{2}}."""
    tmpl = get_template(key)
    if not tmpl:
        log.warning("[whatsapp] unknown template_key — not sent", template_key=key)
        return {"error": f"unknown template_key {key!r}"}, "template"
    name = str(tmpl.get("name") or "")
    if not name or name.startswith("REPLACE_WITH"):
        log.warning(
            "[whatsapp] template has no approved name configured — not sent",
            template_key=key, name=name,
        )
        return {"error": f"template {key!r} has no approved name configured"}, "template"
    lang = resolve_language(tmpl, getattr(lead, "language_preference", None))
    params = _build_template_params(tmpl, lead, provided_params)
    if tmpl.get("header") in ("document", "image") and not header_doc_url:
        header_doc_url = (provided_params or {}).get("url")
    result = await send_template(
        to=phone, template_name=name, language=lang, body_params=params,
        header_doc_url=header_doc_url, header_doc_filename=header_doc_filename,
    )
    return result, "template"


# ---------------------------------------------------------------------------
# Missed-call outreach — fired by the voice hangup callback when an OUTBOUND
# call ends unanswered (busy / rejected / no-answer). Sends the registered
# `outreach` template (approved templates initiate in OR out of the 24h
# window). Fully self-contained and best-effort: it never raises into the
# caller, so the hangup callback's pipeline-teardown path is unaffected.
# ---------------------------------------------------------------------------
_outreach_lock = threading.Lock()
_last_outreach_at: dict[str, float] = {}  # digits-only phone → epoch seconds


def _digits(number: str) -> str:
    return (number or "").strip().lstrip("+").replace(" ", "").replace("-", "")


def _mask(num: str) -> str:
    return f"{num[:3]}***{num[-3:]}" if len(num) > 6 else "***"


def _outreach_cooldown_ok(digits: str, cooldown_minutes: int) -> bool:
    """True if no outreach went to this number within the cooldown. In-memory
    (per process): worst case after a restart is one extra message."""
    with _outreach_lock:
        last = _last_outreach_at.get(digits)
        return last is None or (time.time() - last) >= cooldown_minutes * 60


def _mark_outreach(digits: str) -> None:
    with _outreach_lock:
        _last_outreach_at[digits] = time.time()


async def send_missed_call_outreach(*, phone: str, cause: str = "") -> bool:
    """Send the missed-call `outreach` template to `phone`. Returns True if a
    message was sent. Skips (False) when: disabled, cooldown active, the number
    is unknown to the BusinessLayer, or the lead has no WhatsApp consent."""
    try:
        s = get_settings()
        if not s.missed_call_outreach_enabled:
            return False
        digits = _digits(phone)
        if not digits:
            return False
        if not _outreach_cooldown_ok(digits, s.missed_call_outreach_cooldown_minutes):
            log.info("[whatsapp] missed-call outreach skipped (cooldown)", to=_mask(digits))
            return False

        # Consent + identity from the system of record. Unknown number / no
        # consent / BusinessLayer down → don't message.
        from agent_backend.integrations import business as _biz

        biz = await _biz.find_lead_by_phone(phone)
        if not biz:
            log.info("[whatsapp] missed-call outreach skipped (unknown number)", to=_mask(digits))
            return False

        # Retry the CALL regardless of WhatsApp consent/result: a missed call
        # puts the lead back in the dial queue (status FOLLOWUP +
        # next_action_at) so the dialer re-attempts, by default next day.
        try:
            await _biz.schedule_followup(
                lead_id=str(biz["lead_id"]), in_minutes=s.missed_call_retry_minutes
            )
            log.info(
                "[voice] missed-call retry scheduled",
                lead_id=biz.get("lead_id"), in_minutes=s.missed_call_retry_minutes,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("[voice] missed-call retry scheduling failed", err=str(e)[:160])

        if not biz.get("consent_whatsapp"):
            log.info(
                "[whatsapp] missed-call outreach skipped (no whatsapp consent)",
                lead_id=biz.get("lead_id"),
            )
            return False

        full_name = (biz.get("full_name") or "").strip()
        first_name = full_name.split()[0] if full_name and full_name.lower() != "unknown" else "there"
        # {{2}} of general_template — the approved body already closes with
        # "just reply to this message and we'll be happy to help", so this only
        # needs the context line.
        message = (
            "We just tried calling you about your admission enquiry but "
            "couldn't reach you. Do let us know a good time to call back."
        )
        result, _via = await _send_registered_template(
            key=s.missed_call_outreach_template_key,
            phone=phone,
            lead=None,  # name passed explicitly below — biz is a dict, not a Lead
            provided_params={"first_name": first_name, "message": message},
        )
        if sent_ok(result):
            _mark_outreach(digits)
            log.info(
                "[whatsapp] missed-call outreach sent",
                lead_id=biz.get("lead_id"), cause=cause,
                message_uuid=result.get("message_uuid"),
            )
            return True
        log.warning(
            "[whatsapp] missed-call outreach failed",
            lead_id=biz.get("lead_id"), cause=cause, reason=result.get("error"),
        )
        return False
    except Exception as e:  # noqa: BLE001
        # Never let outreach problems propagate into the hangup callback.
        log.warning("[whatsapp] missed-call outreach crashed", err=str(e)[:200])
        return False


@send_router.post("/send")
async def send(body: SendRequest) -> dict:
    # Entry marker — if this line is ABSENT from the AgentBackend log when a send
    # is attempted, the running process is NOT this code (stale/duplicate).
    log.info("[whatsapp] /send received", lead_id=body.lead_id, to_phone=body.to_phone,
             doc_key=body.doc_key, template_key=body.template_key)
    lead = None
    phone = body.to_phone
    if body.lead_id:
        lead = LeadRepo.get().get_by_id(body.lead_id)
        if lead is None and not phone:
            raise HTTPException(404, f"unknown lead_id={body.lead_id!r}")
        if not phone and lead is not None:
            phone = lead.phone_e164
    if not phone:
        raise HTTPException(400, "provide to_phone or a lead_id with a phone on file")

    # Explicit template send (standalone outreach / missed-call / admission next
    # steps) — an approved template can be sent in OR out of the 24h window, so
    # this is the reliable way to (re-)initiate contact.
    if body.template_key:
        result, via = await _send_registered_template(
            key=body.template_key, phone=phone, lead=lead, provided_params=body.template_params,
        )
        if sent_ok(result):
            log.info("[whatsapp] sent via /send", lead_id=body.lead_id, via=via,
                     template_key=body.template_key, message_uuid=result.get("message_uuid"))
            return {"ok": True, "via": via, "message_uuid": result.get("message_uuid")}

        # Template unavailable (e.g. name not approved yet) — if a free-form
        # `body` was supplied AND the 24h window is open, fall back to it so the
        # message still reaches the candidate. This is what lets the admission
        # next-steps message work before its templates are Meta-approved.
        reason = result.get("error") or "unknown error"
        fallback_text = (body.body or "").strip()
        if fallback_text and await window_open(phone):
            ft = await send_text(to=phone, body=fallback_text)
            if sent_ok(ft):
                log.info("[whatsapp] template unavailable — sent free-form fallback",
                         lead_id=body.lead_id, template_key=body.template_key, reason=reason)
                return {"ok": True, "via": "text", "message_uuid": ft.get("message_uuid")}
            reason = f"{reason}; fallback text: {ft.get('error')}"
        log.warning("[whatsapp] template send failed", lead_id=body.lead_id,
                    template_key=body.template_key, reason=reason)
        raise HTTPException(502, f"whatsapp template send failed ({reason})")

    # Resolve the document (catalog key or free text) → url/title/template.
    doc = get_document(body.doc_key) if body.doc_key else None
    url = body.media_url or (doc.get("url") if doc else None)
    title = (doc.get("title") if doc else None) or "the details"
    # In-window message text: a short intro line + the (tappable) link.
    intro = body.body or (f"As requested, here is the {title}:" if doc else None)
    text_body = (f"{intro}\n{url}" if url else intro) or ""

    open_window = await window_open(phone)
    result: dict = {}
    via: str | None = None
    errors: list[str] = []  # accumulate per-attempt reasons for a precise 502

    # In-window → free-form text containing the link.
    if open_window and text_body.strip():
        result, via = await send_text(to=phone, body=text_body), "text"
        if not sent_ok(result):
            errors.append(f"text: {result.get('error')}")

    # Window closed (or the in-window text send failed) → approved template. For
    # general_document_template the link is both the media header attachment and
    # body {{2}}. The document references its template by key; the registry holds
    # the approved name + per-language code.
    if not sent_ok(result) and url:
        tkey = (doc or {}).get("template_key")
        if tkey:
            result, via = await _send_registered_template(
                key=tkey, phone=phone, lead=lead, provided_params={"url": url},
            )
            if not sent_ok(result):
                errors.append(f"template[{tkey}]: {result.get('error')}")
        else:
            errors.append("document has no template_key for out-of-window send")

    if not sent_ok(result):
        # Build a SPECIFIC reason so the action worker's last_error is diagnosable
        # (not the old catch-all). Surface 502 so the worker retries with backoff.
        reason = "; ".join(e for e in errors if e) or (
            "window closed and no document url/template to fall back to"
        )
        log.warning("[whatsapp] send failed", lead_id=body.lead_id, doc_key=body.doc_key,
                    window_open=open_window, reason=reason)
        raise HTTPException(502, f"whatsapp send failed ({reason})")

    log.info("[whatsapp] sent via /send", lead_id=body.lead_id, doc_key=body.doc_key,
             via=via, window_open=open_window, message_uuid=result.get("message_uuid"))
    return {"ok": True, "via": via, "message_uuid": result.get("message_uuid")}
