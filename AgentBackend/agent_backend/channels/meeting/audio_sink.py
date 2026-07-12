"""InputAudioSink — drop raw room audio after STT has consumed it
(meeting channel, fully self-contained).

Why this exists
---------------
The LiveKit input transport delivers every participant's microphone audio as
`UserAudioRawFrame`s (a subclass of `InputAudioRawFrame`). The STT stage (the
per-speaker `MeetingSTTRouter`, or the single shared recognizer) consumes that
audio and deliberately passes it through — but once STT has it, those frames
have no further purpose downstream. Left unchecked they keep flowing through
the barge-in manager → bridge → sentence streamer → TTS → Simli → the output
transport: with N participants that's N × ~100 frames/second hammering every
processor's async queue, which pressures the event loop the WebRTC media is
paced on — a direct cause of choppy agent audio. The avatar_video channel drops
these frames right after STT for exactly this reason (its InputAudioSink); this
is the meeting channel's own copy of that fix.

Placing this sink immediately AFTER the STT stage lets STT consume the audio,
then drops the raw frames so nothing downstream ever sees them. All other
frames (TranscriptionFrame, VAD frames, control frames, …) pass through
untouched. The barge-in manager's acoustic RMS gate degrades gracefully without
raw audio (see barge_classifier.acoustic_gate_passed) — same contract as the
avatar channel.
"""

from __future__ import annotations

from pipecat.frames.frames import Frame, InputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class InputAudioSink(FrameProcessor):
    """Pass everything through except InputAudioRawFrame (incl. the LiveKit
    per-participant UserAudioRawFrame subclass), which it drops."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            # STT (upstream of this sink) already consumed it. Drop it so it
            # never reaches the barge manager / bridge / TTS / Simli / output.
            return
        await self.push_frame(frame, direction)


__all__ = ["InputAudioSink"]
