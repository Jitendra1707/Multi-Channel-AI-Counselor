"""SentenceStreamer — coalesce brain tokens into sentence-sized TextFrames
(avatar-video channel, fully isolated).

ISOLATION NOTE
--------------
Self-contained copy for avatar_video. Imports nothing from
`channels.voice` / `channels.pipecat`.

What it solves
--------------
AgentBridge pushes one TextFrame per token. Token-by-token TextFrames cause
choppy TTS synthesis boundaries, more HTTP round-trips, and worse prosody.
This buffers tokens until sentence-end punctuation, then forwards ONE TextFrame
per sentence.

LATENCY WIN (avatar-specific)
-----------------------------
The first sentence still ships the moment it completes — so first-audio latency
is unchanged — but downstream the TTS makes fewer synthesis calls AND the Simli
service receives whole sentences, meaning fewer `simli.send()` calls and cleaner
lip-sync (Simli lip-syncs per audio chunk; whole-sentence chunks are smoother).

Insert BETWEEN AgentBridge and TTS (composer places it there when
AVATAR_ENABLE_STREAMING_OPTIMIZATIONS is on).

Interruption safety
-------------------
On a confirmed barge-in the BargeInManager fires InterruptionFrame (and the
framework fires StartInterruptionFrame). This processor DROPS its buffered
partial sentence on either — otherwise the cancelled brain task's trailing
LLMFullResponseEndFrame would flush that stale half-sentence to TTS → Simli,
making the avatar speak a fragment of the OLD reply right after the user
interrupted. Dropping it keeps the "stop fast" guarantee intact.
"""
from __future__ import annotations

import re

from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    StartInterruptionFrame,
    TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from agent_backend.infra import get_logger

log = get_logger(__name__)


# Sentence end: . ! ? optionally followed by ) " ' ] then whitespace OR end.
# Requiring trailing whitespace stops "12.5" splitting on the dot.
_SENTENCE_END = re.compile(r"([.!?][\"')\]]*\s+)")


class SentenceStreamer(FrameProcessor):
    """Buffer brain tokens; emit one TextFrame per complete sentence."""

    def __init__(self) -> None:
        super().__init__()
        self._buf: str = ""
        self._inside_response: bool = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        # INTERRUPTION → DROP the buffered partial sentence immediately.
        # Without this, a confirmed barge-in (which fires InterruptionFrame from
        # the BargeInManager, and StartInterruptionFrame from the framework)
        # would still leave a half-sentence sitting in self._buf. When the brain
        # task is cancelled its LLMFullResponseEndFrame flushes that stale tail
        # to TTS → Simli — so the avatar would speak a fragment of the OLD reply
        # AFTER the user already interrupted. Clearing the buffer here makes the
        # streamer honour "stop fast" like every other processor in the chain.
        # We forward the frame downstream so TTS + Simli still act on it.
        if isinstance(frame, (InterruptionFrame, StartInterruptionFrame)):
            self._buf = ""
            self._inside_response = False
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMFullResponseStartFrame):
            self._buf = ""
            self._inside_response = True
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            await self._flush_remainder(direction)
            self._inside_response = False
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TextFrame) and self._inside_response:
            self._buf += frame.text or ""
            await self._drain_complete_sentences(direction)
            return

        await self.push_frame(frame, direction)

    async def _drain_complete_sentences(self, direction: FrameDirection) -> None:
        parts = _SENTENCE_END.split(self._buf)
        i = 0
        sentences: list[str] = []
        while i + 1 < len(parts):
            sentence = (parts[i] + parts[i + 1]).strip()
            if sentence:
                sentences.append(sentence)
            i += 2
        self._buf = parts[i] if i < len(parts) else ""

        for s in sentences:
            await self.push_frame(TextFrame(text=s + " "), direction)

    async def _flush_remainder(self, direction: FrameDirection) -> None:
        tail = self._buf.strip()
        if tail:
            await self.push_frame(TextFrame(text=tail), direction)
        self._buf = ""


__all__ = ["SentenceStreamer"]
