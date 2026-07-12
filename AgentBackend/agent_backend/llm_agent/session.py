"""Session dataclass — the contract every channel passes to the agent.

Channel-agnostic. The brain reads `channel` to pick the OUTPUT STYLE block
and (for the counselor/voice family) reads `lead_id` to load lead state.

Backwards-compat: `lead_id`, `lead_status`, `language`, `call_id` are all
optional, so the call sites (email, avatar_video) that don't have a lead keep
working unchanged — the brain just skips the lead-specific slots in that case.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Every channel shares one brain, dispatched on this literal. It's only a type
# hint — the runtime never enforces it — but keeping the union complete keeps
# type-checking honest across all call surfaces.
Channel = Literal[
    "email",          # email reply
    "voice",          # real PSTN phone call to the lead's mobile
    "whatsapp",       # WhatsApp text
    "avatar_video",   # browser-rendered avatar video room
    "meeting",        # LiveKit room: human counsellor + candidate + listening agent
]

# The counselor product's channels. The brain dispatches on membership here:
# these channels get the counselor path (leads/university/RAG/conversation
# memory/playbook, voice tools, counselor persona); every other channel gets
# the avatar-video (director) path. Single source of truth — imported by
# prompts/system.py, agent.py, tools/__init__.py.
VOICE_FAMILY: frozenset[str] = frozenset({"voice", "whatsapp", "avatar_video"})


@dataclass(frozen=True)
class Session:
    """One per active turn / connection.

    Required:
        channel:         which mouth is speaking.
        conversation_id: opaque per-conversation handle (uuid). Used for log
                         correlation and as the brain/graph cache key.

    Optional (counselor/voice family):
        lead_id:         the natural key for the candidate. When set, the
                         brain loads the Lead via LeadRepo and renders
                         LEAD PROFILE + status playbook into the prompt.
        lead_status:     funnel stage. Pre-loaded so the brain doesn't need
                         a round-trip for the status alone. If omitted, the
                         brain reads it off the Lead.
        language:        2-letter code: 'en' | 'hi' | 'ta' | 'te' | ...
                         Drives STT/TTS in voice; persona tone otherwise.
        display_name:    free-form label (sender name, etc.). When `lead_id`
                         is set, the lead's full_name
                         wins.
        call_id:         telephony-provider call id (ACS callConnectionId).
                         Set on voice channels so tools like `end_call` can
                         reach the carrier to hang up.
        direction:       "outbound" (we dialed the candidate) or "inbound" (the
                         candidate called us). Drives the opener + first-turn
                         prompt: inbound greets "thanks for calling — how can I
                         help?" instead of the outbound "got a minute?". Defaults
                         to "outbound" so every existing call site (ACS, email,
                         avatar, meeting) is unchanged.
    """

    channel: Channel
    conversation_id: str
    lead_id: str | None = None
    lead_status: str | None = None
    language: str = "en"
    display_name: str | None = None
    call_id: str | None = None
    direction: Literal["inbound", "outbound"] = "outbound"

    def short(self) -> str:
        """Compact representation for log lines."""
        bits = [self.channel, self.conversation_id[:8]]
        if self.lead_id:
            bits.append(f"lead={self.lead_id[:8]}")
        return ":".join(bits)
