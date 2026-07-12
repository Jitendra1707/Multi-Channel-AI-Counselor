"""Minimal context types for the web_extractor tools.

The original web_extractor (from AegisBackend) closed over an `aegis_backend`
`Session` + `ToolContext`. In BusinessLayer we have no agent/session machinery,
so this module provides the tiny equivalents the tools actually need: a session
that only carries a `conversation_id`, wrapped in a `ToolContext`. This keeps
the tool/navigator code unchanged while removing the dependency on AegisBackend.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NavSession:
    """The only field the browser tools read off a session is conversation_id —
    it keys the per-session Playwright BrowserContext/Page in BrowserManager."""

    conversation_id: str


@dataclass(frozen=True)
class ToolContext:
    """Per-run handle passed to `build_tools`. Mirrors the original contract:
    tools do `ctx.session.conversation_id`."""

    session: NavSession


__all__ = ["NavSession", "ToolContext"]
