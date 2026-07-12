"""Auto-discovery + registration for tools.

Layout convention:

    tools/
      _base.py       — ToolContext (private — leading underscore)
      voice/         — counsellor tools (end_call, knowledge_base, ...)
        __init__.py  — folder marker (skipped by discovery)
        end_call.py  — one tool per file
        ...
      director/      — avatar_video director-briefing tools
      <future>/      — additional tool families

Each leaf .py exposes a factory:

    def build_tools(ctx: ToolContext) -> list[BaseTool]: ...

`_discover()` walks the tree once at module import time, collecting
factories. `build_all_tools(session)` then materialises the actual
`BaseTool` list for one turn by calling every factory with a fresh
`ToolContext`. Folder `__init__.py` files and files starting with `_`
are skipped — convention matches LLmLayer so the pattern is
recognisable across projects.

Failure modes are graceful:
  - Import of a tool module fails → log a warning, skip it. Service
    stays up. Other tools still work.
  - Factory raises at build time → log + skip that tool, others still
    register.

Why this pattern:
  - Adding a new tool is one file. No registry list to update, no
    central wiring to touch. Drop a .py with a `build_tools`
    function and it's in.
  - The walker is one-shot at import — discovery cost doesn't repeat
    per turn.
  - Tests can import individual tool modules and call `build_tools`
    in isolation.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Callable

from langchain_core.tools import BaseTool

from agent_backend.infra import get_logger
from agent_backend.llm_agent.session import Session
from agent_backend.llm_agent.tools._base import ToolContext

log = get_logger(__name__)


ToolFactory = Callable[[ToolContext], list[BaseTool]]

# Channel family → tool group (the first dotted part of a tool module's path,
# e.g. "voice.end_call" → "voice"). Discovery finds ALL tools once at import;
# `build_all_tools` then offers only the group that matches the session's
# channel, so counsellor tools never reach the wrong surface.
#
# The LiveKit `meeting` channel gets its OWN group ("meeting"): there are no
# meeting tool files yet, so the agent runs as a pure listener/answerer with an
# empty tool surface. Critically this keeps the Teams meeting-control tools
# (join/leave/share — all Playwright-Teams-bridge specific) away from a LiveKit
# room where they'd be meaningless. Drop a file under tools/meeting/ later to
# add a meeting-scoped tool (e.g. flag_concern, summarize_so_far) — no wiring.
#
# avatar_video is a SEPARATE group ("director"): it's the director-briefing
# analytics presenter, NOT the counsellor — so it gets the `director/` tools
# (e.g. present_analytics) and NONE of the voice/whatsapp counsellor tools
# (search_knowledge_base, send_document, ...). This is the channel-level
# repurpose: persona AND tool surface both switch on the channel.
def _group_for_channel(channel: str) -> str:
    if channel == "meeting":
        return "meeting"
    if channel == "avatar_video":
        return "director"
    return "voice"


# Cross-group tools offered regardless of the channel's primary group. Each must
# self-gate internally by channel. `end_call` (voice group) is shared with the
# avatar_video/director channel so the avatar can still hang up / end on a silence
# timeout. `search_knowledge_base` (voice group) is shared with avatar_video too,
# because the avatar is a HYBRID — it answers general university questions from the
# knowledge base in addition to briefing analytics; it self-gates to the counsellor
# channels + avatar_video. Module paths as registered by _discover().
_SHARED_TOOLS: frozenset[str] = frozenset({"voice.end_call", "voice.knowledge_base"})

# Populated at import time by _discover(). Cleared + repopulated on
# re-import (matters for hot-reload dev workflows; not for prod).
_factories: list[tuple[str, ToolFactory]] = []


def _discover() -> None:
    """Walk tools/**/*.py and collect every `build_tools` factory.

    Idempotent — clears the registry first so re-imports (rare, dev
    workflows like uvicorn --reload) don't accumulate duplicates.
    """
    _factories.clear()
    pkg_root = Path(__file__).parent
    for py in sorted(pkg_root.rglob("*.py")):
        # Skip package markers, private modules, and this file.
        if py.name.startswith("_") or py.name == "__init__.py":
            continue
        rel = py.relative_to(pkg_root).with_suffix("")
        # Skip anything under a private directory (a part starting with `_`).
        # `tools/_legacy_*/` retires tools without deleting them — same effect
        # as removing them from discovery.
        if any(part.startswith("_") for part in rel.parts):
            continue
        module_name = "agent_backend.llm_agent.tools." + ".".join(rel.parts)
        try:
            mod = importlib.import_module(module_name)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "[tools] module failed to import",
                module=module_name,
                err=str(e),
            )
            continue
        factory = getattr(mod, "build_tools", None)
        if not callable(factory):
            log.warning(
                "[tools] module missing build_tools(ctx) factory",
                module=module_name,
            )
            continue
        _factories.append((".".join(rel.parts), factory))
        log.debug("[tools] registered", module=".".join(rel.parts))


def build_all_tools(session: Session) -> list[BaseTool]:
    """Materialise the tool list for one turn under the given session.

    Called from `agent._get_graph(session)` when a session's first
    turn lands. The resulting list is passed to LangGraph's
    `create_react_agent(tools=...)` so the LLM sees the real tool
    surface in its prompt and can call any of them.
    """
    ctx = ToolContext(session=session)
    group = _group_for_channel(session.channel)
    all_tools: list[BaseTool] = []
    for name, factory in _factories:
        # Only offer tools whose group matches the session's channel family,
        # PLUS any cross-group shared tools. `end_call` lives in the voice group
        # but is also needed by the avatar_video (director) channel — it self-
        # gates internally to {voice, avatar_video}, so offering it to the
        # director group is safe and is what keeps the avatar's silence-timeout /
        # goodbye teardown working after the channel repurpose.
        if name.split(".", 1)[0] != group and name not in _SHARED_TOOLS:
            continue
        try:
            all_tools.extend(factory(ctx))
        except Exception as e:  # noqa: BLE001
            log.warning("[tools] factory raised", module=name, err=str(e))
    # One-line audit so the operator can confirm tools are reaching
    # the LLM. Without it, a missing tool call is ambiguous (discovery
    # problem? prompt problem?).
    log.info(
        "[tools] built for session",
        session=session.short(),
        count=len(all_tools),
        names=sorted({t.name for t in all_tools}),
    )
    return all_tools


def list_registered() -> list[str]:
    """Names of every discovered module (debug / /health endpoint)."""
    return [name for name, _ in _factories]


_discover()
