"""Knowledge capture from a live video call (Phase 1 core — pure, testable).

The director states a fact mid-call; this module turns it into a structured
candidate, checks it for CONTRADICTIONS against the existing KB, and (on
approval) ingests it into the correct collection — optionally superseding the
points it replaces. The live-loop wiring (data channel, approval card) and the
governance persistence (BusinessLayer table) live in the channel + BusinessLayer
layers; everything here is synchronous, side-effect-light, and unit-testable, so
the async hooks just call these via `asyncio.to_thread`.

Pipeline:
  extract_candidate(utterance)            → ExtractedCandidate | None   (LLM, cheap)
  analyze_conflict(text, topic, kb)       → dict {score, blocking, items[]}
  apply_resolution(text, heading, topic,  → dict {doc_id, point_ids, collection,
                   kb, conflict_items,            patched[]}
                   action, candidate_id)

Contradiction ≠ low similarity: "fee is ₹2L" and "fee is ₹3L" are embedding-close
yet contradictory. So we RETRIEVE the same-topic neighborhood (hybrid search) and
make a DIRECTED judgment per neighbor (LLM-as-judge), with a deterministic
numeric/date pre-check that forces a contradiction on the highest-stakes facts
(money / % / dates) regardless of judge softness.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from agent_backend.config import get_settings
from agent_backend.rag import settings as rag_settings

try:
    from agent_backend.infra import get_logger

    log = get_logger(__name__)
except Exception:  # noqa: BLE001 — keep importable standalone
    import logging

    log = logging.getLogger("agent_backend.rag.knowledge_capture")


# ---------------------------------------------------------------------------
# Structured-output schemas (the LLM is forced to fill these).
# ---------------------------------------------------------------------------
class ExtractedCandidate(BaseModel):
    """The detect+extract step's output."""

    is_fact: bool = Field(description="True only if the utterance asserts a durable, KB-worthy fact (not a question, opinion, or hedge).")
    heading: str = Field(default="", description="Short title for the knowledge item, e.g. 'Infosys Scholarship 2026'.")
    text: str = Field(default="", description="The fact as a clean, self-contained statement (resolve pronouns/referents using context).")
    topic: str = Field(default="", description="One of: fees, eligibility, application, admission, programs, placements, reservation, certification, governance, overview, general.")
    suggested_kb: str = Field(default="university", description="'university' (this institution's facts) or 'general' (industry/career).")
    confidence: float = Field(default=0.0, description="0..1 confidence that this is a durable, correctly-extracted fact.")


class _JudgeItem(BaseModel):
    index: int = Field(description="Index of the neighbor this verdict is about.")
    relation: str = Field(description="contradicts | updates | entails | unrelated")
    confidence: float = Field(default=0.0, description="0..1 confidence in the relation.")
    attribute: str = Field(default="", description="The shared attribute, e.g. 'infosys_scholarship_pct'.")
    old_value: str = Field(default="", description="The neighbor's value, if any.")
    new_value: str = Field(default="", description="The candidate's value, if any.")
    conflicting_span: str = Field(default="", description="The neighbor span that conflicts.")
    explanation: str = Field(default="", description="One short sentence.")


class _JudgeResult(BaseModel):
    items: list[_JudgeItem] = Field(default_factory=list)


@dataclass
class ConflictItem:
    point_id: str | None
    source_doc: str
    version: str
    relation: str
    confidence: float
    attribute: str
    old_value: str
    new_value: str
    span: str
    explanation: str


@dataclass
class ConflictResult:
    score: int
    blocking: bool
    items: list[ConflictItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "blocking": self.blocking,
            "items": [vars(i) for i in self.items],
        }


# ---------------------------------------------------------------------------
# LLM client (cheap model, structured output). Classic models (gpt-4o-mini) are
# the default and support structured output + temperature/max_tokens directly.
# ---------------------------------------------------------------------------
_BASE_CLIENTS: dict[str, Any] = {}


def _client(model: str, max_tokens: int = 900) -> Any:
    # max_tokens must cover the output: 900 fits extract/judge verdicts, but a
    # chunk REWRITE returns the whole chunk (hard max 1100 tokens) — pass a
    # bigger cap there. Cached per (model, cap).
    key = f"{model}:{max_tokens}"
    c = _BASE_CLIENTS.get(key)
    if c is not None:
        return c
    from langchain_openai import ChatOpenAI

    s = get_settings()
    # Reasoning models (gpt-5.x/o-series) only accept the default temperature and
    # use max_completion_tokens; the knowledge models default to gpt-4o-mini
    # (classic), so the simple config is correct. Guard anyway.
    m = (model or "").lower()
    if m.startswith(("o1", "o3", "o4", "gpt-5")):
        c = ChatOpenAI(model=model, api_key=s.llm_api_key, base_url=s.llm_api_url,
                       temperature=1, model_kwargs={"max_completion_tokens": max(1200, max_tokens)})
    else:
        c = ChatOpenAI(model=model, api_key=s.llm_api_key, base_url=s.llm_api_url,
                       temperature=0, max_tokens=max_tokens)
    _BASE_CLIENTS[key] = c
    return c


# ---------------------------------------------------------------------------
# [1] Detect + extract
# ---------------------------------------------------------------------------
_EXTRACT_SYS = (
    "You convert a university director's spoken statement into a structured "
    "knowledge item for the knowledge base. The director is an AUTHORITY who is "
    "ADDING or CORRECTING institutional knowledge — so capture their assertion as a "
    "fact EVEN IF you cannot verify it, even if it's brand new, and even if it "
    "conflicts with what you already know (verification and conflict handling happen "
    "later, downstream — that is NOT your job). Do NOT reject a statement just "
    "because it isn't in the knowledge base yet. Set is_fact=false ONLY for genuine "
    "questions, greetings/small talk, or vague filler that asserts no fact. Rewrite "
    "the fact as ONE clean, self-contained statement, resolving pronouns/referents "
    "from context so it stands alone; normalise obvious speech-to-text word slips "
    "(e.g. 'BT'/'B tech' -> 'B.Tech') but NEVER change numbers, percentages, or "
    "dates. Choose suggested_kb='university' for this institution's own facts, "
    "'general' for industry/career topics."
)


def extract_candidate(
    utterance: str, *, history: list[str] | None = None, lenient: bool = False
) -> ExtractedCandidate | None:
    """LLM detect+extract. Never raises — a failure yields None (no candidate).

    `lenient=True` (an EXPLICIT director Capture click) trusts the intent: we keep
    whatever the model extracts as long as there's usable text, ignoring the
    `is_fact`/confidence gate (the director deliberately asked to capture this). The
    strict gate is only for the off-by-default AUTO detector (`lenient=False`)."""
    utterance = (utterance or "").strip()
    if not utterance:
        return None
    s = get_settings()
    ctx = ""
    if history:
        ctx = "Conversation so far (for referent resolution):\n" + "\n".join(history[-10:]) + "\n\n"
    prompt = f"{ctx}Director said: \"{utterance}\"\n\nExtract the fact (or is_fact=false)."
    try:
        structured = _client(s.knowledge_detect_model).with_structured_output(ExtractedCandidate)
        result: ExtractedCandidate = structured.invoke(
            [("system", _EXTRACT_SYS), ("human", prompt)]
        )
    except Exception as e:  # noqa: BLE001
        log.warning("[kcapture] extract failed", err=str(e)[:200])
        return None

    # Always log the decision so a drop is never opaque ("no durable fact" was silent).
    log.info(
        "[kcapture] extract result",
        is_fact=result.is_fact, conf=round(float(result.confidence), 2),
        lenient=lenient, text=(result.text or "")[:80],
    )

    if not (result.text or "").strip():
        return None  # nothing usable to capture, regardless of mode

    if not lenient:
        # AUTO path: strict — must be a confident, asserted fact.
        if not result.is_fact:
            return None
        if result.confidence < s.knowledge_confidence_min:
            log.info("[kcapture] candidate below confidence floor", conf=result.confidence)
            return None
    elif not result.is_fact:
        # EXPLICIT path: the director chose to capture this — honour it, just note it.
        log.info("[kcapture] explicit capture overriding is_fact=false")

    if result.suggested_kb not in ("university", "general"):
        result.suggested_kb = "university"
    return result


# ---------------------------------------------------------------------------
# [2] Contradiction analysis
# ---------------------------------------------------------------------------
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b)", re.I)
_MONEY = re.compile(
    r"(?:₹|rs\.?\s*)\s*([\d,]+(?:\.\d+)?)|\b(\d+(?:\.\d+)?)\s*(lakhs?|lpa|crores?|cr|k)\b",
    re.I,
)


def _pcts(text: str) -> set[float]:
    return {float(m) for m in _PCT.findall(text or "")}


def _monies(text: str) -> set[str]:
    out: set[str] = set()
    for m in _MONEY.finditer(text or ""):
        if m.group(1):
            out.add(m.group(1).replace(",", ""))
        elif m.group(2):
            out.add(f"{m.group(2)}{(m.group(3) or '').lower()}")
    return out


def _numeric_conflict(a: str, b: str) -> bool:
    """Conservative deterministic check: if BOTH texts state a percentage (or a
    money amount) and the sets are disjoint, treat it as a contradiction. Only
    fires when the same UNIT type is present on both sides, so unrelated numbers
    don't trip it. False positives merely block for human review (safe)."""
    pa, pb = _pcts(a), _pcts(b)
    if pa and pb and pa.isdisjoint(pb):
        return True
    ma, mb = _monies(a), _monies(b)
    if ma and mb and ma.isdisjoint(mb):
        return True
    return False


_JUDGE_SYS = (
    "You compare a NEW fact against existing knowledge-base passages and label each "
    "passage's relation to the new fact: 'contradicts' (same attribute, incompatible "
    "value, both meant to be current), 'updates' (new fact is a newer value of a now-"
    "stale passage), 'entails' (passage already says the same), or 'unrelated'. Be "
    "strict: only 'contradicts'/'updates' when they concern the SAME attribute. Give "
    "the attribute and the two values when relevant."
)


def _collections_to_check() -> list[str]:
    srcs = ["university"]
    if rag_settings.QDRANT_GENERAL_COLLECTION:
        srcs.append("general")
    return srcs


def analyze_conflict(
    text: str,
    *,
    topic: str = "",
    kb: str = "university",
    exclude_point_ids: set[str] | None = None,
) -> ConflictResult:
    """Retrieve same-topic neighbors across collections, judge each, fold a
    deterministic numeric pre-check, and return an explainable score + blocking
    flag. Never raises — on any failure returns an empty (non-blocking) result so
    the capture flow degrades to 'no conflict detected' rather than breaking."""
    text = (text or "").strip()
    if not text:
        return ConflictResult(score=0, blocking=False)
    s = get_settings()
    exclude = exclude_point_ids or set()

    # 1) Retrieve neighborhood across BOTH collections (a university fact can
    #    contradict a general-KB fact, so we don't trust query-time routing here).
    try:
        from agent_backend.rag.retriever import get_retriever

        r = get_retriever()
        hits = []
        for src in _collections_to_check():
            hits.extend(r.search(text, k=s.knowledge_neighbors_k, source=src))
    except Exception as e:  # noqa: BLE001
        log.warning("[kcapture] neighbor retrieval failed — no conflict", err=str(e)[:200])
        return ConflictResult(score=0, blocking=False)

    # De-dup by point_id, drop excluded (an edit of an ingested fact must not
    # conflict with its own current points).
    seen: set[str] = set()
    neighbors = []
    for h in hits:
        pid = h.point_id or ""
        if pid in exclude or (pid and pid in seen):
            continue
        if pid:
            seen.add(pid)
        neighbors.append(h)

    # Narrow to same-assertion candidates; fall back to the top few if the filter
    # is too aggressive so the judge still has something to look at.
    def _relevant(h: Any) -> bool:
        m = h.meta or {}
        return (topic and m.get("topic") == topic) or bool(m.get("fee_sensitive")) or bool(m.get("date_sensitive"))

    focused = [h for h in neighbors if _relevant(h)] or neighbors[:3]
    focused = focused[: s.knowledge_neighbors_k]
    if not focused:
        return ConflictResult(score=0, blocking=False)

    # 2) LLM-as-judge over all neighbors in ONE batched call.
    listing = "\n".join(
        f"[{i}] (source: {h.meta.get('source_doc','?')}, v{h.meta.get('version','?')}) {h.text}"
        for i, h in enumerate(focused)
    )
    prompt = f"NEW fact:\n\"{text}\"\n\nExisting passages:\n{listing}\n\nLabel each passage by index."
    verdicts: dict[int, _JudgeItem] = {}
    try:
        structured = _client(s.knowledge_judge_model).with_structured_output(_JudgeResult)
        jr: _JudgeResult = structured.invoke([("system", _JUDGE_SYS), ("human", prompt)])
        for it in jr.items:
            if 0 <= it.index < len(focused):
                verdicts[it.index] = it
    except Exception as e:  # noqa: BLE001
        log.warning("[kcapture] judge failed — numeric-only", err=str(e)[:200])

    # 3) Deterministic numeric/date pre-check overrides a soft/blank judge.
    items: list[ConflictItem] = []
    for i, h in enumerate(focused):
        v = verdicts.get(i)
        relation = (v.relation if v else "unrelated").lower()
        confidence = float(v.confidence) if v else 0.0
        if _numeric_conflict(text, h.text) and relation not in ("contradicts", "updates"):
            relation = "contradicts"
            confidence = max(confidence, 0.9)
        if relation not in ("contradicts", "updates"):
            continue
        items.append(
            ConflictItem(
                point_id=h.point_id,
                source_doc=str(h.meta.get("source_doc", "?")),
                version=str(h.meta.get("version", "?")),
                relation=relation,
                confidence=round(confidence, 3),
                attribute=(v.attribute if v else ""),
                old_value=(v.old_value if v else ""),
                new_value=(v.new_value if v else ""),
                span=(v.conflicting_span if v else h.text[:200]),
                explanation=(v.explanation if v else "numeric value differs"),
            )
        )

    # 4) Score + blocking. A true 'contradicts' blocks; 'updates' is a supersede
    #    candidate (warn, not block).
    contradicts = [i for i in items if i.relation == "contradicts"]
    updates = [i for i in items if i.relation == "updates"]
    if contradicts:
        score = round(100 * max(i.confidence for i in contradicts))
        blocking = True
    elif updates:
        score = round(60 * max(i.confidence for i in updates))
        blocking = False
    else:
        score = 0
        blocking = False
    return ConflictResult(score=score, blocking=blocking, items=items)


# ---------------------------------------------------------------------------
# [3] Patch / sweep / ingest — applying an approved resolution to the KB.
#
# Superseding used to hard-DELETE the conflicting Qdrant point, which nuked the
# chunk's OTHER (still-valid) facts and left stale copies alive in chunks that
# retrieval hadn't flagged. Now we PATCH: an LLM surgically rewrites just the
# stale value inside each flagged chunk and it's re-upserted under the SAME
# point id (collateral facts survive, old text is returned for audit/undo), and
# a deterministic SWEEP hunts down remaining copies of the old value and patches
# them too — so the KB itself becomes consistent instead of relying on a
# session-prompt override.
# ---------------------------------------------------------------------------
def kb_to_collection(kb: str) -> str:
    """Map the logical KB label to the actual Qdrant collection. Falls back to
    the university collection when 'general' isn't configured."""
    if kb == "general" and rag_settings.QDRANT_GENERAL_COLLECTION:
        return rag_settings.QDRANT_GENERAL_COLLECTION
    return rag_settings.QDRANT_COLLECTION


def _patch_collections(kb: str) -> list[str]:
    """Collections to look in when patching a point by id: the target KB's
    collection first, then any other configured one — conflict analysis searches
    BOTH collections but its items don't record which one a point came from."""
    colls = [kb_to_collection(kb)]
    for other in (rag_settings.QDRANT_COLLECTION, rag_settings.QDRANT_GENERAL_COLLECTION):
        if other and other not in colls:
            colls.append(other)
    return colls


class _RewriteResult(BaseModel):
    changed: bool = Field(description="True ONLY if the passage actually asserts the stale claim and was edited.")
    text: str = Field(default="", description="The full passage, verbatim except the corrected value(s).")


_REWRITE_SYS = (
    "You surgically edit a knowledge-base passage so it agrees with a NEW "
    "authoritative fact. Change ONLY the value/statement the new fact corrects; "
    "preserve every other sentence, number, name, and all formatting VERBATIM — "
    "do not summarise, reorder, shorten, or drop anything. Return the FULL edited "
    "passage. If the passage does not actually assert the stale claim, set "
    "changed=false."
)


def rewrite_chunk(
    chunk_text: str, *, new_fact: str, attribute: str = "", old_value: str = "", new_value: str = ""
) -> str | None:
    """LLM surgical edit of one chunk. Returns the rewritten text, or None when
    the chunk needs no change / the rewrite fails or looks unsafe (never raises —
    a skipped patch is recoverable, a corrupted chunk is not)."""
    chunk_text = (chunk_text or "").strip()
    if not chunk_text:
        return None
    s = get_settings()
    hint = ""
    if attribute or old_value or new_value:
        hint = f"\n(Known correction: {attribute or 'value'}: {old_value or '?'} -> {new_value or '?'})"
    prompt = f"NEW authoritative fact:\n\"{new_fact}\"{hint}\n\nPassage to edit:\n{chunk_text}"
    try:
        structured = _client(s.knowledge_judge_model, max_tokens=2000).with_structured_output(_RewriteResult)
        result: _RewriteResult = structured.invoke([("system", _REWRITE_SYS), ("human", prompt)])
    except Exception as e:  # noqa: BLE001
        log.warning("[kcapture] rewrite failed", err=str(e)[:200])
        return None
    new_text = (result.text or "").strip()
    if not result.changed or not new_text or new_text == chunk_text:
        return None
    # Truncation/summarisation guard: a surgical value edit barely changes
    # length; a much shorter result means the model dropped content — skip.
    if len(new_text) < 0.5 * len(chunk_text):
        log.warning("[kcapture] rewrite rejected — output much shorter than chunk",
                    old_len=len(chunk_text), new_len=len(new_text))
        return None
    return new_text


def _patch_point(
    point_id: str,
    *,
    new_fact: str,
    attribute: str = "",
    old_value: str = "",
    new_value: str = "",
    collections: list[str],
    candidate_id: str = "",
) -> dict[str, Any] | None:
    """Fetch → rewrite → re-upsert ONE point in place. Best-effort: returns
    {point_id, collection, old_text, new_text} on success, None otherwise."""
    from agent_backend.rag.ingestion.pipeline import get_points, update_point_text

    for coll in collections:
        try:
            pts = get_points([point_id], collection=coll)
        except Exception as e:  # noqa: BLE001
            log.debug("[kcapture] get_points failed", collection=coll, err=str(e)[:160])
            continue
        if not pts:
            continue
        old_text = str(pts[0]["payload"].get("text") or "")
        rewritten = rewrite_chunk(
            old_text, new_fact=new_fact, attribute=attribute, old_value=old_value, new_value=new_value
        )
        if rewritten is None:
            return None
        try:
            update_point_text(
                point_id, rewritten, collection=coll,
                payload_updates={
                    "edited_by": candidate_id,
                    "edited_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                },
            )
        except Exception as e:  # noqa: BLE001
            log.warning("[kcapture] patch upsert failed", point_id=point_id[:12], err=str(e)[:200])
            return None
        log.info("[kcapture] patched point", point_id=point_id[:12], collection=coll)
        return {"point_id": point_id, "collection": coll, "old_text": old_text, "new_text": rewritten}
    return None


_SWEEP_MAX = 6  # patch at most this many swept chunks per resolution


def sweep_stale_copies(
    *,
    new_fact: str,
    conflict_items: list[dict[str, Any]],
    exclude_ids: set[str],
    kb: str = "university",
    candidate_id: str = "",
) -> list[dict[str, Any]]:
    """Find OTHER chunks still stating the old value(s) and patch them too.
    Queries come from the judge's old_value/attribute output (the BM25 arm nails
    exact stale numbers); a hit is patched only on DETERMINISTIC confirmation
    (numeric conflict, or the old value appears verbatim) — no extra judge call.
    Never raises; returns the audit list of patches."""
    old_values = []
    queries: list[str] = []
    for it in conflict_items or []:
        ov = str(it.get("old_value") or "").strip()
        if not ov:
            continue
        old_values.append(ov)
        attr = str(it.get("attribute") or "").replace("_", " ").strip()
        q = f"{attr} {ov}".strip()
        if q not in queries:
            queries.append(q)
    if not queries:
        return []
    try:
        from agent_backend.rag.retriever import get_retriever

        r = get_retriever()
    except Exception as e:  # noqa: BLE001
        log.warning("[kcapture] sweep retriever unavailable", err=str(e)[:160])
        return []

    s = get_settings()
    seen: set[str] = set(exclude_ids or set())
    patched: list[dict[str, Any]] = []
    for q in queries:
        if len(patched) >= _SWEEP_MAX:
            break
        for src in _collections_to_check():
            if len(patched) >= _SWEEP_MAX:
                break
            try:
                hits = r.search(q, k=s.knowledge_neighbors_k, source=src)
            except Exception as e:  # noqa: BLE001
                log.debug("[kcapture] sweep search failed", src=src, err=str(e)[:160])
                continue
            collection = kb_to_collection(src)
            for h in hits:
                if len(patched) >= _SWEEP_MAX:
                    break
                pid = h.point_id or ""
                if not pid or pid in seen:
                    continue
                text = h.text or ""
                if not (_numeric_conflict(new_fact, text) or any(ov in text for ov in old_values)):
                    continue
                seen.add(pid)
                p = _patch_point(
                    pid, new_fact=new_fact, collections=[collection], candidate_id=candidate_id
                )
                if p:
                    p["swept"] = True
                    patched.append(p)
    if patched:
        log.info("[kcapture] sweep patched stale copies", n=len(patched))
    return patched


def apply_resolution(
    *,
    text: str,
    heading: str = "",
    topic: str = "",
    kb: str = "university",
    tenant_id: str | None = None,
    conflict_items: list[dict[str, Any]] | None = None,
    action: str = "approve",
    candidate_id: str = "",
    exclude_point_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Apply a director decision to the KB: patch the flagged stale chunks
    (supersede: contradicts+updates; approve: updates only; keep_both: none),
    sweep for remaining stale copies, then ingest the new fact. Returns
    {doc_id, point_ids, collection, patched}. Raises only on a hard INGEST
    error (patching/sweeping are best-effort) so the caller can record it."""
    from agent_backend.rag.ingestion.pipeline import delete_points, get_points, ingest_text

    items = conflict_items or []
    if action == "supersede":
        to_patch = [it for it in items if it.get("point_id") and it.get("relation") in ("contradicts", "updates")]
    elif action == "approve":
        to_patch = [it for it in items if it.get("point_id") and it.get("relation") == "updates"]
    else:  # keep_both (or reject, which never reaches here): both stay valid
        to_patch = []

    collections = _patch_collections(kb)
    patched: list[dict[str, Any]] = []
    for it in to_patch:
        pid = str(it["point_id"])
        p = _patch_point(
            pid,
            new_fact=text,
            attribute=str(it.get("attribute") or ""),
            old_value=str(it.get("old_value") or ""),
            new_value=str(it.get("new_value") or ""),
            collections=collections,
            candidate_id=candidate_id,
        )
        if p:
            patched.append(p)
        elif action == "supersede" and it.get("relation") == "contradicts":
            # The rewrite declined/failed on a REAL contradiction — the stale
            # claim must not survive a supersede, so fall back to deleting the
            # point (the old destructive behaviour, now only the last resort).
            for coll in collections:
                try:
                    pts = get_points([pid], collection=coll)
                    if not pts:
                        continue
                    delete_points([pid], collection=coll)
                    patched.append({
                        "point_id": pid, "collection": coll,
                        "old_text": str(pts[0]["payload"].get("text") or ""), "deleted": True,
                    })
                    log.warning("[kcapture] rewrite failed — deleted contradicting point", point_id=pid[:12])
                    break
                except Exception as e:  # noqa: BLE001
                    log.warning("[kcapture] delete fallback failed", point_id=pid[:12], err=str(e)[:160])

    if action in ("supersede", "approve"):
        exclude = {p["point_id"] for p in patched} | set(exclude_point_ids or ())
        patched.extend(sweep_stale_copies(
            new_fact=text, conflict_items=items, exclude_ids=exclude,
            kb=kb, candidate_id=candidate_id,
        ))

    collection = kb_to_collection(kb)
    res = ingest_text(
        text,
        heading=heading,
        collection=collection,
        topic=topic or None,
        tenant_id=tenant_id,
        source="video-transcript",
    )
    # If the new fact is an identity/overview fact, refresh the always-on block.
    if (topic or "") == "overview":
        try:
            from agent_backend.rag.retriever import get_retriever

            get_retriever()._core_cache = None  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    return {"doc_id": res.doc_id, "point_ids": res.point_ids, "collection": collection, "patched": patched}
