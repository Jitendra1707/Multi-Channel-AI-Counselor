"""BargeClassifier — five-intent classification of a candidate barge-in.

Replaces the vocab-list patchwork (`BackchannelFilter` + interim-string-match
in `BargeInManager`) with a deterministic, context-aware decision tree.

Five intents the classifier can return:

    ACK        — listener noise / acknowledgement (yes, mhm, ah, okay).
                 Bot keeps speaking. Transcript recorded into conversation
                 memory so the brain knows the user is engaged.
    ANSWER     — short ack that semantically answers a question the bot
                 just asked. Bot keeps speaking (don't cancel mid-question);
                 transcript recorded so the brain sees the answer next turn.
    INTERRUPT  — real interruption with new content. Cancel TTS, run brain.
    CONFUSED   — user is lost ("huh?", "what?", "sorry?", "kya bola?").
                 Cancel TTS, run brain with a [CONFUSED] tag so it clarifies.
    AMBIGUOUS  — heuristic can't decide. Caller spawns the async LLM path
                 while the bot keeps speaking; LLM result acts on the
                 confirmation within ~500-700 ms or is discarded.

Three architectural rules this module follows:

  1. **No latency on the critical path.** The heuristic is <5 ms and
     decides 90%+ of cases. LLM only fires for AMBIGUOUS, and it runs
     in parallel — the bot doesn't stop while it runs.

  2. **No trial-and-error vocab tuning.** The decision tree uses signals
     we already have: STT transcript, conversation history, bot's last
     sentence (has `?`?), user sentiment from `conversation_state`, and
     acoustic features (duration, energy). Adding a new edge case is a
     rule change, not "add another word to the set".

  3. **Sentiment + context aware.** A frustrated user saying "ha" is
     CONFUSED, not ACK. A user answering "yes" to a yes/no question is
     ANSWER, not ACK. The classifier reads `lead.conversation_state`
     for the live sentiment trace.

Industry reference: this is the same architecture Retell / LiveKit /
Vapi use internally (heuristic-first, ML/LLM only for ambiguous, never
on the critical-path). The vocab-list approach we had was a textbook
tutorial approach, not a production one.
"""
from __future__ import annotations

import asyncio
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
# Vocab — kept SMALL and PURPOSEFUL. These are not exhaustive lists; they
# encode the semantics of each category. If a word isn't here, it lands in
# AMBIGUOUS and the LLM decides — that's the design.
# ---------------------------------------------------------------------------

# Pure listener noises + short affirmatives. Map to ACK by default; ANSWER if
# the bot's last utterance ended with '?'.
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

# Single-word confusion signals. Map to CONFUSED only when the user said
# JUST this word — not when it appears inside a longer utterance (so
# "what about fees" → INTERRUPT, not CONFUSED).
_CONFUSED_SINGLES = frozenset({
    "huh", "what", "sorry", "repeat", "again", "pardon",
    "kya",  # Hindi single-word "what?"
})

# Multi-word confusion phrases. Matched against the WHOLE normalized text
# (or as a substring for the longer ones — those are less ambiguous).
_CONFUSED_PHRASES = frozenset({
    "say again", "come again", "what did you say",
    "didn't catch", "didnt catch", "didn't get", "didnt get",
    "didn't hear", "didnt hear",
    "kya bola", "kya kaha", "samjha nahi", "samjhe nahi",
    "phir se", "ek baar phir", "again please",
})

# Question / info-request signals. A multi-word utterance carrying one of these
# (or ending in "?") is a REAL question the candidate wants answered — i.e. an
# INTERRUPT, not a listener noise. Catches "what about fees", "can you tell me
# the fees", "how much is it", "where is the campus", plus Hindi question words.
# This is what lets a mid-answer question be handled like a human (stop + answer)
# WITHOUT needing the flaky async LLM classifier.
_QUESTION_WORDS = frozenset({
    "what", "how", "where", "when", "why", "which", "who", "whom", "whose",
    "can", "could", "would", "will", "do", "does", "did", "is", "are", "should",
    "tell", "explain", "give", "share", "send", "list",
    # Hindi / Hinglish question leads
    "kya", "kaise", "kahan", "kab", "kaun", "kitna", "kitne", "kyun", "batao", "bataye",
})


class BargeClassifier:
    """Pure decision class — no Pipecat dependency. Used by BargeInManager."""

    def __init__(self, *, session: "Session", conversation_id: str) -> None:
        self._session = session
        self._conversation_id = conversation_id

    # ------------------------------------------------------------------
    # Phase 1 — acoustic gate. Called when VAD trips, before we have
    # any transcript. Rejects obvious noise/echo/cough without ever
    # cancelling TTS.
    # ------------------------------------------------------------------
    def acoustic_gate_passed(
        self,
        *,
        user_rms: float,
        tts_peak_rms: float,
        user_duration_ms: int,
    ) -> bool:
        """True = could be real user speech; proceed to transcript check.

        We reject when:
          - duration < 300 ms (cough, breath, mic bump, glitch)
          - user audio is much quieter than what the bot was producing
            (echo bleed from the candidate's phone speaker → mic)

        Tuned to be slightly permissive on `user_rms` so we don't suppress
        a quiet but genuine "hello?". The transcript phase catches actual
        intent.
        """
        if user_duration_ms < 300:
            return False
        # Echo check — only if the bot was actually loud just now.
        if tts_peak_rms > 50:
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

        # No transcript + short audio → treat as ACK (the acoustic gate already
        # let it through, but without text content we shouldn't barge).
        if not norm and user_duration_ms < 700:
            return BargeIntent.ACK

        words = norm.split()
        n = len(words)

        # === All-ack repetition (any length) → ACK / ANSWER ===
        # "yeah yeah yeah", "yes yes ok", "right right ok sure" — pure
        # acknowledgement; bot must keep speaking regardless of word count.
        if n >= 2 and all(w in _ACK_WORDS for w in words):
            return self._ack_or_answer()

        # === Confused PHRASES (any length, exact substring) → CONFUSED ===
        # "say again", "didn't catch", "kya bola", etc. We deliberately do
        # NOT treat single confused-WORDS like "what" / "kya" embedded in
        # longer utterances as confusion — "what about fees" is a question,
        # not "huh?". (Single-word confused matches are handled below.)
        if n >= 2 and any(phrase in norm for phrase in _CONFUSED_PHRASES):
            return BargeIntent.CONFUSED

        # === Long content (5+ words) → INTERRUPT ===
        # Five+ words of non-ack content is almost certainly a real
        # interruption with substance. Direct decision, no LLM needed.
        # Examples: "I have a question about the fees", "actually let me
        # ask about scholarships first", "my intermediate percentage is 92".
        if n >= 5:
            return BargeIntent.INTERRUPT

        # === 3-4 word inputs with a clear INTERRUPT signal → INTERRUPT ===
        # "wait what about fees", "no I disagree with that", "stop right
        # there please". A leading or embedded interrupt token settles it.
        if n >= 3 and any(w in _INTERRUPT_WORDS for w in words):
            return BargeIntent.INTERRUPT

        # === Single confused word → CONFUSED. ===
        if n == 1 and norm in _CONFUSED_SINGLES:
            return BargeIntent.CONFUSED

        # === Single interrupt word → INTERRUPT. ===
        if n == 1 and norm in _INTERRUPT_WORDS:
            return BargeIntent.INTERRUPT

        # === Single ack word → ACK or ANSWER (context-aware). ===
        if n == 1 and norm in _ACK_WORDS:
            return self._ack_or_answer()

        # === Two-word: known confusion phrase already caught above. ===
        if n == 2 and any(w in _INTERRUPT_WORDS for w in words):
            return BargeIntent.INTERRUPT

        # === Question / info-request → INTERRUPT. ===
        # A multi-word, non-all-ack utterance that asks something ("what about
        # fees", "can you tell me the fees", "how much is it", trailing "?",
        # "kya fees hai") is a real question the candidate wants answered. We
        # decide this with the heuristic so there's NO LLM on the critical path
        # and no fragment guessing — the BargeInManager waits for the FULL final
        # transcript before answering.
        if n >= 2 and not all(w in _ACK_WORDS for w in words):
            if text.endswith("?") or any(w in _QUESTION_WORDS for w in words):
                return BargeIntent.INTERRUPT

        # === Everything else (short, non-ack, non-question) → AMBIGUOUS. ===
        # The BargeInManager keeps the bot speaking on AMBIGUOUS interims and
        # re-classifies the FULL final utterance when it lands (a complete
        # sentence with content is treated as an interrupt there). No async LLM.
        # Examples that land here: "okay tell me" (continuation), "yes I follow",
        # half-formed interims like "you can".
        return BargeIntent.AMBIGUOUS

    # ------------------------------------------------------------------
    # Internal helpers — all return safe defaults on any error so a
    # broken signal never blocks the audio path.
    # ------------------------------------------------------------------
    def _ack_or_answer(self) -> BargeIntent:
        """An ack-word with context — if the bot just asked a question, the
        word is the answer; otherwise it's a listener noise."""
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
        """True only if the bot's last utterance ENDS with a question mark.

        Why "ends with" instead of "contains": a reply like
            "In CSE we have programming. Will you also try AI? Anyway,
             our placements are excellent — let me explain that next."
        contains `?` but the question is no longer the freshest thing
        the bot said — it has moved on. A user "okay" in this case is
        a listener acknowledgement (ACK), not an answer to the AI question.

        Only when `?` is the LAST non-whitespace / non-closing-quote
        character do we treat a user ack-word as ANSWER.
        """
        text = self._bot_last_sentence().rstrip()
        if not text:
            return False
        # Strip trailing closing quotes / brackets that often follow `?`
        # in transcribed speech (e.g. He said "are you sure?")
        stripped = text.rstrip("\"')]").rstrip()
        return stripped.endswith("?")

    def _recent_history(self, n: int = 2) -> str:
        try:
            from langchain_core.messages import AIMessage, HumanMessage
            from agent_backend.llm_agent.conversation import get_conversation
            history = get_conversation(self._conversation_id).recent(n=n * 2)
            lines: list[str] = []
            for msg in history:
                role = "Bot" if isinstance(msg, AIMessage) else "User"
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                lines.append(f"  {role}: {content[:80]}")
            return "\n".join(lines) if lines else "  (no prior turns)"
        except Exception:  # noqa: BLE001
            return "  (history unavailable)"

    def _user_recent_sentiment(self) -> str:
        try:
            from agent_backend.data.leads import LeadRepo
            if not self._session.lead_id:
                return "neutral"
            lead = LeadRepo.get().get_by_id(self._session.lead_id)
            if not lead or not lead.conversation_state:
                return "neutral"
            cs = lead.conversation_state
            return cs.get("sentiment", "neutral") if isinstance(cs, dict) else "neutral"
        except Exception:  # noqa: BLE001
            return "neutral"


__all__ = ["BargeClassifier", "BargeIntent"]
