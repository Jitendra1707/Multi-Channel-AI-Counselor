"""Langfuse tracing — LangChain/LangGraph callback wiring (langfuse v3).

WHY HERE: every channel runs through the SAME two LangGraph invoke sites in
`llm_agent/agent.py` (`run_stream` and `open_call`). Attaching a Langfuse
`CallbackHandler` to those `graph.astream(... config=...)` calls traces the
WHOLE turn for free — LLM request/response, every tool call, token usage,
latency and cost — with zero changes to the agent, prompt, or tool logic.

LANGFUSE v3 API (differs from v2):
  - The client is configured ONCE from env (LANGFUSE_PUBLIC_KEY / SECRET_KEY /
    HOST) via the global `Langfuse()` singleton — we just touch `get_client()`.
  - `CallbackHandler()` takes NO per-trace args. Instead, session/user/tags go
    in the per-run LangChain `config["metadata"]` under RESERVED keys:
      langfuse_session_id, langfuse_user_id, langfuse_tags.
  - LangGraph's `astream`/`ainvoke` take callbacks+metadata ONLY nested inside
    a `config=` (RunnableConfig) kwarg — NOT as top-level kwargs. So
    `trace_config()` returns `{"config": {...}}`, and the call site spreads it
    (`**cfg` → `config={...}`). When tracing is off it returns `{}` and `**{}`
    adds nothing.

SAFE NO-OP: if the `langfuse` package / its `langchain` dep isn't importable,
the master switch is off, or the keys are blank, `trace_config()` returns `{}`
— the agent then invokes with no extra config, exactly as before. Tracing can
never break a live call.

HOT PATH: the handler only ENQUEUES events; an OpenTelemetry background
exporter batches and flushes them asynchronously, so it adds nothing to voice
latency.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_backend.config import get_settings
from agent_backend.infra.logger import get_logger

if TYPE_CHECKING:
    from agent_backend.llm_agent.session import Session

log = get_logger(__name__)

# Resolved once: True only when langfuse is importable AND configured. We log
# the outcome a single time so a misconfiguration is obvious in startup logs
# instead of silently producing no traces.
_RESOLVED: bool | None = None
_HANDLER: Any = None  # shared, stateless v3 CallbackHandler (built once)


def _resolve() -> bool:
    """Decide once whether tracing is live, and cache the verdict.

    Returns True only if: master switch on, both keys present, the `langfuse`
    v3 langchain integration imports cleanly, and the global client initializes
    from env. Any miss → tracing is a no-op. langfuse v3 reads the keys from the
    LANGFUSE_* env vars itself, so we just have to make sure they're exported."""
    global _RESOLVED, _HANDLER
    if _RESOLVED is not None:
        return _RESOLVED

    s = get_settings()
    if not (s.langfuse_enabled and s.langfuse_public_key and s.langfuse_secret_key):
        log.info(
            "[tracing] Langfuse OFF (set LANGFUSE_ENABLED + LANGFUSE_PUBLIC_KEY "
            "+ LANGFUSE_SECRET_KEY to enable)"
        )
        _RESOLVED = False
        return False

    # langfuse v3 reads LANGFUSE_PUBLIC_KEY / SECRET_KEY / HOST from the
    # ENVIRONMENT. Our settings come from `.env` via pydantic, which does NOT
    # export them to os.environ — so push them in before initializing the
    # client, otherwise get_client() comes back disabled with "no public_key".
    import os

    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", s.langfuse_public_key)
    os.environ.setdefault("LANGFUSE_SECRET_KEY", s.langfuse_secret_key)
    os.environ.setdefault("LANGFUSE_HOST", s.langfuse_host)

    try:
        from langfuse import get_client  # type: ignore
        from langfuse.langchain import CallbackHandler  # type: ignore
    except Exception as e:  # noqa: BLE001  (ImportError / missing langchain dep)
        log.warning(
            "[tracing] Langfuse keys set but the langchain integration failed to "
            "import — tracing disabled. Run `pip install 'langfuse>=3' langchain`.",
            err=str(e)[:200],
        )
        _RESOLVED = False
        return False

    # Touch the global client so an auth/host problem surfaces here (once), not
    # silently per turn. auth_check() pings the project's credentials.
    try:
        client = get_client()
        ok = client.auth_check()
        if not ok:
            log.warning(
                "[tracing] Langfuse credentials rejected (auth_check failed) — "
                "check LANGFUSE_PUBLIC_KEY / SECRET_KEY / HOST. Tracing disabled."
            )
            _RESOLVED = False
            return False
    except Exception as e:  # noqa: BLE001
        # Don't hard-fail on a transient network blip — the handler will still
        # try to flush later. Log and proceed.
        log.warning("[tracing] Langfuse auth_check raised (continuing)", err=str(e)[:200])

    _HANDLER = CallbackHandler()
    log.info("[tracing] Langfuse ON", host=s.langfuse_host)
    _RESOLVED = True
    return True


def warmup_tracing() -> None:
    """Resolve Langfuse ONCE at server boot, off the live hot path.

    `_resolve()` does the heavy one-time work — importing the langfuse langchain
    integration (which pulls in LangChain + OpenTelemetry), pinging the project
    with `auth_check()` (a network round-trip), and building the CallbackHandler.
    Lazily, that all runs INLINE on the FIRST `run_stream`/`open_call`, on the
    event loop — adding seconds to a candidate's very first reply (and starving
    realtime audio while it blocks). Calling this at boot — ideally via
    `asyncio.to_thread`, since the import + auth_check are synchronous/blocking —
    pre-pays it so the first live turn finds the verdict + handler already cached.

    Best-effort and idempotent: `_resolve()` caches its verdict and returns early
    if already resolved; on any failure here the first turn just resolves lazily
    as before (or stays a clean no-op when tracing is disabled). Never raises."""
    try:
        live = _resolve()
        log.info("[tracing] warmup done", live=live)
    except Exception as e:  # noqa: BLE001
        log.warning(
            "[tracing] warmup failed (will resolve lazily on first turn)",
            err=str(e)[:200],
        )


def trace_config(
    *,
    session: "Session",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Build the LangChain run `config` for one agent turn (langfuse v3).

    Spread the result straight into the agent invoke — it expands to a
    `config=` (RunnableConfig) kwarg, which is the ONLY place LangGraph accepts
    callbacks/metadata:

        cfg = trace_config(session=session, tags=["run_stream"])
        graph.astream({"messages": messages}, stream_mode="messages", **cfg)
        #   → graph.astream(..., config={"callbacks": [...], "metadata": {...}})

    Returns `{}` (a clean no-op) when tracing is off, so `**cfg` adds nothing
    and the call site is identical whether or not Langfuse is configured.

    The run metadata tags the trace with the v3 RESERVED keys so the Langfuse
    UI groups and filters correctly:
      - langfuse_session_id → conversation_id (groups all turns of one call/chat)
      - langfuse_user_id     → lead_id          (groups a candidate across calls)
      - langfuse_tags        → channel + caller tags (e.g. "run_stream")
    plus plain metadata (lead_status, language, call_id) for filtering.
    """
    if not _resolve():
        return {}

    try:
        # LangGraph wants callbacks+metadata nested under `config=` (a
        # RunnableConfig), so the call site does `**cfg` → `config={...}`.
        return {
            "config": {
                "callbacks": [_HANDLER],
                "metadata": {
                    "langfuse_session_id": session.conversation_id,
                    "langfuse_user_id": session.lead_id or "anonymous",
                    "langfuse_tags": [session.channel, *(tags or [])],
                    # Plain metadata — searchable/filterable in the Langfuse UI.
                    "channel": session.channel,
                    "lead_status": session.lead_status,
                    "language": session.language,
                    "call_id": session.call_id,
                },
            }
        }
    except Exception as e:  # noqa: BLE001
        # Never let a tracing problem touch a live conversation.
        log.warning("[tracing] config build failed — running this turn untraced", err=str(e)[:200])
        return {}
