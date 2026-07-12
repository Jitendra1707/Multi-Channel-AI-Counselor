"""Plivo inbound WhatsApp → counselor brain → Plivo outbound reply.

Resolves the candidate's Lead by phone (so the brain renders LEAD PROFILE +
conversation memory exactly like inbound PSTN voice), routes the text through
`llm_agent.run_stream(channel="whatsapp", ...)`, mirrors both sides to the
episodic store, pushes session lifecycle to the BusinessLayer, and sends the
reply back through Plivo.

Channel-agnostic from the brain's perspective — only the transport (Plivo) and
the inbound parsing (in routes.py) are WhatsApp/Plivo-specific.
"""

from __future__ import annotations

from agent_backend.channels.whatsapp.client import send_text
from agent_backend.channels.whatsapp.window import mark_inbound
from agent_backend.data import LeadRepo
from agent_backend.infra import get_logger
from agent_backend.llm_agent import Session, run_stream
from agent_backend.llm_agent.memory import get_episodic_store
from agent_backend.llm_agent.memory.episodic import make_conversation_record

log = get_logger(__name__)


async def handle_inbound(*, from_number: str, text: str) -> None:
    """Process one inbound WhatsApp text and reply. Dedup + filtering happen in
    routes.py; this assumes a real text turn from `from_number`."""
    text = (text or "").strip()
    if not text:
        return

    # Opens/refreshes the candidate's 24h free-form window (used by the send path
    # to choose free-form vs template).
    mark_inbound(from_number)

    # Resolve the candidate by phone — BusinessLayer FIRST (system of record),
    # else a local in-memory lead. This recognises known candidates even after
    # leads.json is removed; `ensure_lead_by_phone` also hydrates facts/summary.
    from agent_backend.integrations import business as _biz

    lead = await _biz.ensure_lead_by_phone(from_number, source="inbound_whatsapp")

    # conversation_id keys the brain's conversation memory + graph cache. Use the
    # sender number so every WhatsApp message from this person threads into one
    # continuous conversation (stable per person; no call_id on this channel).
    session = Session(
        channel="whatsapp",
        conversation_id=from_number or lead.lead_id,
        lead_id=lead.lead_id,
        lead_status=lead.status.value,
        language=lead.language_preference,
        display_name=lead.full_name if lead.full_name != "Unknown" else None,
    )

    log.info("[whatsapp] >>> USER", session=session.short(), from_=_redact(from_number), text=text)

    # BusinessLayer: open/continue the session + record the user turn. Memory was
    # already hydrated in ensure_lead_by_phone. Best-effort — never blocks reply.
    try:
        await _biz.open_session(session, direction="inbound", contact_phone=lead.phone_e164)
        await _biz.append_turn(session, role="user", text=text)
    except Exception as e:  # noqa: BLE001
        log.debug("[whatsapp] business pre-turn hook failed (continuing)", err=str(e))

    # Episodic timeline (separate from the brain's own conversation memory).
    try:
        get_episodic_store(session.conversation_id).append(
            make_conversation_record(source="user", content=text, channel="whatsapp")
        )
    except Exception as e:  # noqa: BLE001
        log.warning("[whatsapp] failed to capture user turn", session=session.short(), err=str(e))

    # Run the brain. WhatsApp is non-streaming on the user side — accumulate
    # tokens then send the full reply in one message.
    bot_chunks: list[str] = []
    try:
        async for token in run_stream(text, channel="whatsapp", session=session):
            bot_chunks.append(token)
    except Exception as e:  # noqa: BLE001
        log.warning("[whatsapp] run_stream failed", session=session.short(), err=str(e))
        await send_text(to=from_number, body="Sorry, I had trouble reaching the model. Please try again.")
        return

    reply = "".join(bot_chunks).strip() or "Done."
    log.info("[whatsapp] <<< BOT", session=session.short(), tokens=len(bot_chunks), text=reply)

    try:
        get_episodic_store(session.conversation_id).append(
            make_conversation_record(source="bot", content=reply, channel="whatsapp")
        )
    except Exception as e:  # noqa: BLE001
        log.debug("[whatsapp] failed to capture bot turn", session=session.short(), err=str(e))

    try:
        await _biz.append_turn(session, role="bot", text=reply)
    except Exception as e:  # noqa: BLE001
        log.debug("[whatsapp] business bot-turn hook failed", session=session.short(), err=str(e))

    await send_text(to=from_number, body=reply)


def _redact(num: str) -> str:
    if len(num) <= 6:
        return "***"
    return f"{num[:3]}***{num[-3:]}"
