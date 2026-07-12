"""System-prompt composition — persona/channel-aware dispatch.

`build_system_prompt(...)` is the one public entry. It routes to the right
per-persona prompt builder by channel:

  - avatar_video  → director-briefing presenter prompt (prompts/director.py),
                    with directives sourced from the persona JSON.
  - meeting       → meeting co-pilot prompt (prompts/meeting.py), a passive
                    in-meeting expert with its OWN inline directives + identity
                    (no persona JSON — identity lives in the prompt module).
  - everything else (voice / whatsapp / chat / pipecat / email)
                    → the counsellor prompt (prompts/system.py).

Each builder owns its OWN directives + output style, so the director never
inherits counsellor rules and vice-versa. The caller (agent._compose_system_
prompt) passes a superset of slots; each builder uses the ones it needs and
ignores the rest.
"""
from __future__ import annotations

from typing import Any

from agent_backend.llm_agent.prompts.system import build_system_prompt as _build_counsellor_prompt


def build_system_prompt(*, channel: str, session: Any, **slots: Any) -> str:
    """Dispatch to the persona-appropriate prompt builder.

    avatar_video is the director-briefing presenter; every other channel is the
    counsellor. Each builder pulls its own behaviour (director's from the persona
    JSON, counsellor's from the static directives in system.py)."""
    if channel == "avatar_video":
        from agent_backend.llm_agent.identity import get_identity
        from agent_backend.llm_agent.prompts.director import build_director_prompt

        # Resolve the director persona dict so the builder can read its
        # system_prompt.directives. Channel→persona name lives in agent.py, but
        # avatar_video always maps to the avatar identity, so resolve it here via
        # settings to keep this module self-contained.
        from agent_backend.config import get_settings

        identity = get_identity(get_settings().avatar_identity_name)
        return build_director_prompt(
            session=session, identity=identity, channel=channel, **slots
        )

    if channel == "meeting":
        # Meeting co-pilot: a passive in-meeting expert. Its directives + identity
        # are INLINE in prompts/meeting.py (no persona JSON), so the counsellor
        # slots the caller also passes (playbook / lead profile / conversation
        # state / counsellor persona) are simply ignored by that builder.
        from agent_backend.llm_agent.prompts.meeting import build_meeting_prompt

        return build_meeting_prompt(session=session, **slots)

    return _build_counsellor_prompt(channel=channel, session=session, **slots)


__all__ = ["build_system_prompt"]
