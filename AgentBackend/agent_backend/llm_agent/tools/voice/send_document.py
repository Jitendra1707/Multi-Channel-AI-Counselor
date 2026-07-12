"""send_document — share a document with the candidate on WhatsApp, live.

Scenario 2: the candidate is chatting on WhatsApp and asks for a document
("can you send me the fee details?"). The brain calls this tool with the
matching document key and we send it immediately (the 24h window is open by
definition — they just messaged us), then tell the brain it's on its way.

Scoped to WhatsApp ONLY. On voice the request is instead captured and the
post-call analyzer delivers it (Scenario 1, free-form or template by window) —
so this factory returns NOTHING for non-WhatsApp channels. (It lives under the
`voice` tool group because WhatsApp shares that counselor group; the channel
gate below is what restricts it.)

Link-based: sends the catalog's public document URL as a tappable text link
(not an attached file). On success it best-effort records the delivery in the
BusinessLayer so the next conversation follows up instead of re-offering.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from agent_backend.channels.whatsapp.client import send_text, sent_ok
from agent_backend.data.documents import get_document, list_documents
from agent_backend.infra import get_logger
from agent_backend.llm_agent.tools._base import ToolContext

log = get_logger(__name__)


def build_tools(ctx: ToolContext) -> list[BaseTool]:
    session = ctx.session
    # Only on WhatsApp (a live, in-window chat). Voice defers to the analyzer.
    if session.channel != "whatsapp":
        return []
    catalog = list_documents()
    if not catalog:
        return []  # nothing to send → don't offer the tool

    @tool
    async def send_document(document_key: str) -> str:
        """Send a document to the candidate on WhatsApp right now.

        Use this the moment the candidate asks for a document (brochure, fee/
        scholarship details, application guide, etc.). Pick the closest matching
        document key from the list below. After it sends, briefly tell the
        candidate it's on its way — don't paste the link yourself.

        Args:
            document_key: the catalog key of the document to send.
        """
        doc = get_document(document_key)
        if doc is None:
            avail = ", ".join(d["key"] for d in list_documents())
            return f"No document matches '{document_key}'. Available keys: {avail}."

        url = doc.get("url")
        title = doc.get("title", document_key)
        to = session.conversation_id  # the WhatsApp sender id we received from

        if not url:
            return f"The {title} has no link configured yet — tell the candidate you'll follow up with it."
        # Live chat = window open → send the link as a tappable text message.
        result = await send_text(to=to, body=f"Here's the {title} you asked for:\n{url}")
        if not sent_ok(result):
            log.warning("[send_document] send failed", session=session.short(),
                        doc=document_key, reason=result.get("error"))
            return f"I couldn't send the {title} just now — tell the candidate you'll follow up with it."

        # Best-effort: record the delivery so a later call doesn't re-offer it.
        if session.lead_id:
            try:
                from agent_backend.integrations import business as _biz

                await _biz.record_delivery(lead_id=session.lead_id, item=title, channel="whatsapp")
            except Exception as e:  # noqa: BLE001
                log.debug("[send_document] record_delivery failed", err=str(e)[:160])

        log.info("[send_document] sent", session=session.short(), doc=document_key, title=title)
        return f"Sent the {title} to the candidate on WhatsApp — let them know it's on its way."

    # Make the model aware of what it can send (dynamic catalog → description).
    send_document.description += "\n\nAvailable documents (use the key):\n" + "\n".join(
        f"  - {d['key']}: {d['title']} — {d['description']}" for d in catalog
    )
    return [send_document]
