"""Avatar-video pipeline processors.

AgentSimliVideoService is our 2.0.x-compatible replacement for
pipecat's SimliVideoService (which targets the retired simli-ai 0.1.x API).
Same FrameProcessor interface; uses simli-ai >= 2.0.0 under the hood.
"""
from agent_backend.channels.avatar_video.processors.audio_sink import InputAudioSink
from agent_backend.channels.avatar_video.processors.av_sync import (
    enable_timestamp_passthrough,
    pace_and_resync_video_track,
)
from agent_backend.channels.avatar_video.processors.simli_service import (
    AgentSimliVideoService,
)
from agent_backend.channels.avatar_video.processors.soulx_service import (
    AgentSoulXVideoService,
)

__all__ = [
    "AgentSimliVideoService",
    "AgentSoulXVideoService",
    "InputAudioSink",
    "enable_timestamp_passthrough",
    "pace_and_resync_video_track",
]
