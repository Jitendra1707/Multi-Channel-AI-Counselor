"""present_analytics — the director's generative-UI tool.

Two stages:
  1. FETCH (deterministic Python): pull the aggregated outreach slice for the
     month(s) the director asked about from the active StatsProvider. The LLM
     never computes numbers.
  2. VISUALISE (fast LLM + structured output): a data-viz-expert model turns the
     director's question + the resolved month's stats into a CONSTRAINED
     `UiDirective` (chart / report spec). OpenAI structured outputs guarantee
     valid JSON; we re-validate with Pydantic before sending.

The validated directive is pushed to the browser over the avatar's WebRTC data
channel (via the per-conversation ui_emitter). The tool then returns a SHORT
spoken takeaway to the brain — and CRUCIALLY, when the question names a specific
metric/month, the exact figure is computed HERE in Python and placed first in
the return string, so the avatar SPEAKS the correct number regardless of what
the viz LLM charts.

Director (avatar_video) channel only.
"""

from __future__ import annotations

import json

from langchain_core.tools import BaseTool, tool

from agent_backend.infra import get_logger
from agent_backend.llm_agent.tools._base import ToolContext

log = get_logger(__name__)

_VIZ_SYSTEM = (
    "You are a data-visualisation expert preparing ONE on-screen panel for a "
    "live executive briefing to a university director. You are given the "
    "director's question and the aggregated outreach statistics (already "
    "computed — do not invent or change any number; use the values as given).\n"
    "\n"
    "Choose the SINGLE most relevant view for the question and express it as a "
    "UiDirective:\n"
    "  - A KPI strip for headline 'how are we doing' questions.\n"
    "  - A line chart for trends over time (e.g. calls per day).\n"
    "  - A bar chart for comparisons across categories (counsellor, programme).\n"
    "  - A donut for composition/splits (outcomes, languages).\n"
    "  - A funnel is best shown as a bar chart of the stages in order.\n"
    "  - A table or report block for detailed drill-downs.\n"
    "When the question COMPARES two months, put the months on the x-axis of a "
    "bar chart (one bar per month for the asked metric), or use a KPI strip / "
    "table showing both months side by side. Use the exact numbers given.\n"
    "You MAY combine a KPI strip with one chart when it strengthens the point, "
    "but do not dump every chart at once — pick what answers THIS question.\n"
    "Give the panel a short title and a one-line narration caption. Use the exact "
    "numbers from the data."
)

# Question keyword → (metric field, spoken noun). Order matters: "not interested"
# is checked before "interested" by the parser below.
_METRIC_KEYWORDS: list[tuple[tuple[str, ...], str, str]] = [
    (("not interested", "uninterested", "not-interested", "declined", "rejected"),
     "not_interested", "leads who were not interested"),
    (("escalat", "human counsellor", "human counselor", "handed", "handoff"),
     "escalated_to_human_counsellor", "leads escalated to a human counsellor"),
    (("interested", "warm", "keen"),
     "interested", "interested leads"),
    (("outreach", "calls", "call", "dial", "contacted", "reached out"),
     "outreach_calls", "outreach calls"),
    (("leads", "lead", "prospect"),
     "total_leads", "total leads"),
]

_COMPARE_WORDS = ("compare", "comparison", "versus", " vs ", "vs.", "against",
                  "both months", "month over month", "month-over-month", "trend",
                  "growth", "change", "difference")


def _detect_metric(q: str) -> tuple[str, str] | None:
    """Return (field, spoken_noun) for the metric named in the question, or None."""
    for keys, field, noun in _METRIC_KEYWORDS:
        if any(k in q for k in keys):
            return field, noun
    return None


def _fmt(value: float) -> str:
    """Whole numbers without a trailing .0; keep one decimal otherwise."""
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _viz_llm():
    """A structured-output client bound to the UiDirective schema."""
    from langchain_openai import ChatOpenAI

    from agent_backend.config import get_settings
    from agent_backend.schemas.ui_directive import UiDirective

    s = get_settings()
    llm = ChatOpenAI(
        model=s.analytics_viz_model,
        api_key=s.llm_api_key,  # type: ignore[arg-type]
        base_url=s.llm_api_url,
        temperature=0,
    )
    # Structured output → guaranteed-valid UiDirective (OpenAI json_schema mode).
    return llm.with_structured_output(UiDirective)


def _resolve_periods(provider, q: str) -> tuple[list[str], bool]:
    """Which month key(s) the question refers to, and whether it's a comparison.

    Matches the question against the provider's available periods (key + label).
    Falls back to [None] (provider default) when no month is named.
    """
    periods: list[str] = []
    try:
        available = provider.available_periods() if hasattr(provider, "available_periods") else []
    except Exception:  # noqa: BLE001
        available = []
    for p in available:
        key = str(p.get("key", "")).lower()
        label = str(p.get("label", "")).lower()
        # Match on the month token (key) or the first word of the label ("june 2026" → "june").
        label_word = label.split()[0] if label else ""
        if (key and key in q) or (label_word and label_word in q):
            periods.append(p["key"])

    wants_compare = any(w in q for w in _COMPARE_WORDS) or len(periods) >= 2
    if wants_compare and len(periods) < 2 and len(available) >= 2:
        # "compare the two months" with no explicit names → use all available.
        periods = [p["key"] for p in available]
    if not periods:
        periods = [None]  # provider default (latest)
    return periods, (wants_compare and len(periods) >= 2)


def _spoken_figure(provider, periods: list[str], metric: tuple[str, str] | None,
                   is_comparison: bool) -> str | None:
    """Compute the authoritative spoken takeaway from raw month data (Python, not
    the LLM). Returns None when raw months aren't available (e.g. dummy provider),
    so the caller falls back to the viz narration."""
    if not hasattr(provider, "get_month"):
        return None

    def month(key):
        try:
            return provider.get_month(key)
        except Exception:  # noqa: BLE001
            return None

    # --- comparison across two months ---
    if is_comparison and len(periods) >= 2:
        a, b = month(periods[0]), month(periods[1])
        if not a or not b:
            return None
        la, lb = a.get("label", periods[0]), b.get("label", periods[1])
        if metric:
            field, noun = metric
            va, vb = float(a.get(field, 0) or 0), float(b.get(field, 0) or 0)
            trend = "up" if vb > va else "down" if vb < va else "unchanged"
            pct = ""
            if va:
                pct = f" ({trend} about {abs(round((vb - va) / va * 100))} percent)" if trend != "unchanged" else ""
            return (f"There were {_fmt(va)} {noun} in {la} and {_fmt(vb)} in {lb}{pct}.")
        # no specific metric → compare the two headline numbers
        return (
            f"Comparing {la} and {lb}: total leads {_fmt(float(a.get('total_leads', 0) or 0))} "
            f"versus {_fmt(float(b.get('total_leads', 0) or 0))}, and interested "
            f"{_fmt(float(a.get('interested', 0) or 0))} versus "
            f"{_fmt(float(b.get('interested', 0) or 0))}."
        )

    # --- single month ---
    m = month(periods[0])
    if not m:
        return None
    label = m.get("label", "this period")
    if metric:
        field, noun = metric
        return f"There were {_fmt(float(m.get(field, 0) or 0))} {noun} in {label}."
    # no specific metric → speak a one-line headline of the month
    return (
        f"In {label} there were {_fmt(float(m.get('total_leads', 0) or 0))} total leads, "
        f"{_fmt(float(m.get('outreach_calls', 0) or 0))} outreach calls, "
        f"{_fmt(float(m.get('interested', 0) or 0))} interested, "
        f"{_fmt(float(m.get('not_interested', 0) or 0))} not interested, and "
        f"{_fmt(float(m.get('escalated_to_human_counsellor', 0) or 0))} escalated to a human counsellor."
    )


def build_tools(ctx: ToolContext) -> list[BaseTool]:
    session = ctx.session
    if session.channel != "avatar_video":
        return []

    @tool
    async def present_analytics(question: str) -> str:
        """Put the right outreach chart or report on the director's screen and
        return a one-line spoken takeaway.

        Call this for ANY quantitative question from the director — call volume,
        outcomes, conversion/funnel, per-month figures, comparisons between
        months, or the headline numbers. Name the month in your reasoning if the
        director did (e.g. June or July). Before calling, say one short natural
        line so there's no silence (e.g. "Let me pull that up"). After it returns,
        SPEAK the takeaway it gives you in one or two sentences — the chart on
        screen carries the detail; don't read the numbers row by row.

        Args:
            question: the director's question, in natural language.
        """
        import asyncio

        from agent_backend.analytics import get_stats_provider
        from agent_backend.channels.avatar_video.ui_emitter import emit_ui_directive

        q_raw = (question or "").strip()
        if not q_raw:
            return "Ask the director to clarify what they'd like to see."
        q = q_raw.lower()

        provider = get_stats_provider()
        periods, is_comparison = _resolve_periods(provider, q)
        metric = _detect_metric(q)

        # (1) authoritative spoken figure — computed in Python from raw months.
        spoken = _spoken_figure(provider, periods, metric, is_comparison)

        # (2) build the viz payload for the resolved month(s) only, so the chart
        # can't show a different month than the one we're speaking about.
        if is_comparison and len(periods) >= 2:
            payload = {
                "comparison": True,
                "months": {
                    (provider.get_outreach_stats(p).period_label if hasattr(provider, "get_outreach_stats") else str(p)):
                        provider.get_outreach_stats(p).model_dump()
                    for p in periods
                },
            }
        else:
            stats = provider.get_outreach_stats(periods[0])
            payload = {"period": stats.period_label, "stats": stats.model_dump()}

        prompt = (
            f"Director's question: {q_raw}\n\n"
            f"Outreach statistics (JSON, authoritative — use these exact numbers):\n"
            f"{json.dumps(payload, ensure_ascii=False)}"
        )
        try:
            directive = await asyncio.to_thread(
                lambda: _viz_llm().invoke([("system", _VIZ_SYSTEM), ("human", prompt)])
            )
        except Exception as e:  # noqa: BLE001
            log.warning("[present_analytics] viz step failed", err=str(e)[:200], q=q_raw[:120])
            if spoken:
                # We still know the exact number — let the avatar speak it even
                # though the chart couldn't be built.
                return (
                    f"{spoken} (The on-screen chart could not be built this time — "
                    f"speak the figure to the director, then stop.)"
                )
            return (
                "I couldn't build that view just now — tell the director you'll "
                "bring it up in a moment."
            )

        directive_dict = directive.model_dump()
        shown = emit_ui_directive(session.conversation_id, directive_dict)
        kinds = [b.get("kind") for b in directive_dict.get("blocks", [])]
        log.info(
            "[present_analytics] directive emitted",
            session=session.short(), shown=shown, comparison=is_comparison,
            periods=periods, metric=(metric[0] if metric else None),
            title=directive_dict.get("title"), blocks=kinds, q=q_raw[:120],
        )

        takeaway = spoken or directive.narration or directive.title
        if not shown:
            # Channel not open (shouldn't happen on a live avatar call) — still
            # let the avatar speak the takeaway.
            return (
                f"(The screen panel could not be shown.) Speak this takeaway to "
                f"the director: {takeaway}"
            )
        return (
            f"{takeaway} The panel '{directive.title}' is now on the director's "
            f"screen. Speak that takeaway in one or two sentences, in your own "
            f"words, then STOP and wait for the director — do not add extra offers "
            f"or keep talking."
        )

    return [present_analytics]
