"""SentenceStreamer — coalesces brain tokens into sentence-sized TextFrames.

What it solves
--------------
The AgentBridge currently pushes one TextFrame per token. Pipecat's TTS
service synthesises as soon as it has enough text, but token-by-token
TextFrames cause:

  - Choppy synthesis boundary decisions in the TTS service
  - More HTTP round-trips for HTTP-based TTS variants
  - Worse prosody (each chunk synthesised in isolation)

This processor buffers tokens until a sentence-end punctuation, then
forwards ONE TextFrame for the whole sentence. The TTS service treats it
as a single utterance, prosody is better, audio start latency is the same
(first sentence ships before subsequent ones are buffered).

Where to insert
---------------
BETWEEN AgentBridge and TTS — composer.py places it there when the
ENABLE_STREAMING_OPTIMIZATIONS flag is on.

Edge cases handled
------------------
- Numbers like "12.5" don't split (regex requires whitespace OR end-of-text
  after the punctuation).
- Quotation marks AFTER punctuation are part of the sentence ("He said hi!").
- A final LLMFullResponseEndFrame flushes any remaining tail as one TextFrame
  so the last sentence is never lost.
- Empty buffers are skipped — never emit zero-length TextFrames.

Backwards-compatibility
-----------------------
With ENABLE_STREAMING_OPTIMIZATIONS=False this processor isn't inserted at
all. The token-per-TextFrame path remains unchanged.
"""
from __future__ import annotations

import re

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from agent_backend.channels.pipecat.services.text_normalizer import normalize_spoken_text
from agent_backend.infra import get_logger

log = get_logger(__name__)


# Sentence end: . ! ? optionally followed by ) " ' ] and then whitespace OR end.
# We require the punctuation to be followed by whitespace so "12.5" doesn't
# split on the dot.
_SENTENCE_END = re.compile(r"([.!?][\"')\]]*\s+)")


class SentenceStreamer(FrameProcessor):
    """Buffer brain tokens; emit one TextFrame per complete sentence."""

    def __init__(self) -> None:
        super().__init__()
        self._buf: str = ""
        self._inside_response: bool = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        # Brain started — start buffering, forward the begin frame.
        if isinstance(frame, LLMFullResponseStartFrame):
            self._buf = ""
            self._inside_response = True
            await self.push_frame(frame, direction)
            return

        # Brain ended — flush any remaining tail, then forward the end frame.
        if isinstance(frame, LLMFullResponseEndFrame):
            await self._flush_remainder(direction)
            self._inside_response = False
            await self.push_frame(frame, direction)
            return

        # Token from the brain — accumulate; emit complete sentences.
        if isinstance(frame, TextFrame) and self._inside_response:
            self._buf += frame.text or ""
            await self._drain_complete_sentences(direction)
            return

        # Everything else passes through unchanged.
        await self.push_frame(frame, direction)

    async def _drain_complete_sentences(self, direction: FrameDirection) -> None:
        """Split buffer on sentence-end punctuation; push each completed
        sentence as a TextFrame and keep the trailing remainder."""
        parts = _SENTENCE_END.split(self._buf)
        # `re.split` with a capture group: [text, delim, text, delim, ..., tail]
        i = 0
        sentences: list[str] = []
        while i + 1 < len(parts):
            sentence = (parts[i] + parts[i + 1]).strip()
            if sentence:
                sentences.append(sentence)
            i += 2
        self._buf = parts[i] if i < len(parts) else ""

        for s in sentences:
            # Normalise the WHOLE sentence here — 'B.Tech' is still intact at
            # this point (the streamer never splits on a dot without a trailing
            # space), so the abbreviation dot is removed BEFORE the TTS sentence-
            # aggregator can flush a lone 'B.' and lose it to a "B <pause> Tech".
            norm = normalize_spoken_text(s)
            if norm != s:
                # Observability: confirms the spoken text differs from the raw
                # brain output (which is what the agent-bridge logs). If you see
                # 'B.Tech' in [BOT] but 'B Tech' here, normalization is working.
                log.info("[normalize] tts-text", before=s[:90], after=norm[:90])
            await self.push_frame(TextFrame(text=norm + " "), direction)

    async def _flush_remainder(self, direction: FrameDirection) -> None:
        """Emit whatever's left at end-of-stream as one final TextFrame."""
        tail = self._buf.strip()
        if tail:
            await self.push_frame(TextFrame(text=normalize_spoken_text(tail)), direction)
        self._buf = ""


__all__ = ["SentenceStreamer"]
