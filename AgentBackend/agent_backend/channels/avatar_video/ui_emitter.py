"""UI directive emitter registry for the avatar_video channel.

The director's `present_analytics` tool needs to push a UiDirective to the
browser, but tools have no handle on the per-session WebRTC `connection`. So the
runner registers a small emitter callback here keyed by conversation_id (the
same pattern as the mute registry in agent_bridge), and the tool looks it up by
`session.conversation_id` and calls it. Best-effort: a missing/closed channel is
a silent no-op, never an error into the agent.

Envelope on the wire (data channel):  {"type": "ui_directive", "directive": {...}}
The FE parses it in web-app/src/hooks/useWebRTCAvatar.ts.
"""
from __future__ import annotations

from typing import Any, Callable

from agent_backend.infra import get_logger

log = get_logger(__name__)

# conversation_id -> emitter(directive_dict) -> None
_EMITTERS: dict[str, Callable[[dict[str, Any]], None]] = {}


def register_ui_emitter(conversation_id: str, emitter: Callable[[dict[str, Any]], None]) -> None:
    _EMITTERS[conversation_id] = emitter


def clear_ui_emitter(conversation_id: str) -> None:
    _EMITTERS.pop(conversation_id, None)


def emit_ui_directive(conversation_id: str, directive: dict[str, Any]) -> bool:
    """Push a UiDirective to the browser for this conversation. Returns True if an
    emitter was found and invoked without raising; False otherwise (channel not
    open / not an avatar session) — callers treat False as 'not shown'."""
    fn = _EMITTERS.get(conversation_id)
    if fn is None:
        return False
    try:
        fn({"type": "ui_directive", "directive": directive})
        return True
    except Exception as e:  # noqa: BLE001
        log.debug("[avatar-video] ui emit failed", conversation_id=conversation_id, err=str(e))
        return False
