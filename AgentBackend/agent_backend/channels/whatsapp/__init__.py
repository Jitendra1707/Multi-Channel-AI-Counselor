"""WhatsApp channel — Plivo WhatsApp Business API → shared counselor brain.

Same brain, tools, and persona as the PSTN voice channel — different transport.
Plivo sits in front of Meta's WhatsApp Business Platform.

Inbound: Plivo POSTs each message to `POST /channels/whatsapp/inbound` (set as
the number's Message URL in the Plivo console). The webhook acks 200 immediately
and processes in a background task, so a slow LLM turn never triggers Plivo's
retry-on-timeout.

WhatsApp is a member of `session.VOICE_FAMILY`, so inbound text routes through
the SAME counselor brain (`llm_agent.run_stream`) as PSTN voice — only the OUTPUT
STYLE prompt block differs. Leads are resolved by phone number, so conversation
memory + lead profile carry across channels for the same candidate.

Outbound sends (replies + BusinessLayer follow-ups via `POST /api/whatsapp/send`)
go through Plivo's Messages API (`type="whatsapp"`). Reuses PLIVO_AUTH_ID /
PLIVO_AUTH_TOKEN; sender is PLIVO_WHATSAPP_FROM.
"""

from agent_backend.channels.whatsapp.routes import router

__all__ = ["router"]
