"""Query → knowledge-source router (university vs general) — SEMANTIC, mixture.

Each KB is described by a `KB_MAP` entry holding (a) a detailed DESCRIPTION of
what it covers and (b) a set of example-query ANCHORS. The description is split
into sentences and each sentence + each anchor becomes a "magnet" — a point in
embedding space. A query routes to the source whose NEAREST magnet (max cosine)
it's closest to.

Why the mixture (description sentences + anchors):
  - Anchors match specific query phrasings tightly (question↔question).
  - Description sentences blanket the KB's whole SCOPE, catching topics no single
    anchor anticipated (education loans, faculty, visas, …) — closing the
    "both scores low → default" coverage gap that a small anchor set has.
  Together, max-over-union means whichever magnet (anchor OR description facet)
  is closest decides — robust to phrasing and to topic coverage.

Bias: UNIVERSITY is the default; a query routes to GENERAL only when its best
general magnet beats its best university magnet by at least
`settings.ROUTE_MARGIN` (counsellor is university-centric; ties → university).

Cost: the query embedding is REUSED from the dense search (passed as
`query_vec`), so routing adds no extra embedding call. All magnets are embedded
once (one batched request) and cached for the process. Per query it's a few
dozen in-memory dot products — microseconds.

Tuning is DATA, not code: edit the descriptions/anchors in KB_MAP. The matched
magnet kind ("desc"/"anchor") and both scores are returned as `reason` and
logged, so you can see exactly why a query routed where it did.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

import numpy as np

from agent_backend.rag import settings as rag_settings
from agent_backend.rag.embedder import embed_query, embed_texts

# Minimum length (chars) for a description sentence to become a magnet — drops
# fragments like "Use it." that would add noise.
_MIN_SENTENCE_CHARS = 12


# ---------------------------------------------------------------------------
# The KB map — the single place to describe what each KB holds. Editing these
# strings re-shapes routing (after a process restart, since magnets are cached).
# ---------------------------------------------------------------------------
KB_MAP: dict[str, dict[str, Any]] = {
    "university": {
        "description": (
            "This knowledge base covers everything specific to the university "
            "itself. It holds the university's tuition fees and fee structure for "
            "each branch and specialisation, scholarships, fee waivers and "
            "concessions, the admission process, eligibility criteria and cutoff "
            "ranks, the entrance exams accepted and the counselling steps, the "
            "courses, B.Tech branches and specialisations offered here, hostel and "
            "accommodation, campus facilities and the university's location, "
            "placements, recruiters and salary packages at this university, "
            "important admission dates and deadlines, the documents required, and "
            "contact details for the admissions office. Use it for any question "
            "about THIS university's offerings, costs, scholarships, admissions, "
            "courses, campus, or placements."
        ),
        "anchors": [
            "What are the tuition fees for the program?",
            "How much does the course cost here?",
            "Tell me about scholarships and fee waivers.",
            "Can I get a fee concession if my marks are good?",
            "How do I apply for admission?",
            "What is the admission process and eligibility?",
            "What is the cutoff rank required for CSE?",
            "Which entrance exams do you accept?",
            "Is hostel accommodation available on campus?",
            "Tell me about the campus and its facilities.",
            "Where is the university located?",
            "What are the placement statistics and recruiters here?",
            "Which B.Tech branches and specialisations do you offer?",
            "What is the fee for CSE with AI and ML?",
            "What documents are needed for counselling?",
            "What is the application deadline?",
            "Tell me about your university.",
            "How do I contact the admissions office?",
        ],
    },
    "general": {
        "description": (
            "This knowledge base covers the broader education industry and career "
            "guidance, NOT any single university. It holds general information "
            "about the types of undergraduate degrees and how they compare, the "
            "core engineering branches, what computer-science specialisations like "
            "Artificial Intelligence, Machine Learning, Data Science, Cyber "
            "Security and Cloud mean and how they differ from each other, career "
            "paths and job roles, technology hiring and industry trends, typical "
            "salary ranges in India for various roles, the skills that are in "
            "demand, how to choose a course or branch, entrance exams in general, "
            "education loans and study-abroad considerations, and higher-study "
            "options after graduation. Use it for general questions about careers, "
            "courses as concepts, industry trends, comparisons between fields, and "
            "salaries."
        ),
        "anchors": [
            "What are the latest trends in the technology industry?",
            "Is artificial intelligence a good career choice?",
            "What is the difference between AI and Data Science?",
            "What is the average salary for software engineers in India?",
            "Which engineering field has the best future scope?",
            "What is machine learning and how does it work?",
            "What does the job market look like for tech graduates?",
            "What are the career prospects in computer science?",
            "How is the demand for AI skills growing?",
            "Should I choose CSE or ECE in general?",
            "What skills are in demand in the IT industry?",
            "What can I do after a B.Tech degree?",
            "Is computer science oversaturated as a field?",
            "How much do AI engineers typically earn?",
            "What is the scope of data science as a career?",
            "What is the difference between B.Tech and BCA?",
            "Are private colleges better than government ones in general?",
            "Is studying abroad worth it for a tech career?",
        ],
    },
}


def _sentences(text: str) -> list[str]:
    """Split a description into sentence-ish magnets (on sentence enders and
    newlines), dropping very short fragments."""
    parts = re.split(r"(?<=[.!?])\s+|\n+", (text or "").strip())
    return [p.strip() for p in parts if len(p.strip()) >= _MIN_SENTENCE_CHARS]


@lru_cache(maxsize=1)
def _magnets() -> dict[str, tuple[np.ndarray, list[str]]]:
    """{source: (matrix (N, dim) L2-normalised, kinds)} where each row is a
    magnet (a description sentence or an anchor) and `kinds[i]` is "desc"/"anchor".
    Embedded ONCE with the same model as queries (so cosine is comparable) and
    cached for the process. First call costs one batched embedding request."""
    out: dict[str, tuple[np.ndarray, list[str]]] = {}
    for source, spec in KB_MAP.items():
        texts: list[str] = []
        kinds: list[str] = []
        for s in _sentences(spec.get("description", "")):
            texts.append(s)
            kinds.append("desc")
        for a in spec.get("anchors", []) or []:
            texts.append(str(a))
            kinds.append("anchor")
        if not texts:
            continue
        mat = embed_texts(texts, model=rag_settings.DENSE_MODEL)  # (N, dim), normalised
        out[source] = (mat, kinds)
    return out


def route_source(
    query: str,
    *,
    general_available: bool,
    query_vec: Any = None,
) -> tuple[str, str]:
    """Return (source, reason). source ∈ {"university", "general"}.

    `query_vec` is the query's dense embedding (reused from search); if omitted,
    it's computed here. If the general KB isn't configured, always 'university'.
    Any failure falls back to 'university' (never raises).
    """
    if not general_available:
        return "university", "general-kb-disabled"

    try:
        qv = query_vec if query_vec is not None else embed_query(query, model=rag_settings.DENSE_MODEL)
        qv = np.asarray(qv, dtype=np.float32)
        magnets = _magnets()
        scores: dict[str, float] = {}
        won_kind: dict[str, str] = {}
        for source, (mat, kinds) in magnets.items():
            sims = mat @ qv  # cosine (unit vectors): similarity to each magnet
            idx = int(sims.argmax())
            scores[source] = float(sims[idx])
            won_kind[source] = kinds[idx]
    except Exception as e:  # noqa: BLE001 — routing must never break a turn
        return "university", f"route-error:{type(e).__name__}"

    uni = scores.get("university", -1.0)
    gen = scores.get("general", -1.0)
    if gen > uni + rag_settings.ROUTE_MARGIN:
        return "general", f"gen={gen:.3f}({won_kind.get('general')})>uni={uni:.3f}"
    return "university", f"uni={uni:.3f}({won_kind.get('university')})>=gen={gen:.3f}"
