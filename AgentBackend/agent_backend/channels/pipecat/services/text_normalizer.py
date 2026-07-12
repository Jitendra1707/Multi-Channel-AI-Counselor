"""Spoken-text normalizer — a Pipecat text filter that rewrites symbols and
numbers into what they should SOUND like, before TTS synthesis.

Why deterministic (not a prompt rule): the LLM is unreliable at expanding these
(it emitted 'B.Tech', '8.9 LPA', '6-15 LPA'), and Azure then reads them oddly —
the dot in 'B.Tech' is spoken, '8.9' becomes '8 … 9 paise', '6-15' becomes
'6 <pause> 15'. This filter runs on the FULLY-aggregated sentence inside
TTSService._push_tts_frames (so multi-token numbers/ranges are intact) and emits
PLAIN WORDS — it must NOT emit SSML, because AzureTTSService escapes the text
into SSML afterwards (any tags would be read aloud).

Script-safe: every pattern is anchored on ASCII letters/digits/symbols, so
Devanagari / Telugu / Tamil text (and Indic digits) passes through untouched —
only the embedded English abbreviations, numbers and symbols are rewritten. That
makes it correct for the code-mixed replies (Hindi sentence + English terms).

Transformations (in order):
    ₹/Rs before a number   → 'rupees '          ₹4,50,000 → rupees 4,50,000
    &                      → ' and '            AI & ML   → AI and ML
    letter '.' letter      → 'letter letter'    B.Tech    → B Tech ; Ph.D → Ph D
    digit '.' digit        → 'digit point digit' 8.9      → 8 point 9
    digit '-' digit        → 'digit to digit'    6-15     → 6 to 15
    '/' between alphanum   → ' '                 AI/ML    → AI ML
Sentence-ending periods (followed by a space) are left alone, so normal
punctuation/prosody is unaffected.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from pipecat.utils.text.base_text_filter import BaseTextFilter

# Currency markers immediately before a number → 'rupees ' (also de-glues
# 'Rs8.9' → 'rupees 8.9', which is what made Azure read it as paise).
_RE_RUPEE_SYM = re.compile(r"₹\s*(?=\d)")
_RE_RS = re.compile(r"(?i)\bRs\.?\s*(?=\d)")
# Ampersand → spoken 'and'.
_RE_AMP = re.compile(r"\s*&\s*")
# A dot tightly BETWEEN letters = an abbreviation (B.Tech, Ph.D, U.S.A).
# Requires a letter immediately after the dot, so sentence-final periods
# (always followed by a space/end) are never touched.
_RE_ABBR_DOT = re.compile(r"(?<=[A-Za-z])\.(?=[A-Za-z])")
# Decimal number → 'X point Y' (8.9 → 8 point 9). Digit-anchored, so it
# never collides with the abbreviation rule above.
_RE_DECIMAL = re.compile(r"(\d)\.(\d)")
# Numeric range with hyphen/en-dash/em-dash → 'X to Y' (6-15 → 6 to 15).
_RE_RANGE = re.compile(r"(\d)\s*[-–—]\s*(?=\d)")
# Slash between alphanumerics → space (AI/ML → AI ML), so '/' isn't spoken.
_RE_SLASH = re.compile(r"(?<=[A-Za-z0-9])\s*/\s*(?=[A-Za-z0-9])")
# Collapse any runs of spaces/tabs the rewrites introduced.
_RE_WS = re.compile(r"[ \t]{2,}")


def normalize_spoken_text(text: str) -> str:
    """Rewrite symbols/numbers in `text` to their spoken form. Pure + sync, so
    it can run BOTH as a TTS text filter (below) AND earlier — e.g. inside the
    SentenceStreamer — where the abbreviation dot in 'B.Tech' is still intact
    (before the TTS sentence-aggregator can flush a lone 'B.' and lose it).
    Never raises — returns the original text on any error."""
    if not text:
        return text
    try:
        t = _RE_RUPEE_SYM.sub("rupees ", text)
        t = _RE_RS.sub("rupees ", t)
        t = _RE_AMP.sub(" and ", t)
        t = _RE_ABBR_DOT.sub(" ", t)
        t = _RE_DECIMAL.sub(r"\1 point \2", t)
        t = _RE_RANGE.sub(r"\1 to ", t)
        t = _RE_SLASH.sub(" ", t)
        t = _RE_WS.sub(" ", t)
        return t
    except Exception:  # noqa: BLE001 — never let normalization break the pipeline
        return text


class SpokenTextNormalizer(BaseTextFilter):
    """Rewrites symbols/numbers to their spoken form for any TTS provider."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._enabled = True

    async def update_settings(self, settings: Mapping[str, Any]) -> None:
        if "enable_text_filter" in settings:
            self._enabled = bool(settings["enable_text_filter"])

    async def filter(self, text: str) -> str:
        if not self._enabled or not text:
            return text
        return normalize_spoken_text(text)

    async def handle_interruption(self) -> None:
        # Stateless filter — nothing to reset.
        pass

    async def reset_interruption(self) -> None:
        pass


__all__ = ["SpokenTextNormalizer", "normalize_spoken_text"]
