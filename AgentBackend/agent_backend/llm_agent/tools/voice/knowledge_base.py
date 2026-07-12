"""search_knowledge_base — on-demand RAG over the university knowledge base.

Agentic RAG (LangGraph standard): instead of injecting retrieved snippets into
EVERY turn's prompt (which pays an embed + retrieval round-trip even on
"hi"/"yes"/"okay"), the COUNSELLOR brain calls this tool only when the candidate
actually asks for facts — fees, eligibility, cutoffs, placements, programs,
deadlines, scholarships, figures. Conversational turns cost nothing.

Routes through `agent_backend.rag_router`, so it honours the RAG_BACKEND switch
(legacy float32 vs TurboQuant) and emits `[RAG]` logs.

Offered to the counsellor channels (voice / whatsapp / chat) AND to the
avatar_video channel: the avatar is a HYBRID (counsellor + director presenter),
so it answers general university questions from the knowledge base as well as
briefing on analytics. It is offered to avatar_video as a cross-group shared
tool (see tools/__init__.py `_SHARED_TOOLS`).
"""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from agent_backend.infra import get_logger
from agent_backend.llm_agent.tools._base import ToolContext

log = get_logger(__name__)

# Channels that may look up university facts. Includes avatar_video — the avatar
# is a hybrid that answers general university questions, not only analytics — and
# `meeting`, where the in-meeting expert (Aria) is asked for exact fees / cutoffs
# / placements and must ground them, not guess.
_COUNSELLOR_CHANNELS = {"voice", "whatsapp", "avatar_video", "meeting"}
# Channels where a brief spoken "one moment" filler before retrieval is good UX.
_SPOKEN_CHANNELS = {"voice"}


def build_tools(ctx: ToolContext) -> list[BaseTool]:
    session = ctx.session
    if session.channel not in _COUNSELLOR_CHANNELS:
        return []

    spoken = session.channel in _SPOKEN_CHANNELS

    @tool
    async def search_knowledge_base(query: str) -> str:
        """Look up authoritative university facts to answer the candidate.

        Call this WHENEVER the candidate asks anything factual you're not already
        certain of: fees, scholarships, eligibility, EAPCET/SUCET cutoffs,
        programs and specializations, placements/recruiters, certifications (e.g.
        SAP), application steps, deadlines, or any exact figure or date. Do NOT
        call it for greetings, small talk, confirmations, or things already
        answered this turn.

        Pass a focused natural-language query. You'll get the most relevant
        knowledge-base passages — answer directly from them, reading exact values
        out of any tables. If the figure isn't in the results, say you'll confirm
        and follow up; never guess fees/dates.

        Args:
            query: what to look up, in natural language.
        """
        import asyncio

        from agent_backend.rag_router import engine_label, get_retriever

        q = (query or "").strip()
        if not q:
            return "No query provided — ask the candidate to clarify what they'd like to know."

        # LATENCY MASK: speak a short, topic-matched static filler ("let me check
        # the fee details…") the INSTANT retrieval starts, so the caller hears a
        # human line instead of dead air during the embed + Qdrant + LLM round
        # trip. Static (zero LLM cost); only fires on spoken channels where a
        # speaker is registered. The bridge queues the real answer behind it so
        # they never overlap. Best-effort — no-op if no speaker.
        if spoken:
            try:
                from agent_backend.llm_agent.filler_speaker import speak_filler

                speak_filler(session.conversation_id, q)
            except Exception:  # noqa: BLE001
                pass

        try:
            hits = await asyncio.to_thread(get_retriever().search_text, q, 4)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "[RAG] tool retrieval failed",
                rag=engine_label(), query=q[:120], err=str(e)[:160],
            )
            return (
                "I couldn't reach the knowledge base just now — tell the candidate "
                "you'll confirm the detail and follow up shortly."
            )

        headings = [
            h.split("]", 1)[0].lstrip("[") if h.startswith("[") else h[:60]
            for h in hits
        ]
        log.info(
            "[RAG] tool query",
            rag=engine_label(), channel=session.channel,
            query=q[:120], hits=len(hits), matched=headings,
        )
        if not hits:
            return (
                f"Nothing in the knowledge base matched '{q}'. Tell the candidate "
                "you'll confirm that detail and follow up — don't guess."
            )
        body = "\n".join(f"  - {h}" for h in hits)
        return (
            "KNOWLEDGE BASE RESULTS (answer the candidate directly from these, "
            "including exact values from any tables; if the specific figure isn't "
            "here, say you'll confirm and follow up — don't guess).\n"
            "Speak these as your OWN knowledge, the way a counsellor who simply "
            "knows them would. State each fact as a plain, direct statement — "
            "NEVER attribute it to a source or frame it as something that 'says / "
            "mentions / shows / states / lists' it. Do not say 'brochure', "
            "'document', 'knowledge base', 'the university (also) says', 'they "
            "say', 'it says', 'the data shows', 'as per/according to', or 'as "
            "mentioned/shown/listed in...'. If the text below uses such words, "
            "DROP them and just give the fact:\n" + body
        )

    if spoken:
        search_knowledge_base.description += (
            "\n\nVOICE: do NOT speak or write ANY text before calling this — just "
            "call the tool directly with no message content. A short spoken filler "
            "is played automatically while it runs, so any 'let me check' / 'give "
            "me a moment' line you write would be spoken ON TOP of it (duplicate). "
            "Call the tool silently; speak only the answer AFTER it returns."
        )

    return [search_knowledge_base]
