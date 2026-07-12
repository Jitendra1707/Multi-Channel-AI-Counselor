"""Per-conversation FILLER SPEAKER registry.

A RAG tool (search_knowledge_base / present_analytics) runs in the brain layer
and has no handle on the audio pipeline. To speak a "let me check…" filler the
INSTANT retrieval starts — masking the lookup+LLM latency — the channel's
AgentBridge registers a speaker callback here (keyed by conversation_id), and the
tool calls `speak_filler(conversation_id, query)` to trigger it.

Same registry pattern as the avatar mute / ui-emitter registries. Channel-
agnostic: voice and avatar bridges each register their own callback that knows
how to push a spoken line into THEIR pipeline. Best-effort: no speaker
registered (e.g. text channel, or filler disabled) → `speak_filler` is a no-op
returning False, and the caller just proceeds without a filler.
"""
from __future__ import annotations

import re
from typing import Callable

from agent_backend.infra import get_logger
from agent_backend.llm_agent.fillers import pick_filler

log = get_logger(__name__)

# Romanized-Hindi (Hinglish) markers — high-precision Hindi function/words that
# essentially never occur as standalone English words. Azure en-IN transcribes a
# Hindi caller into ROMANIZED Latin (not Devanagari), so we detect the language
# from these, not from script. Deliberately EXCLUDES English-colliding tokens
# ('is', 'us', 'me', 'to', 'are', 'main' → 'JEE Main'). Matched on word
# boundaries, lowercased.
_HI_MARKERS: frozenset[str] = frozenset({
    "hai", "hain", "haan", "han", "kya", "kyun", "kyon", "kitna", "kitni", "kitne",
    "kitana", "kitani", "mujhe", "muje", "mera", "meri", "mere", "mereko", "aap",
    "aapko", "aapka", "aapki", "aapke", "tum", "tumhe", "tumhara", "tumhaara",
    "tumhari", "tumhaari", "tumse", "tera", "teri", "tere", "nahi", "nahin",
    "kaise", "kaisa", "kaisi", "chahiye", "chaahiye", "chahie", "raha", "rahi",
    "rahe", "rha", "rhi", "hoon", "hun", "hota", "hoti", "hoga", "hogi", "bata",
    "batao", "bataiye", "bataye", "sakta", "sakti", "sakte", "accha", "acha",
    "achha", "theek", "thik", "theek", "bhej", "bhejo", "bhejdo", "wala", "wali",
    "wale", "waala", "waali", "yahan", "wahan", "yaha", "waha", "thoda", "thodi",
    "zara", "jara", "matlab", "samajh", "samjha", "samjhi", "dijiye", "dijie",
    "dena", "karo", "karna", "karun", "karoon", "krna", "milega", "milegi",
    "milta", "milti", "mil", "laakh", "lakh", "hazaar", "hazar", "rupaye", "paisa",
    "paise", "toh", "bhi", "abhi", "lekin", "magar", "kyunki", "waise", "jaise",
    "aur", "ki", "ka", "ke", "ko", "se", "mein", "pe", "kuch", "koi", "sab",
    "phir", "fir", "jab", "tab", "yeh", "ye", "woh", "wo", "kaun", "kahan", "kab",
    "bahut", "sasta", "mehnga", "join", "karunga", "karungi",
})
_WORD_RE = re.compile(r"[a-z]+")
# How many marker hits across the recent turns flips the conversation to Hindi.
# 2 avoids a single ambiguous token tripping it; a real Hindi turn has many.
_HI_THRESHOLD = 2

# conversation_id -> speaker(text) -> None. The bridge's callback pushes `text`
# as a spoken utterance into its TTS pipeline and records that a filler is in
# flight (so the bridge can gate the real answer behind it).
_SPEAKERS: dict[str, Callable[[str], None]] = {}


def register_filler_speaker(conversation_id: str, speaker: Callable[[str], None]) -> None:
    _SPEAKERS[conversation_id] = speaker


def clear_filler_speaker(conversation_id: str) -> None:
    _SPEAKERS.pop(conversation_id, None)


def _conversation_is_hindi(conversation_id: str) -> bool:
    """True when the conversation is being held in Hindi.

    Detection works on ROMANIZED text (Azure en-IN transcribes Hindi into Latin,
    so Devanagari rarely appears): we count high-precision Hindi marker words
    across the last few turns and flip to Hindi once they cross a small
    threshold. Any stray Devanagari is also treated as Hindi. Best-effort: any
    failure → English."""
    try:
        from agent_backend.llm_agent.conversation import get_conversation

        msgs = get_conversation(conversation_id).recent(n=6)
        text = " ".join(
            getattr(m, "content", "") for m in msgs if isinstance(getattr(m, "content", ""), str)
        )
        # Devanagari block U+0900–U+097F (bonus signal when it does appear).
        if any("ऀ" <= ch <= "ॿ" for ch in text):
            return True
        hits = sum(1 for w in _WORD_RE.findall(text.lower()) if w in _HI_MARKERS)
        return hits >= _HI_THRESHOLD
    except Exception:  # noqa: BLE001 — never let language detection break the filler
        return False


def speak_filler(conversation_id: str, query: str) -> bool:
    """Speak a topic-matched filler for this conversation while RAG runs.

    Returns True if a filler was emitted, False if there's no speaker (so the
    caller knows whether a filler is in flight). Never raises into the tool."""
    fn = _SPEAKERS.get(conversation_id)
    if fn is None:
        return False
    lang = "hi" if _conversation_is_hindi(conversation_id) else "en"
    line = pick_filler(query, lang=lang)
    try:
        fn(line)
        log.info("[filler] spoke", conversation_id=conversation_id[:12], line=line)
        return True
    except Exception as e:  # noqa: BLE001
        log.debug("[filler] speak failed", conversation_id=conversation_id[:12], err=str(e)[:120])
        return False


__all__ = ["register_filler_speaker", "clear_filler_speaker", "speak_filler"]
