"""SentenceStreamer — coalesce brain tokens into sentence-sized TextFrames
(meeting channel, fully isolated).

ISOLATION NOTE
--------------
Self-contained copy for the meeting channel. Imports nothing from
`channels.voice` / `channels.avatar_video` / `channels.pipecat` — same
isolation contract as the rest of `channels/meeting`.

What it solves
--------------
The MeetingAgentBridge pushes one TextFrame per brain token straight into TTS.
Token-by-token TextFrames cause choppy TTS synthesis boundaries, more HTTP
round-trips, and worse prosody — and, with the SoulX/Simli avatar on, worse
lip-sync (the renderer lip-syncs per audio chunk; micro-chunks animate badly).
This buffers tokens until sentence-end punctuation, then forwards ONE TextFrame
per sentence — exactly what the avatar_video channel does (its docstring:
"whole-sentence chunks are smoother" lip-sync).

LATENCY
-------
The FIRST sentence still ships the moment it completes, so first-audio latency
is unchanged — downstream just makes fewer, cleaner TTS + renderer calls.

Insert BETWEEN the bridge and TTS (pipeline.py places it there when
MEETING_SENTENCE_STREAMING is on).

Interruption safety
-------------------
On a barge-in the bridge cancels the brain and pushes InterruptionFrame (and the
framework pushes StartInterruptionFrame). This processor DROPS its buffered
partial sentence on either — otherwise the cancelled brain's trailing
LLMFullResponseEndFrame would flush that stale half-sentence to TTS, making the
agent speak a fragment of the OLD reply right after the interruption. Dropping it
keeps the "stop fast" guarantee intact.
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

        # INTERRUPTION → DROP the buffered partial sentence immediately so a
        # confirmed barge-in can't leave a half-sentence that the cancelled
        # brain's LLMFullResponseEndFrame would later flush to TTS. Forward the
        # frame so TTS + the avatar still act on it.
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
