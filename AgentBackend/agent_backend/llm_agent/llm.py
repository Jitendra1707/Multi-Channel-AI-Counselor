"""Process-wide ChatOpenAI cache.

`langchain-openai.ChatOpenAI` is the LLM client LangGraph's
`create_react_agent` consumes. ONE brain, ONE model: every channel
(voice, whatsapp, chat, email, pipecat, ...) shares the same `get_llm()`
configured from `llm_model` / `llm_max_tokens`. What varies per channel is
the system prompt, the tool surface, and the memory layer — not the
underlying LLM client.

We cache instances keyed by the settings that actually change the client
(model, api url, key, max_tokens, temperature) so a runtime config flip
(a test overriding the model, hot config) rebuilds on the next call instead
of returning a stale client.

Kept intentionally tiny — the rest of the agent layer never touches the
provider directly.
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from agent_backend.config import get_settings
from agent_backend.infra import get_logger

log = get_logger(__name__)


# Bounded cache keyed by the settings that change the underlying client. A
# single model normally means a single live entry; keying on the full combo
# keeps it correct if settings change at runtime (e.g. tests) without
# thrashing.
_LLM_CACHE: dict[tuple[str, str, int, float, str], ChatOpenAI] = {}
_LLM_CACHE_MAX = 8


def _is_reasoning_model(model: str) -> bool:
    """Reasoning models (OpenAI o-series, gpt-5.x) need a DIFFERENT client
    config than classic chat models: they only accept the default temperature,
    the token cap is `max_completion_tokens` (and must leave headroom for hidden
    reasoning tokens — a small cap starves the visible reply), and they expose a
    `reasoning_effort` dial. Misconfiguring these is the usual cause of
    'gpt-5-mini gives short / truncated / weird output' — the reply gets starved
    by a 200-token cap that the reasoning pass already spent."""
    m = (model or "").lower()
    return m.startswith(("o1", "o3", "o4", "gpt-5"))


def get_llm() -> ChatOpenAI:
    """The brain's LLM — channel-agnostic.

    Built from `llm_model` / `llm_max_tokens` (plus the shared api url / key /
    temperature). Used by every channel; the counselor vs avatar difference
    lives in the prompt and tools, not here.

    Auto-adapts to reasoning models (gpt-5.x / o-series): default temperature,
    `max_completion_tokens` with reasoning headroom, and a low `reasoning_effort`
    so real-time voice/chat stays fast and on-point. Classic models (gpt-4o-mini)
    keep their original config unchanged.
    """
    s = get_settings()
    key = (s.llm_model, s.llm_api_url, s.llm_max_tokens, s.llm_temperature, s.llm_api_key)
    cached = _LLM_CACHE.get(key)
    if cached is not None:
        return cached
    if not s.llm_api_key:
        raise RuntimeError(
            "LLM_API_KEY is empty. Copy a real key from LLmLayer/.env."
        )

    common = dict(
        model=s.llm_model,
        api_key=s.llm_api_key,  # type: ignore[arg-type]
        base_url=s.llm_api_url,
        # Do NOT set `parallel_tool_calls` here. OpenAI rejects it with
        # 400 when no tools are bound. LangGraph's create_react_agent will
        # pass parallel_tool_calls internally when it binds tools.
        stream_usage=False,
        streaming=True,
    )

    if _is_reasoning_model(s.llm_model):
        # Output budget must cover hidden reasoning tokens + the visible reply,
        # so floor it well above the (voice-tuned) llm_max_tokens. The prompt
        # still asks for short replies, so spoken output stays brief while the
        # model has room to think. `max_completion_tokens` goes via model_kwargs
        # (langchain-openai 0.2.x has no named field for it).
        #
        # NOTE: we deliberately do NOT send `reasoning_effort`. The agent always
        # binds function tools (end_call, etc.), and gpt-5.x rejects
        # `reasoning_effort` + function tools on the /v1/chat/completions endpoint
        # ("...not supported... use /v1/responses instead"). The Responses API
        # isn't available in langchain-openai 0.2.x, so we omit the dial and let
        # the model use its default effort — tool calling then works fine.
        budget = max(s.llm_max_tokens, s.llm_reasoning_max_tokens)
        llm = ChatOpenAI(
            **common,
            temperature=1,  # reasoning models only accept the default
            model_kwargs={"max_completion_tokens": budget},
        )
        log.debug(
            "LLM client built (reasoning)",
            model=s.llm_model,
            max_completion_tokens=budget,
        )
    else:
        llm = ChatOpenAI(
            **common,
            temperature=s.llm_temperature,
            max_tokens=s.llm_max_tokens,
        )
        log.debug("LLM client built", model=s.llm_model, max_tokens=s.llm_max_tokens)

    # Bound the cache: drop the oldest entry if we exceed the cap.
    if len(_LLM_CACHE) >= _LLM_CACHE_MAX:
        _LLM_CACHE.pop(next(iter(_LLM_CACHE)))
    _LLM_CACHE[key] = llm
    return llm
