"""The agent — LangGraph ReAct, one entry point, two product families.

PUBLIC contract (stable across phases):

    async def run_stream(text, *, channel, session) -> AsyncIterator[str]:
        ...

ONE brain, every channel (voice / whatsapp / email / avatar_video). The brain is
identical across channels — same LLM (`get_llm()`), same persona-driven prompt
assembly, same conversation memory. The use case is tuned by the PERSONA
(identity JSON: role, objectives, guardrails, grounding), NOT by a code branch.

Per-turn the prompt fills only the slots that apply: PERSONA always; PLAYBOOK +
KNOWLEDGE (RAG) when relevant; LEAD PROFILE when the session has a lead. The tool
surface is channel-scoped inside `tools.build_all_tools(session)` (counsellor
tools vs the avatar_video director tools), and `open_call()` provides the
bot-speaks-first opener on voice.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import (
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.tools import BaseTool
from langgraph.prebuilt import create_react_agent

from agent_backend.config import get_settings
from agent_backend.infra import get_logger, trace_config
from agent_backend.llm_agent.identity import get_identity, render_identity_block
from agent_backend.llm_agent.llm import get_llm
from agent_backend.llm_agent.prompts import build_system_prompt
from agent_backend.llm_agent.session import Session

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Graph cache — keyed by conversation_id (both families). Tools close over the
# session at build time (e.g. voice `end_call` over `call_id`), so the key must
# be per-call; conversation_id is exactly that. Cross-call MEMORY lives
# separately in ConversationStore, so rebuilding the graph per call is cheap.
# ---------------------------------------------------------------------------
_GRAPH_CACHE: dict[str, tuple[Any, float]] = {}
_GRAPH_TTL_S = 3600.0


def _build_tools(session: Session) -> list[BaseTool]:
    """Per-turn tool list. Auto-discovery walks `tools/**/*.py` under a fresh
    `ToolContext`, scoped to the session's channel family. One file per tool —
    no changes here to add one."""
    from agent_backend.llm_agent.tools import build_all_tools

    return build_all_tools(session)


def _get_graph(session: Session) -> Any:
    """Return a cached ReAct graph for this session, building one if needed.

    All channels share one LLM (`get_llm()`); only the prompt, tools, and
    memory differ by family. The cache key is `conversation_id`, unique per
    call, so sessions never collide on a shared entry."""
    now = time.monotonic()
    cached = _GRAPH_CACHE.get(session.conversation_id)
    if cached is not None and (now - cached[1]) < _GRAPH_TTL_S:
        _GRAPH_CACHE[session.conversation_id] = (cached[0], now)
        return cached[0]

    llm = get_llm()
    tools = _build_tools(session)
    graph = create_react_agent(model=llm, tools=tools)
    _GRAPH_CACHE[session.conversation_id] = (graph, now)
    log.info(
        "[agent] LangGraph ReAct agent built",
        session=session.short(),
        tools=len(tools),
    )
    # Evict idle entries to keep the cache bounded over long uptimes.
    for k, (_, ts) in list(_GRAPH_CACHE.items()):
        if (now - ts) >= _GRAPH_TTL_S:
            _GRAPH_CACHE.pop(k, None)
    return graph


def prewarm(session: Session) -> None:
    """Eagerly build + cache this session's ReAct graph before the first turn.

    The graph (LLM client + tool discovery + create_react_agent) is otherwise
    built lazily inside the first `run_stream`, adding ~3-4 s to the candidate's
    very first reply. Calling this at session start — ideally via
    `asyncio.to_thread`, since the build is synchronous/CPU — lets the first turn
    find the graph already cached. Idempotent (no-op if already cached) and
    self-contained (never raises; on failure the first turn just builds lazily)."""
    try:
        _get_graph(session)
    except Exception as e:  # noqa: BLE001
        log.debug("[agent] prewarm failed (will build lazily on first turn)", err=str(e)[:160])


def warmup_llm_graph() -> None:
    """Pre-pay the COLD, process-global cost of the LangGraph stack at app startup.

    The first `_get_graph` after a server start pays one-time costs that block the
    event loop for SECONDS: importing langgraph/langchain (done by importing this
    module) plus the first `create_react_agent(...)` compile. Because that work is
    CPU/GIL-bound, `asyncio.to_thread` does NOT free the loop for it — so doing it
    per-session at connect starves the SoulX handshake (tried and reverted). Doing
    it ONCE at boot, before any WebRTC peer exists, pays the cost when there is
    nothing to starve; the first live call's per-session build is then just the
    (warm) graph compile (~1-2s) instead of ~14s.

    Builds a throwaway session-less graph (empty tools) purely to JIT the compile
    path; the real per-session graph is still built lazily/cached in `_get_graph`.
    Idempotent-ish and self-contained (never raises; lazy build remains the fallback).
    """
    try:
        from langchain_core.tools import tool

        # Warm with a REPRESENTATIVE (non-empty) tool list. create_react_agent compiles a
        # different graph shape when tools are present (it wires a tools node + conditional
        # edges); warming with tools=[] skips that path, leaving the per-session build to pay
        # it on the first turn. One dummy tool warms the with-tools compile so the real
        # per-session build (which always has tools) is fast.
        @tool
        def _warmup_noop(query: str) -> str:
            """Warmup placeholder — never invoked."""
            return query

        t0 = time.monotonic()
        create_react_agent(model=get_llm(), tools=[_warmup_noop])
        log.info("[agent] LLM graph warmup done", secs=round(time.monotonic() - t0, 1))
    except Exception as e:  # noqa: BLE001
        log.debug("[agent] LLM graph warmup failed (will build lazily on first turn)", err=str(e)[:160])


# ---------------------------------------------------------------------------
# Prompt assembly (single, persona-driven, channel-agnostic)
# ---------------------------------------------------------------------------

def _persona_name_for_channel(channel: str) -> str:
    """Which persona drives this channel.

    The avatar_video channel is the director-briefing analytics presenter; every
    other channel (voice / whatsapp / email) is the counsellor. Resolved here so
    the rest of the brain stays channel-agnostic."""
    s = get_settings()
    return s.avatar_identity_name if channel == "avatar_video" else s.identity_name


def _render_persona(channel: str | None = None) -> str | None:
    """The persona block for this channel's brain. avatar_video → director
    presenter; all other channels → the counsellor (`identity_name`)."""
    try:
        name = _persona_name_for_channel(channel) if channel else None
        return render_identity_block(get_identity(name))
    except Exception as e:  # noqa: BLE001
        log.warning("[agent] persona load failed", err=str(e), channel=channel)
        return None


def _render_university() -> str | None:
    """Always-on UNIVERSITY core block — the 'overview' chunks of the Qdrant
    university collection (TTL-cached in the retriever, so this is in-memory on
    the hot path). Returns None if the collection is unreachable/not ingested;
    the prompt then simply omits the core block and the brain still has the
    on-demand RAG slot. Ingestion lives in the standalone script."""
    try:
        from agent_backend.rag_router import core_context, engine_label

        block = core_context()
        log.info("[RAG] core_context", rag=engine_label(), loaded=bool(block))
        return block
    except Exception as e:  # noqa: BLE001
        log.warning(
            "[agent] KB core context unavailable — UNIVERSITY block omitted "
            "(check Qdrant / RAG_QDRANT_COLLECTION ingestion)",
            err=str(e),
        )
        return None


def _render_lead_profile(lead: Any, direction: str = "outbound") -> str:
    """LEAD PROFILE slot + TWO opening hints: the status hint (operational/quality
    axis) and the funnel-stage hint (admissions lifecycle axis). Both are shown so
    the brain blends them — e.g. status 'called' (you've spoken before) + funnel
    'fees_pending' (guide the payment)."""
    from agent_backend.llm_agent.prompts.funnel_playbook import funnel_hint
    from agent_backend.llm_agent.prompts.status_playbook import opening_hint

    lines: list[str] = ["LEAD PROFILE"]
    _name = (lead.full_name or "").strip()
    _known_name = bool(_name) and _name.lower() != "unknown"
    if _known_name:
        lines.append(f"  Name: {lead.full_name}")
    else:
        # Unknown caller — we have NOT been told their name. Don't ever address
        # them as "Unknown"; ask for it early so we can record + greet properly.
        lines.append("  Name: NOT KNOWN YET — you have not been told the candidate's name.")
        lines.append("    → Never address them as 'Unknown'. Early in the conversation,")
        lines.append("      warmly ask who you're speaking with (their name) BEFORE the")
        lines.append("      detailed questions, then continue.")
    lines.append(f"  Status: {lead.status.value}")
    _funnel_stage = str(getattr(lead, "funnel_stage", "") or "").strip()
    if _funnel_stage:
        lines.append(f"  Application stage: {_funnel_stage.replace('_', ' ')}")
    # Lead temperature (hot/warm/cold) — context only; the campaign prioritises
    # and escalates on this, the brain just talks naturally to whoever's on the line.
    _priority = str(getattr(lead, "lead_priority", "") or "").strip()
    if _priority:
        lines.append(f"  Priority: {_priority}")
    lines.append(f"  Source: {lead.source}")
    lines.append(f"  Preferred language: {lead.language_preference}")
    if lead.course_interest:
        lines.append(f"  Course interest: {lead.course_interest}")
    if lead.intake_year:
        lines.append(f"  Intake year: {lead.intake_year}")
    if lead.city:
        lines.append(f"  City: {lead.city}")
    if lead.persona_summary:
        lines.append(f"  Persona summary: {lead.persona_summary}")
    if lead.last_session_summary:
        lines.append(f"  Last session summary: {lead.last_session_summary}")
    if lead.open_concerns:
        lines.append("  Open concerns: " + "; ".join(lead.open_concerns))
    # Cumulative facts learned across prior conversations (BusinessLayer memory).
    # Lead.facts may be absent on older in-memory objects — guard with getattr.
    facts = getattr(lead, "facts", None)
    if isinstance(facts, dict) and facts:
        lines.append("  Known details from prior conversations (don't re-ask these):")
        for k, v in facts.items():
            if v is None or v == "":
                continue
            lines.append(f"    - {str(k).replace('_', ' ')}: {v}")

    # Materials already sent to the candidate — FOLLOW UP on these, do NOT
    # re-offer to send them again (closes the "I'll send it on WhatsApp" loop).
    sent_items = getattr(lead, "sent_items", None)
    if isinstance(sent_items, list) and sent_items:
        lines.append(
            "  Already shared with the candidate (ask if they reviewed it — do NOT re-offer to send):"
        )
        for it in sent_items:
            if not isinstance(it, dict):
                continue
            label = it.get("item")
            ch = it.get("channel")
            if label:
                lines.append(f"    - {label}" + (f" (sent on {ch})" if ch else ""))

    # FIRST CONTACT vs RETURNING — decides whether to introduce or resume.
    # A NEW call / WhatsApp thread starts with EMPTY conversation memory, so the
    # "don't re-introduce if there are prior turns" directive can't fire and the
    # brain greets like a stranger every time. Decide off the LEAD's history
    # instead: treat as RETURNING if we've engaged in ANY way before — status
    # moved past NEW, or we hold a prior summary / sent items / learned facts.
    _status_val = str(getattr(lead.status, "value", lead.status) or "").lower()
    # An application-lifecycle stage (started / fees pending / submitted) means
    # the candidate is well past first contact — treat as RETURNING even if the
    # operational status is still 'new'. ('raw'/'lead' don't imply prior contact.)
    _funnel_val = str(getattr(lead, "funnel_stage", "") or "").lower()
    _funnel_returning = _funnel_val in (
        "application_started", "fees_pending", "application_submitted",
    )
    _returning = (
        (_status_val not in ("", "new"))
        or _funnel_returning
        or bool(lead.last_session_summary)
        or (isinstance(sent_items, list) and bool(sent_items))
        or (isinstance(facts, dict) and bool(facts))
    )
    # Two independent guidance lines, ALWAYS computed:
    #   • status hint  — operational/quality (called/cold/warm/hot/new)
    #   • funnel hint  — admissions lifecycle (raw/application_*/fees_pending/...)
    # The funnel hint is None for stage 'lead'/blank (a plain lead opens by its
    # status tier), so it's only shown when there's a real lifecycle steer.
    _status_hint = opening_hint(lead.status)
    _funnel_hint = funnel_hint(_funnel_val)

    lines.append("")
    if direction == "inbound":
        # INBOUND — the candidate called US. This OVERRIDES the outbound
        # first-contact / returning intro: never ask "got a minute?" and never run
        # an outbound pitch. Warmly thank them for calling, then ask how you can
        # help and let THEM lead. The status + funnel hints still follow, so a
        # candidate at an application stage (e.g. fees_pending) still gets the
        # right talk-track once they say why they called.
        if _known_name:
            lines.append(
                "INBOUND CALL — the candidate called YOU. Do NOT ask if it's a good "
                "time, do NOT run a first-contact pitch, and do NOT re-ask basics "
                "you already have. Greet them by their first name, then help with "
                "whatever they raise and let THEM lead. Pick up naturally."
            )
        else:
            # Unknown inbound caller. The spoken OPENER already asked for their
            # name AND how we can help in one line — so do NOT ask the name again.
            # Instead: from their very first reply, CAPTURE the name they give
            # (e.g. "I'm Priya, I wanted to ask about fees" → name is Priya),
            # acknowledge it warmly, address them by that name for the REST of the
            # call, and answer their query. If they ask a question without giving a
            # name, answer it, then gently ask their name once so you can address
            # + record them. Never call them 'Unknown'.
            lines.append(
                "INBOUND CALL from someone whose name we DON'T have yet. The opening "
                "line you already spoke ASKED for their name and how you can help — "
                "so do NOT ask 'what's your name' again as a separate question. From "
                "their FIRST reply, pick up the name they state and USE it for the "
                "rest of the call; acknowledge it warmly and then help with their "
                "query. If they jump straight to a question with no name, answer it "
                "and gently ask their name once. Never address them as 'Unknown'."
            )
        lines.append("")
        lines.append("CONTEXT HINTS (apply once you know why they called — phrase in your own voice):")
        if _status_val not in ("", "new"):
            lines.append(f"  • Status ({_status_val}): {_status_hint}")
        if _funnel_hint:
            lines.append(f"  • Application stage ({_funnel_val}): {_funnel_hint}")
    elif _returning:
        lines.append(
            "RETURNING CANDIDATE — you've ALREADY connected with them before. Your "
            "first message/turn must NOT re-introduce you: do NOT state your name "
            "or organisation again, do NOT ask 'do you have a minute / got a sec', "
            "and do NOT give any recording disclosure. Greet warmly by first name "
            "and pick up exactly where you left off — reference the last "
            "conversation or anything you sent them (see above) and move it "
            "forward. e.g. 'Hi Ayush! Did you get a chance to look through the fee "
            "details I sent — any questions on them?'"
        )
        lines.append("")
        lines.append("OPENING HINTS (your intent for the first turn — phrase in your own voice):")
        # Status hint: skip the NEW one for a returning lead (it says "first
        # contact … recording disclosure", which contradicts RETURNING). Any other
        # status hint (called/cold/warm/hot/...) is shown.
        if _status_val not in ("", "new"):
            lines.append(f"  • Status ({_status_val}): {_status_hint}")
        # Funnel hint: the lifecycle talk-track (e.g. fees_pending → guide payment).
        if _funnel_hint:
            lines.append(f"  • Application stage ({_funnel_val}): {_funnel_hint}")
        # Safety net: if neither qualified above (e.g. status NEW + funnel 'lead'
        # but flagged returning by a prior summary), fall back to the status hint.
        if _status_val in ("", "new") and not _funnel_hint:
            lines.append(f"  • {_status_hint}")
    else:
        lines.append(
            "FIRST CONTACT — introduce yourself ONCE: a short, warm hello by first "
            "name, who you are and where you're from, and a light 'is now a good "
            "time?'. Keep it to one short line."
        )
        lines.append("")
        lines.append("OPENING HINTS (your intent for the first turn — phrase in your own voice):")
        lines.append(f"  • Status ({_status_val or 'new'}): {_status_hint}")
        if _funnel_hint:
            lines.append(f"  • Application stage ({_funnel_val}): {_funnel_hint}")
    return "\n".join(lines)


def _render_rag_block(query: str, k: int = 4) -> str | None:
    """On-demand hybrid RAG (Qdrant: dense + sparse BM25, RRF-fused server-side,
    routed university-vs-general per query). The KB is the only knowledge source
    — no JSON fallback. Returns None when Qdrant is unavailable or nothing
    matches; the turn then runs without a RAG slot rather than crashing."""
    try:
        from agent_backend.rag import get_retriever

        hits = get_retriever().search_text(query, k=k)
    except Exception as e:  # noqa: BLE001
        log.warning(
            "[agent] KB retrieval unavailable — RAG slot omitted this turn "
            "(check Qdrant / RAG_QDRANT_* settings)",
            err=str(e),
        )
        return None
    if not hits:
        return None
    return (
        "KNOWLEDGE BASE (snippets matched to the current turn — ANSWER the "
        "candidate directly from these, INCLUDING reading exact values out of any "
        "tables below. If the specific figure they asked for is NOT present here, "
        "say you'll confirm it and follow up — don't guess, and don't reflexively "
        "push sending it on WhatsApp mid-conversation)\n"
        + "\n".join(f"  - {h}" for h in hits)
    )


def _compose_system_prompt(
    *, channel: str, session: Session, user_text: str | None
) -> str:
    """Build the full system prompt for one turn. Single path for every channel
    — fills only the slots that apply (persona always; lead profile /
    conversation-state when there's a lead; playbook + RAG when relevant)."""
    from agent_backend.data.leads import Lead, LeadRepo
    from agent_backend.llm_agent.prompts.playbook import render_playbook

    identity_block = _render_persona(channel)
    # No channel carries live vision any more; the slot stays in the prompt
    # builders (typed Optional) but is always None.
    visual_context_block = None
    university_block = _render_university()

    lead: Lead | None = None
    lead_profile_block: str | None = None
    # Live per-turn conversation-state rendering was removed with the side-rail
    # scorer; lead state now comes from the BusinessLayer analyzer via LEAD PROFILE.
    conversation_state_block: str | None = None
    if session.lead_id:
        try:
            lead = LeadRepo.get().get_by_id(session.lead_id)
            if lead is not None:
                lead_profile_block = _render_lead_profile(
                    lead, direction=getattr(session, "direction", "outbound")
                )
        except Exception as e:  # noqa: BLE001
            log.warning("[agent] lead profile render failed", err=str(e))

    playbook_block = render_playbook(lead)
    # KB retrieval is no longer injected every turn — it's the on-demand
    # `search_knowledge_base` tool (counsellor channels). The always-on
    # UNIVERSITY core block above stays (small cached identity block).
    rag_block = None

    # High-recency gender reminder (last block) — derived from the persona's
    # pronouns; guards against mid-reply gender slips ('samajh gaya' for a female
    # persona). None for non-gendered personas (no behavioural change).
    gender_block: str | None = None
    try:
        from agent_backend.llm_agent.identity import gender_reminder, get_identity

        gender_block = gender_reminder(get_identity(_persona_name_for_channel(channel)))
    except Exception as e:  # noqa: BLE001
        log.warning("[agent] gender reminder unavailable", err=str(e))

    return build_system_prompt(
        channel=channel,  # type: ignore[arg-type]
        session=session,
        identity_block=identity_block,
        visual_context_block=visual_context_block,
        user_text=user_text,
        playbook_block=playbook_block,
        university_block=university_block,
        lead_profile_block=lead_profile_block,
        rag_block=rag_block,
        conversation_state_block=conversation_state_block,
        gender_block=gender_block,
    )


def _build_messages(
    *, channel: str, session: Session, user_text: str
) -> list[BaseMessage]:
    """[system, *history, user]. Conversation memory is prepended for EVERY
    channel so the bot keeps context and stops re-greeting on turn 2 — one
    brain, one memory model."""
    system = SystemMessage(
        content=_compose_system_prompt(channel=channel, session=session, user_text=user_text)
    )
    from agent_backend.llm_agent.conversation import get_conversation

    history = get_conversation(session.conversation_id).recent(n=20)
    return [system, *history, HumanMessage(content=user_text)]


def _drop_consecutive_dup_lines(text: str) -> str:
    """Collapse an immediately-repeated line. Small models occasionally echo a
    whole reply verbatim ("Sure…visit.\\nSure…visit."), which would be spoken
    twice. We drop a line that EXACTLY repeats the previous non-empty line
    (trimmed, case-insensitive); all other formatting (paragraphs, lists) is
    preserved untouched, so legitimate multi-line replies are unaffected."""
    out: list[str] = []
    last: str | None = None
    for ln in (text or "").split("\n"):
        s = ln.strip()
        if s and last is not None and s.lower() == last.lower():
            continue  # exact repeat of the previous line → an echo, drop it
        out.append(ln)
        if s:
            last = s
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Public: streaming run
# ---------------------------------------------------------------------------

async def run_stream(
    text: str,
    *,
    channel: str,
    session: Session,
    record_user: bool = True,
) -> AsyncIterator[str]:
    """Stream LLM response tokens for one user turn through a LangGraph ReAct
    agent. Yields raw text deltas; the caller wraps each in its transport's
    frame type (TextFrame for Pipecat, JSON for ACS media WS, HTTP chunk for
    chat/email).

    record_user: when True (default) this appends the user turn to conversation
        memory itself. Callers that may CANCEL this stream before it reaches the
        append (e.g. the avatar bridge, which cancels fast on a follow-up STT
        segment) should record the user turn THEMSELVES before calling and pass
        record_user=False — otherwise a cancelled run loses the turn from memory
        and the next run can't see it (the "dropped first segment" bug)."""
    text = (text or "").strip()
    if not text:
        return

    # Read history BEFORE appending this turn, otherwise the current
    # HumanMessage would be duplicated in the prompt. Every channel has memory.
    from agent_backend.llm_agent.conversation import get_conversation

    convo = get_conversation(session.conversation_id)

    graph = _get_graph(session)
    # Prompt assembly does BLOCKING work for the counselor family — KB
    # retrieval embeds the query via a synchronous OpenAI call (~50-300ms, plus
    # a one-time index load on the first turn). Running that on the event loop
    # starves the realtime audio I/O (heartbeat misses, choppy TTS). Offload to
    # a worker thread so audio keeps flowing while we build the prompt.
    messages = await asyncio.to_thread(
        _build_messages, channel=channel, session=session, user_text=text
    )
    if convo is not None and record_user:
        convo.append_user(text)

    log.info(
        "[agent] run_stream begin",
        session=session.short(),
        channel=channel,
        user_chars=len(text),
        system_chars=len(messages[0].content) if messages else 0,
    )

    t0 = time.monotonic()
    tokens_yielded = 0
    reply_chunks: list[str] = []
    # SUPPRESS PRE-TOOL CHATTER. A ReAct turn can emit assistant TEXT alongside a
    # tool call — that's where the model "thinks out loud" and often FABRICATES an
    # answer before the tool has run (e.g. guessing analytics numbers), then
    # answers AGAIN after the tool returns. Speaking both = double-talk + a
    # hallucinated pre-answer. We therefore DROP the text of any assistant message
    # that carries tool calls, and speak only the FINAL (post-tool) answer. The
    # brief "let me pull that up" filler comes from the tool's own return, not a
    # guessed pre-answer. Tracked per message id because text/tool-call chunks for
    # the same message interleave in messages-mode.
    def _chunk_text(content: object) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(p["text"]) for p in content
                if isinstance(p, dict) and "text" in p
            )
        return ""

    # Tools whose ACCOMPANYING TEXT must still be SPOKEN (not suppressed). Most
    # tool calls (search_knowledge_base, present_analytics) carry "thinking out
    # loud" / hallucinated pre-answers we drop — but a "speak-then-act" tool like
    # end_call attaches the goodbye line that the user MUST hear before the
    # action fires. For those, we flush the message text instead of discarding it.
    _SPEAK_THEN_ACT_TOOLS = {"end_call"}

    # Per-message streaming with a SENTENCE-LEVEL HOLD (the latency fix).
    #
    # We want to STREAM a normal reply token-by-token so TTS can start on the
    # FIRST sentence while the LLM is still writing the rest — instead of waiting
    # for the whole message (the old behaviour: buffer everything, yield 1 chunk
    # at message end → `tokens=1` → multi-second dead air before any audio).
    #
    # But we must still SUPPRESS pre-tool "thinking out loud" text on tool-calling
    # messages (anti-hallucination). The tension: a tool_call chunk can arrive
    # AFTER some text. We resolve it WITHOUT buffering the whole message by only
    # ever EMITTING COMPLETE SENTENCES, and only while NO tool call has been seen
    # on this message yet:
    #   - text streams out sentence-by-sentence as soon as each sentence completes
    #     AND the message is still tool-free → first audio starts ~1 sentence in;
    #   - the moment a tool_call chunk appears, we STOP emitting and DROP the
    #     unflushed tail (the partial pre-tool sentence) — for a normal reply there
    #     is no tool call so everything flushes; for a tool-calling message the
    #     model almost always emits the tool call FIRST (no text) so nothing leaks;
    #   - end_call (speak-then-act) is allowed to flush its goodbye at message end.
    # This keeps the guarantee for the common cases while removing the buffering
    # that serialized the whole answer behind generation.
    import re as _re

    _SENT_END = _re.compile(r"([.!?][\"')\]]*\s+)")

    cur_id: str | None = None
    cur_buf: list[str] = []               # UNFLUSHED tail of the current message (token chunks)
    cur_tool_name: str | None = None      # name of THIS message's tool call, if any
    cur_committed: bool = False           # already streaming this speak message live

    def _should_speak(tool_name: str | None) -> bool:
        return tool_name is None or tool_name in _SPEAK_THEN_ACT_TOOLS

    stream_tokens = getattr(get_settings(), "enable_token_streaming", True)
    # Lookahead before committing to speak a message: large enough that a
    # tool-calling message reveals its tool_call FIRST (its text is then dropped),
    # small enough to start audio quickly. The downstream SentenceStreamer still
    # coalesces tokens to sentence boundaries for TTS, so partial emits are fine.
    _STREAM_COMMIT_CHARS = 32

    # Langfuse: trace this whole turn (LLM + tools + tokens + cost), grouped by
    # conversation. Returns {} when tracing is off — a clean no-op (**{} adds
    # nothing). Events flush on a background thread, so this adds nothing to the
    # realtime hot path.
    _tracing = trace_config(session=session, tags=["run_stream"])
    try:
        async for chunk, _meta in graph.astream(
            {"messages": messages},
            stream_mode="messages",
            **_tracing,
        ):
            if not isinstance(chunk, AIMessageChunk):
                continue
            mid = getattr(chunk, "id", None)
            # New message started → flush the previous one's HELD text IF it was a
            # speak message that never committed (e.g. a short reply under the
            # lookahead, or streaming disabled).
            if mid != cur_id:
                if cur_buf and not cur_committed and _should_speak(cur_tool_name):
                    flushed = _drop_consecutive_dup_lines("".join(cur_buf))
                    if flushed:
                        tokens_yielded += 1
                        reply_chunks.append(flushed)
                        yield flushed
                cur_id, cur_buf, cur_tool_name, cur_committed = mid, [], None, False
            # Capture the tool name (arrives on the first tool-call chunk; later
            # chunks carry only arg deltas with name=None — keep the first seen).
            # If a NON-speak tool call appears, abandon any unflushed tail for this
            # message (anti-hallucination: drop pre-tool chatter). Anything already
            # streamed live was, by construction, complete sentences of a message
            # that looked tool-free at the time — the model emits the tool call
            # first in practice, so this rarely (never, observed) discards real
            # answer text.
            for tc in (getattr(chunk, "tool_call_chunks", None) or []):
                nm = tc.get("name")
                if nm:
                    cur_tool_name = nm
                    if not _should_speak(cur_tool_name):
                        cur_buf = []  # drop unflushed pre-tool tail
            t = _chunk_text(chunk.content)

            # Blocking tool call (anything but a speak-then-act tool) → DROP this
            # message's text, committed or not — we never speak a pre-tool answer.
            if cur_tool_name is not None and not _should_speak(cur_tool_name):
                cur_buf = []
                continue

            if cur_committed:
                # Already streaming this speak message — emit the new token live.
                if t:
                    tokens_yielded += 1
                    reply_chunks.append(t)
                    yield t
                continue

            # Undecided speak message: hold text until we COMMIT (streaming on)
            # or the message ends (streaming off / very short reply).
            if t:
                cur_buf.append(t)
            if (
                stream_tokens
                and _should_speak(cur_tool_name)
                and sum(len(x) for x in cur_buf) >= _STREAM_COMMIT_CHARS
            ):
                flushed = _drop_consecutive_dup_lines("".join(cur_buf))
                cur_buf = []
                cur_committed = True
                if flushed:
                    tokens_yielded += 1
                    reply_chunks.append(flushed)
                    yield flushed
        # End of stream → flush any residual HELD text of a speak message.
        if cur_buf and not cur_committed and _should_speak(cur_tool_name):
            flushed = _drop_consecutive_dup_lines("".join(cur_buf))
            if flushed:
                tokens_yielded += 1
                reply_chunks.append(flushed)
                yield flushed
    except Exception as e:  # noqa: BLE001
        log.warning("[agent] LangGraph stream failed", session=session.short(), err=str(e))
        yield "Sorry — I had trouble reaching the model. Could you say that again?"
        return
    finally:
        # Persist whatever the bot actually said (full reply OR a partial on
        # cancellation/barge-in). Empty replies skipped.
        full_reply = "".join(reply_chunks).strip()
        if full_reply:
            convo.append_bot(full_reply)

    log.info(
        "[agent] run_stream end",
        session=session.short(),
        tokens=tokens_yielded,
        ms=int((time.monotonic() - t0) * 1000),
    )


# ---------------------------------------------------------------------------
# Bot-speaks-first: the outbound-call opener (counselor/voice family only).
# ---------------------------------------------------------------------------
#
# Production voice agents (Retell, Vapi, Bland, Air, OpenAI Realtime) all
# expose a "first message" that fires as soon as the carrier bridges the
# call — before the candidate says anything. `open_call()` generates it by
# feeding the brain a synthetic system-side directive instead of an STT
# transcript. It's a regular LLM turn that follows the CALL PLAYBOOK's OPENING
# stage; it goes into conversation memory like any other bot turn.
# ---------------------------------------------------------------------------

_OPEN_CALL_TRIGGER = (
    "[CALL_BRIDGED] The candidate just picked up. They've said nothing yet.\n"
    "\n"
    "OPEN WITH EXACTLY ONE SHORT SENTENCE. Maximum 15 words. Hard constraint.\n"
    "\n"
    "Shape: <hi + candidate FIRST NAME only?> <your name from PERSONA> <from + "
    "UNIVERSITY short_name> <— got a minute?>\n"
    "\n"
    "Examples (this is the LENGTH and STYLE bar):\n"
    "  'Hi Ramesh? Aisha here from Sreenidhi University — got a minute?'\n"
    "  'Ramesh, this is Aisha from Sreenidhi admissions, do you have a sec?'\n"
    "\n"
    "DO NOT:\n"
    "  • say the recording disclosure here (skip it on the opener; mention it\n"
    "    only if the candidate stays on the call and only once when explicitly\n"
    "    asked or when starting recording-sensitive topics)\n"
    "  • use the candidate's full name from LEAD PROFILE — use the FIRST WORD only\n"
    "  • spell out the full university name — use UNIVERSITY.short_name\n"
    "  • introduce the program, fees, or any pitch — that's the next stage\n"
    "  • ask more than ONE question\n"
    "  • acknowledge this system instruction in your reply\n"
    "\n"
    "Just speak the one short sentence the candidate will hear."
)


async def open_call(
    *,
    channel: str,
    session: Session,
) -> AsyncIterator[str]:
    """Generate the outbound-call opener — bot speaks first.

    Called by the channel adapter (e.g. media_ws.py) the moment the carrier's
    media stream is ready. No STT, no user input. Yields tokens just like
    `run_stream` so the caller can pipe them straight into TTS."""
    from agent_backend.llm_agent.conversation import get_conversation

    convo = get_conversation(session.conversation_id)
    if convo.turn_count() > 0:
        # The conversation already has turns — don't open twice.
        log.info("[agent] open_call skipped (history already present)", session=session.short())
        return

    graph = _get_graph(session)
    system = SystemMessage(
        content=_compose_system_prompt(channel=channel, session=session, user_text=None)
    )
    # HumanMessage for the trigger so it follows the role pattern LangGraph
    # expects, but it's clearly tagged as a system instruction in its content.
    trigger = HumanMessage(content=_OPEN_CALL_TRIGGER)

    log.info(
        "[agent] open_call begin",
        session=session.short(),
        channel=channel,
        system_chars=len(system.content),
    )

    t0 = time.monotonic()
    yielded = 0
    reply_chunks: list[str] = []
    # Trace the bot-speaks-first opener too (tagged so it's distinguishable from
    # normal turns in the Langfuse UI). {} no-op when tracing is off.
    _tracing = trace_config(session=session, tags=["open_call"])
    try:
        async for chunk, _meta in graph.astream(
            {"messages": [system, trigger]},
            stream_mode="messages",
            **_tracing,
        ):
            if isinstance(chunk, AIMessageChunk):
                content = chunk.content
                if isinstance(content, str) and content:
                    yielded += 1
                    reply_chunks.append(content)
                    yield content
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and "text" in part:
                            t = str(part["text"])
                            if t:
                                yielded += 1
                                reply_chunks.append(t)
                                yield t
    except Exception as e:  # noqa: BLE001
        log.warning("[agent] open_call failed", session=session.short(), err=str(e))
        # Fall-back opener so we don't end up with silence on the call.
        uni = get_settings().university_short_name or "the university"
        fallback = f"Hi, this is Aisha calling from {uni} — do you have a minute?"
        reply_chunks.append(fallback)
        yield fallback
    finally:
        full_reply = "".join(reply_chunks).strip()
        if full_reply:
            convo.append_bot(full_reply)
        log.info(
            "[agent] open_call end",
            session=session.short(),
            tokens=yielded,
            ms=int((time.monotonic() - t0) * 1000),
        )
