"""Meeting-channel pipeline processors — the avatar renderers.

ISOLATION NOTE
--------------
This is the meeting channel's OWN copy of the avatar render services
(AgentSoulXVideoService / AgentSimliVideoService) and their helpers (av_sync),
deliberately duplicated from `channels/avatar_video/processors/` so the meeting
channel has ZERO dependency on the avatar_video channel. The two channels can
now evolve their renderers independently — a change to one never affects the
other. (The shared-renderer coupling, and a shared SoulX GPU session id, are
exactly what caused cross-channel bugs earlier; full duplication removes that.)

Each SoulX session uses a UNIQUE GPU `session_id` (meeting passes `meeting-<room>`)
so concurrent meeting/avatar sessions never collide on the same render session.
"""
from agent_backend.channels.meeting.processors.av_sync import (
    enable_timestamp_passthrough,
    pace_and_resync_video_track,
)
from agent_backend.channels.meeting.processors.simli_service import (
    AgentSimliVideoService,
)
from agent_backend.channels.meeting.processors.soulx_service import (
    AgentSoulXVideoService,
)

__all__ = [
    "AgentSimliVideoService",
    "AgentSoulXVideoService",
    "enable_timestamp_passthrough",
    "pace_and_resync_video_track",
]
