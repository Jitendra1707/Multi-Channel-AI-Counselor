"""Static conversational FILLERS — spoken while RAG/retrieval runs, so the caller
hears a natural "let me check" instead of dead air during the lookup latency.

ROBUST, ZERO-LATENCY matching
-----------------------------
Real cosine similarity would need to embed the query (~100-300ms) — the very
latency this feature exists to MASK — so we do NOT embed. Instead `pick_filler`
uses pure in-memory scoring (microseconds, no network):

  1. Tokenize the query into WORDS (word-boundary match — so "date" no longer
     false-matches "candidate", "rank" no longer matches "frankly").
  2. Score EVERY category by how many of its trigger terms appear (weighted;
     multi-word phrases score higher). Pick the highest-scoring category — not
     brittle first-match.
  3. CONFIDENCE FLOOR: if the best score is weak/ambiguous, fall back to a
     GENERIC filler ("let me check that for you") that fits ANY question — so a
     wrong-but-confident line (e.g. a placements filler for a "pathway" query)
     can never happen.

Each category has many short variants, randomized, so repeats are rare and it
doesn't sound like a canned recording.
"""
from __future__ import annotations

import random
import re

# --- Triggers per category ---------------------------------------------------
# Each is a (term, weight). Multi-word phrases get higher weight (more specific).
# Single words are matched on WORD BOUNDARIES, not as substrings.
_TRIGGERS: dict[str, list[tuple[str, float]]] = {
    "fees": [
        ("fee", 1.3), ("fees", 1.3), ("tuition", 1.5), ("scholarship", 1.5),
        ("scholarships", 1.5), ("concession", 1.5), ("cost", 1.3), ("costs", 1.3),
        ("price", 1.3), ("pricing", 1.3), ("amount", 0.8), ("rupees", 1.2),
        ("lakh", 1.2), ("lakhs", 1.2), ("affordable", 1.0), ("expensive", 1.0),
        ("installment", 1.2), ("instalment", 1.2), ("emi", 1.2), ("waiver", 1.5),
        ("discount", 1.0), ("financial aid", 2.0), ("fee structure", 2.0),
    ],
    "deadlines": [
        ("deadline", 1.5), ("deadlines", 1.5), ("last date", 2.0), ("due date", 2.0),
        ("timeline", 1.2), ("schedule", 1.0), ("dates", 1.0), ("apply by", 1.5),
        ("closing date", 2.0), ("start date", 1.5), ("admission date", 2.0),
        ("when can i apply", 2.0), ("when does", 1.2), ("how long", 1.0),
    ],
    "eligibility": [
        ("eligible", 1.5), ("eligibility", 1.5), ("cutoff", 1.5), ("cut off", 2.0),
        ("cut-off", 1.5), ("rank", 1.2), ("eapcet", 1.8), ("sucet", 1.8), ("jee", 1.5),
        ("cuet", 1.5), ("criteria", 1.2), ("qualify", 1.2), ("qualified", 1.2),
        ("marks", 1.0), ("percentage", 1.0), ("percentile", 1.2), ("cgpa", 1.2),
        ("requirement", 1.0), ("requirements", 1.0), ("admission criteria", 2.0),
        ("entrance", 1.2), ("entrance exam", 2.0), ("score", 0.8), ("twelfth", 1.0),
        ("12th", 1.0), ("intermediate", 1.0),
    ],
    "placements": [
        ("placement", 1.5), ("placements", 1.5), ("salary", 1.5), ("package", 1.5),
        ("packages", 1.5), ("recruiter", 1.5), ("recruiters", 1.5), ("hiring", 1.2),
        ("job", 1.0), ("jobs", 1.0), ("ctc", 1.5), ("companies", 1.2), ("company", 1.0),
        ("career", 1.0), ("careers", 1.0), ("placed", 1.2), ("internship", 1.2),
        ("internships", 1.2), ("offers", 1.0), ("highest package", 2.0),
        ("average salary", 2.0), ("placement record", 2.0),
    ],
    "programs": [
        ("course", 1.3), ("courses", 1.3), ("program", 1.3), ("programs", 1.3),
        ("programme", 1.3), ("branch", 1.3), ("branches", 1.3), ("specialization", 1.5),
        ("specialisation", 1.5), ("specializations", 1.5), ("pathway", 1.5),
        ("pathways", 1.5), ("b.tech", 1.2), ("btech", 1.2), ("stream", 1.2),
        ("streams", 1.2), ("department", 1.2), ("departments", 1.2), ("subject", 1.0),
        ("subjects", 1.0), ("curriculum", 1.5), ("syllabus", 1.5), ("cse", 1.0),
        ("ece", 1.0), ("mechanical", 1.0), ("civil", 1.0), ("which course", 2.0),
        ("learning pathway", 2.0), ("ai and ml", 1.2), ("ai&ml", 1.2),
        ("data science", 1.5), ("cyber security", 1.5),
    ],
    "campus": [
        ("campus", 1.2), ("hostel", 1.5), ("hostels", 1.5), ("accommodation", 1.5),
        ("facilities", 1.2), ("library", 1.2), ("lab", 1.0), ("labs", 1.0),
        ("food", 1.2), ("mess", 1.2), ("canteen", 1.2), ("transport", 1.2),
        ("bus", 1.0), ("location", 1.2), ("located", 1.2), ("address", 1.2),
        ("sports", 1.2), ("life on campus", 2.0), ("campus life", 2.0),
    ],
    "admission": [
        ("admission", 1.3), ("admissions", 1.3), ("apply", 1.3), ("application", 1.3),
        ("counselling", 1.5), ("counseling", 1.5), ("seat", 1.2), ("seats", 1.2),
        ("allotment", 1.5), ("process", 0.8), ("documents", 1.2), ("how to apply", 2.0),
        ("admission process", 2.0), ("registration", 1.2), ("enroll", 1.2),
    ],
}

# Per-category spoken fillers — many short, WARM, human variants so it never
# sounds canned or robotic. ALL avoid stating any fact (they only buy a beat).
# Kept short and natural — like a real person saying "one sec, let me check".
_FILLERS: dict[str, list[str]] = {
    "fees": [
        "Give me just a moment.",
        "Give me a moment, I'll check."
    ],
    "deadlines": [

        "Give me just a moment."

    ],
    "eligibility": [

        "Give me just a moment."
    ],
    "placements": [

        "Give me just a moment."
    ],
    "programs": [

        "Give me just a moment."

    ],
    "campus": [
        "One sec, let me check the campus details.",
        "Give me a moment, I'll check that for you.",
        "Let me quickly look that up.",
        "Hold on, just checking for you.",
        "Give me just a moment."
    ],
    "admission": [
        "One sec, let me check the admission process.",
        "Give me a moment, I'll check the steps.",
        "Let me quickly look at the process.",
        "Hold on, just checking the application details.",
    ],
    # GENERIC — used whenever no category clears the confidence floor. Fits ANY
    # question, so an uncertain match never sounds wrong.
    "generic": [
        "Sure, one sec — let me check.",
        "Give me just a moment.",
        "Let me quickly check that for you.",
        "One sec, let me look that up.",
        "Hold on, just checking that now.",
        "Give me a moment, I'll check.",
    ],
}

# Hindi (Hinglish) fillers — spoken when the conversation is in Hindi. Warm,
# short, FEMININE forms (the voice channel persona, Aisha, is she/her — fillers
# only fire on that channel, see tools/voice/knowledge_base.py). Devanagari for
# the Hindi words; common domain nouns kept in English (natural Hinglish, and
# the multilingual TTS voice pronounces the mix correctly).
_FILLERS_HI: dict[str, list[str]] = {
    "fees": [
        "हाँ ज़रूर, एक सेकंड दीजिए।"
    ],
    "deadlines": [
        "हाँ ज़रूर, एक सेकंड दीजिए।"
    ],
    "eligibility": [
        "हाँ ज़रूर, एक सेकंड दीजिए।"
    ],
    "placements": [
        "हाँ ज़रूर, एक सेकंड दीजिए।"
    ],
    "programs": [
        "हाँ ज़रूर, एक सेकंड दीजिए।"
        

    ],
    "campus": [
        "हाँ ज़रूर, एक सेकंड दीजिए।"
    ],
    "admission": [
        "हाँ ज़रूर, एक सेकंड दीजिए।"
    ],
    "generic": [
        "हाँ ज़रूर, एक सेकंड दीजिए।"
    ],
}

# Pre-split multi-word phrases vs single words once at import (no per-call cost).
# Single words → matched against the query's word set; phrases → substring on the
# normalized query.
_WORD_TRIGGERS: dict[str, list[tuple[str, float]]] = {}
_PHRASE_TRIGGERS: dict[str, list[tuple[str, float]]] = {}
for _cat, _terms in _TRIGGERS.items():
    for _t, _w in _terms:
        bucket = _PHRASE_TRIGGERS if " " in _t else _WORD_TRIGGERS
        bucket.setdefault(_cat, []).append((_t, _w))

_WORD_RE = re.compile(r"[a-z0-9&.+]+")

# Minimum score a category must reach to be chosen over the generic fallback.
# Tuned so one weak single-word hit (weight ~1.0) is NOT enough on its own
# unless it's a strong/specific term — preventing wrong-category fillers.
_CONFIDENCE_FLOOR = 1.2


def _score(query: str) -> tuple[str, float]:
    """Return (best_category, score) by weighted word/phrase matching. Pure
    in-memory; no embedding/network — microseconds."""
    q = (query or "").lower()
    words = set(_WORD_RE.findall(q))
    scores: dict[str, float] = {}
    for cat, terms in _WORD_TRIGGERS.items():
        s = sum(w for term, w in terms if term in words)
        if s:
            scores[cat] = scores.get(cat, 0.0) + s
    for cat, terms in _PHRASE_TRIGGERS.items():
        s = sum(w for term, w in terms if term in q)
        if s:
            scores[cat] = scores.get(cat, 0.0) + s
    if not scores:
        return "generic", 0.0
    best = max(scores, key=scores.get)
    return best, scores[best]


def pick_filler(query: str, lang: str = "en") -> str:
    """A short, human, topic-matched filler for the query — randomized. Falls
    back to a GENERIC line when the topic isn't clearly identifiable, so an
    uncertain match never produces a wrong-topic line. Zero added latency.

    `lang`: "hi" → Hindi (Hinglish) fillers; anything else → English. The TOPIC
    is matched off the (English) search query either way; only the spoken pool
    changes. Unknown lang falls back to English."""
    cat, score = _score(query)
    if score < _CONFIDENCE_FLOOR:
        cat = "generic"
    pools = _FILLERS_HI if lang == "hi" else _FILLERS
    pool = pools.get(cat) or pools.get("generic") or _FILLERS["generic"]
    return random.choice(pool)


__all__ = ["pick_filler"]
