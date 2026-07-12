"""Shared types for tool modules.

Every tool file under `tools/**/*.py` exposes a factory:

    def build_tools(ctx: ToolContext) -> list[BaseTool]: ...

The `ToolContext` carries the per-turn handles a tool might want to
close over. Kept minimal: just the session. Tools that need episodic
memory import `get_episodic_store` directly.

The discovery walker in `tools/__init__.py` calls every `build_tools`
on each turn, so the cost of building context is per-turn — cheap.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_backend.llm_agent.session import Session


@dataclass(frozen=True)
class ToolContext:
    """Per-turn handle passed to each tool factory.

    Attributes:
        session: The active Session. Tool factories close over it so
            calls back into Node (if they need session-scoped routing
            keys) and writes to per-conversation memory land in the
            right bucket without each tool re-resolving it.
    """

    session: Session


__all__ = ["ToolContext"]
