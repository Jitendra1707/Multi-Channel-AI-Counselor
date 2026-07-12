"""BargeClassifier — five-intent classification of a barge-in during a MEETING.

ISOLATION NOTE
--------------
Self-contained copy for the meeting channel. Imports nothing from
`channels.voice` / `channels.avatar_video` / `channels.pipecat`. Editing meeting
barge behaviour here never touches the other channels (and vice-versa) — each
channel owns its own human-simulation stack, by design.

Five intents (identical semantics to the proven voice/avatar decision tree):

    ACK        — listener noise / acknowledgement (yes, mhm, ah, okay).
                 Agent keeps speaking. Transcript recorded so the brain knows
                 the user is engaged.
    ANSWER     — short ack that answers a question the agent just asked.
                 Agent keeps speaking (don't cancel mid-question); transcript
                 recorded so the brain sees the answer next turn.
    INTERRUPT  — real interruption with new content. Cancel TTS+Simli, run brain.
    CONFUSED   — user is lost ("huh?", "what?", "kya bola?"). Cancel + re-explain.
    AMBIGUOUS  — heuristic can't decide. Manager keeps the agent speaking and
                 re-classifies on the FULL final transcript. No async LLM on the
                 critical path — latency first.

Architectural rules (same as the other channels):
  1. No latency on the critical path — heuristic is <5 ms, decides 90%+ of cases,
     NO LLM call here; the manager waits for the full final on AMBIGUOUS.
  2. No trial-and-error vocab tuning — a decision tree over signals we already
     have: STT transcript, conversation history, whether the agent's last line
     ended with '?', and duration.
  3. Context aware — "yes" to a yes/no question is ANSWER, not ACK; a lone
     "what?" is CONFUSED, "what about fees" is a question → INTERRUPT.

MEETING ACOUSTIC GATE
---------------------
Like avatar_video, the meeting agent's input audio goes through browser/SFU AEC
and per-frame input RMS is generally unavailable here, so the echo-ratio check
degrades gracefully: when no reliable RMS is present it relies on duration + the
manager's bot-speaking grace window + the transcript heuristic instead.
"""
from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from agent_backend.infra import get_logger

if TYPE_CHECKING:
    from agent_backend.llm_agent.session import Session

log = get_logger(__name__)


class BargeIntent(StrEnum):
    ACK = "ack"
    ANSWER = "answer"
    INTERRUPT = "interrupt"
    CONFUSED = "confused"
    AMBIGUOUS = "ambiguous"


# ---------------------------------------------------------------------------
# Vocab — kept SMALL and PURPOSEFUL. These encode the SEMANTICS of each
# category, not exhaustive lists. A word not here lands in AMBIGUOUS and the
# manager re-decides on the full final — that's the design.
# ---------------------------------------------------------------------------

# Pure listener noises + short affirmatives. ACK by default; ANSWER if the
# agent's last utterance ended with '?'.
_ACK_WORDS = frozenset({
    "yes", "yeah", "ya", "yep", "yup", "yah", "yeh",
    "ok", "okay", "k", "kay",
    "right", "sure", "alright", "all right", "fine", "good", "great",
    "ah", "ahh", "aha", "aah", "oh", "ohh", "ooh", "ha", "haa",
    "hmm", "hmmm", "mmm", "mhm", "mm", "uh-huh", "uhuh", "uh huh",
    "uh", "er", "erm",
    "got it", "i see", "i understand", "makes sense",
    # Hinglish / Hindi
    "ji", "ji ji", "haan", "haan ji", "haa ji", "han",
    "achha", "acha", "accha",
})

# Words that signal "stop, I want to break in". Always INTERRUPT.
_INTERRUPT_WORDS = frozenset({
    "no", "nope", "nah",
    "wait", "stop", "hold on", "hold", "hang on",
    "actually", "but",
    # Hindi
    "ruko", "ek minute",
})

# Single-word confusion signals — CONFUSED only when said ALONE (so
# "what about fees" → INTERRUPT, not CONFUSED).
_CONFUSED_SINGLES = frozenset({
    "huh", "what", "sorry", "repeat", "again", "pardon",
    "kya",
})

# Multi-word confusion phrases (substring match against normalized text).
_CONFUSED_PHRASES = frozenset({
    "say again", "come again", "what did you say",
    "didn't catch", "didnt catch", "didn't get", "didnt get",
    "didn't hear", "didnt hear",
    "kya bola", "kya kaha", "samjha nahi", "samjhe nahi",
    "phir se", "ek baar phir", "again please",
})

# Question / info-request leads. A multi-word utterance carrying one of these
# (or ending in "?") is a REAL question the user wants answered → INTERRUPT.
_QUESTION_WORDS = frozenset({
    "what", "how", "where", "when", "why", "which", "who", "whom", "whose",
    "can", "could", "would", "will", "do", "does", "did", "is", "are", "should",
    "tell", "explain", "give", "share", "send", "list",
    "kya", "kaise", "kahan", "kab", "kaun", "kitna", "kitne", "kyun", "batao", "bataye",
})


class BargeClassifier:
    """Pure decision class — no Pipecat dependency. Used by BargeInManager."""

    def __init__(self, *, session: "Session", conversation_id: str) -> None:
        self._session = session
        self._conversation_id = conversation_id

    # ------------------------------------------------------------------
    # Phase 1 — acoustic gate. Called when VAD trips, before we have a
    # transcript. Rejects obvious noise/glitches without ever cancelling TTS.
    #
    # MEETING ADAPTATION: input RMS is often unavailable (browser/SFU AEC), so
    # when `user_rms` / `tts_peak_rms` are 0 we SKIP the echo-ratio check and
    # rely on duration only — the transcript phase + grace window catch intent.
    # ------------------------------------------------------------------
    def acoustic_gate_passed(
        self,
        *,
        user_rms: float,
        tts_peak_rms: float,
        user_duration_ms: int,
    ) -> bool:
        """True = could be real user speech; proceed to transcript check."""
        if user_duration_ms < 300:
            return False
        if user_rms > 0 and tts_peak_rms > 50:
            ratio = user_rms / max(tts_peak_rms, 1.0)
            if ratio < 0.35:
                return False
        return True

    # ------------------------------------------------------------------
    # Phase 2 — heuristic decision with full transcript + context.
    # ------------------------------------------------------------------
    def classify(
        self,
        *,
        transcript: str,
        user_duration_ms: int,
    ) -> BargeIntent:
        """Sub-5 ms decision. Returns AMBIGUOUS if all rules miss."""
        text = (transcript or "").strip()
        norm = text.lower().rstrip("?.!,'\"")

        if not norm and user_duration_ms < 700:
            return BargeIntent.ACK

        words = norm.split()
        n = len(words)

        # === All-ack repetition (any length) → ACK / ANSWER ===
        if n >= 2 and all(w in _ACK_WORDS for w in words):
            return self._ack_or_answer()

        # === Confused PHRASES (substring) → CONFUSED ===
        if n >= 2 and any(phrase in norm for phrase in _CONFUSED_PHRASES):
            return BargeIntent.CONFUSED

        # === Long content (5+ words) → INTERRUPT ===
        if n >= 5:
            return BargeIntent.INTERRUPT

        # === 3-4 words with a clear INTERRUPT signal → INTERRUPT ===
        if n >= 3 and any(w in _INTERRUPT_WORDS for w in words):
            return BargeIntent.INTERRUPT

        # === Single confused word → CONFUSED ===
        if n == 1 and norm in _CONFUSED_SINGLES:
            return BargeIntent.CONFUSED

        # === Single interrupt word → INTERRUPT ===
        if n == 1 and norm in _INTERRUPT_WORDS:
            return BargeIntent.INTERRUPT

        # === Single ack word → ACK / ANSWER (context-aware) ===
        if n == 1 and norm in _ACK_WORDS:
            return self._ack_or_answer()

        # === Two-word interrupt token → INTERRUPT ===
        if n == 2 and any(w in _INTERRUPT_WORDS for w in words):
            return BargeIntent.INTERRUPT

        # === Question / info-request → INTERRUPT ===
        if n >= 2 and not all(w in _ACK_WORDS for w in words):
            if text.endswith("?") or any(w in _QUESTION_WORDS for w in words):
                return BargeIntent.INTERRUPT

        # === Everything else (short, non-ack, non-question) → AMBIGUOUS ===
        return BargeIntent.AMBIGUOUS

    # ------------------------------------------------------------------
    # Internal helpers — safe defaults on any error so a broken signal
    # never blocks the audio path.
    # ------------------------------------------------------------------
    def _ack_or_answer(self) -> BargeIntent:
        return BargeIntent.ANSWER if self._bot_asked_question() else BargeIntent.ACK

    def _bot_last_sentence(self) -> str:
        try:
            from langchain_core.messages import AIMessage

            from agent_backend.llm_agent.conversation import get_conversation
            history = get_conversation(self._conversation_id).recent(n=4)
            for msg in reversed(history):
                if isinstance(msg, AIMessage):
                    content = msg.content or ""
                    return content if isinstance(content, str) else str(content)
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _bot_asked_question(self) -> bool:
        """True only if the agent's last utterance ENDS with a question mark."""
        text = self._bot_last_sentence().rstrip()
        if not text:
            return False
        stripped = text.rstrip("\"')]").rstrip()
        return stripped.endswith("?")


__all__ = ["BargeClassifier", "BargeIntent"]
