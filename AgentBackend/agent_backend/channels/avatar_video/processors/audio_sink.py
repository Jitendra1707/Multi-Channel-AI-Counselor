"""InputAudioSink — drop raw mic audio after STT has consumed it.

Why this exists
---------------
The SmallWebRTC input transport must run with `audio_in_passthrough=True` so the
microphone audio reaches the STT service (STT is a downstream FrameProcessor that
only receives audio via the passthrough push). But once STT has the audio, those
`InputAudioRawFrame`s have no further purpose — they would otherwise continue
flowing downstream (AgentBridge → TTS → Simli → output transport).

That causes two problems if left unchecked:
  1. During the ~2-5s Simli connect window, the output transport hasn't processed
     StartFrame yet, so it logs "Trying to process InputAudioRawFrame but StartFrame
     not received yet" for every mic frame (cosmetic but noisy).
  2. Pushing the user's own mic audio toward the output transport is pointless —
     the avatar's audio comes from Simli, not the user's microphone echoed back.

Placing this sink immediately AFTER make_stt() lets STT consume the audio, then
drops the InputAudioRawFrame so nothing downstream ever sees it. All other frames
(TranscriptionFrame, control frames, etc.) pass through untouched.
"""

from __future__ import annotations

from pipecat.frames.frames import Frame, InputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class InputAudioSink(FrameProcessor):
    """Pass everything through except InputAudioRawFrame, which it drops."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            # STT (upstream of this sink) already consumed it. Drop it so it
            # never reaches AgentBridge / TTS / Simli / the output transport.
            return
        await self.push_frame(frame, direction)
