"""Public retriever surface — Qdrant hybrid, multi-source routed.

`KnowledgeRetriever` holds the UNIVERSITY store (Sreenidhi: fees, courses, …)
and, when configured, a GENERAL store (education industry / concepts / trends).
Each query is routed to ONE source by `router.route_source` (keyword-based,
university-default), and the chosen source + collection are logged so you can
see which RAG fired. The brain calls this via `get_retriever()` /
`search()` / `core_context()` and is unaware of the routing.

`format_context()` renders retrieved chunks into a prompt block WITH citations
and the high-stakes guardrail (answer fees/dates only from context; otherwise
point to admissions / the live portal).
"""
from __future__ import annotations

import time
from functools import lru_cache
from typing import Any

from agent_backend.rag import settings as rag_settings
from agent_backend.rag.embedder import embed_query
from agent_backend.rag.qdrant_store import Hit, QdrantStore
from agent_backend.rag.router import route_source

try:
    from agent_backend.infra import get_logger

    log = get_logger(__name__)
except Exception:  # noqa: BLE001 — keep the package importable standalone
    import logging

    log = logging.getLogger("agent_backend.rag")


class KnowledgeRetriever:
    # The overview block only changes when the KB is re-ingested, so refreshing
    # every 10 min is plenty — and saves a Qdrant scroll on EVERY turn.
    _CORE_TTL_S = 600.0

    def __init__(self, university: QdrantStore, general: QdrantStore | None = None):
        self._university = university
        self._general = general  # None = general KB disabled (university only)
        self._core_cache: tuple[float, str | None] | None = None  # (fetched_at, text)

    def search(
        self,
        query: str,
        k: int | None = None,
        *,
        hybrid: bool | None = None,
        rerank: bool | None = None,
        where: dict[str, Any] | None = None,
        source: str | None = None,  # force a source; None = route by query
    ) -> list[Hit]:
        query = (query or "").strip()
        if not query:
            return []

        # Embed the query ONCE: used for both routing (semantic match to source
        # anchors) and the dense search arm, so routing adds no extra embed call.
        try:
            qvec = embed_query(query, model=rag_settings.DENSE_MODEL)
        except Exception as e:  # noqa: BLE001
            # Degraded mode: the dense embed is unavailable (network / provider
            # error). The BM25 sparse arm alone still answers exact-figure
            # questions (fees / dates / course codes), so fall back to a
            # sparse-only search on the primary (university) store rather than
            # dropping RAG entirely. Routing needs the dense vector, so we skip it.
            log.warning("[rag] query embed failed — sparse-only fallback (BM25)", err=str(e))
            try:
                hits = self._university.search(query, k=k, where=where, sparse_only=True)
                log.info(
                    "[rag] sparse-only fallback",
                    collection=self._university.collection,
                    n=len(hits),
                )
                return hits
            except Exception as e2:  # noqa: BLE001
                log.warning("[rag] sparse-only fallback failed — RAG skipped", err=str(e2))
                return []

        if source is None:
            source, reason = route_source(
                query, general_available=self._general is not None, query_vec=qvec
            )
        else:
            reason = "forced"

        store = (
            self._general
            if (source == "general" and self._general is not None)
            else self._university
        )
        if source == "general" and self._general is None:
            source = "university"  # general not configured → fall back

        # >>> the log you asked for: which RAG fired, on which collection, why.
        log.info(
            "[rag] route",
            source=source,
            collection=store.collection,
            reason=reason,
            query=query[:80],
        )

        try:
            hits = store.search(
                query, k=k, hybrid=hybrid, rerank=rerank, where=where, dense_vec=qvec
            )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "[rag] search failed",
                source=source,
                collection=store.collection,
                err=str(e),
            )
            # If the GENERAL store failed (e.g. collection not ingested yet),
            # fall back to the university store rather than returning nothing.
            if store is self._general:
                try:
                    hits = self._university.search(
                        query, k=k, hybrid=hybrid, rerank=rerank, where=where, dense_vec=qvec
                    )
                    source, store = "university(fallback)", self._university
                except Exception as e2:  # noqa: BLE001
                    log.warning("[rag] university fallback also failed", err=str(e2))
                    return []
            else:
                return []

        log.info("[rag] hits", source=source, collection=store.collection, n=len(hits))

        # Debug: print the actual retrieved chunks so you can see exactly what
        # context the LLM received this turn. Toggle off with RAG_DEBUG=0.
        if rag_settings.RAG_DEBUG and hits:
            for i, h in enumerate(hits, 1):
                log.info(
                    "[rag] hit",
                    rank=i,
                    score=round(h.score, 4),
                    topic=h.meta.get("topic"),
                    heading=h.heading,
                    text=h.text,
                )

        return hits

    def search_text(self, query: str, k: int | None = None, **kw: Any) -> list[str]:
        """Rendered snippets, highest-relevance first."""
        return [h.render() for h in self.search(query, k=k, **kw)]

    def core_context(self) -> str | None:
        """Always-on UNIVERSITY block — the 'overview' chunks from the UNIVERSITY
        KB only (it's the bot's identity; the general KB has no 'who we are').

        TTL-cached: `_render_university` calls this EVERY turn, and the scroll is
        a network round-trip to Qdrant for content that only changes on re-ingest.
        On a fetch failure we serve the STALE cached block rather than dropping
        the university identity from the prompt for that turn."""
        now = time.monotonic()
        if self._core_cache is not None and (now - self._core_cache[0]) < self._CORE_TTL_S:
            return self._core_cache[1]
        try:
            texts = self._university.overview_texts()
            text = (
                "UNIVERSITY (authoritative facts — ground every claim in this block)\n"
                + "\n\n".join(texts)
            ) if texts else None
        except Exception as e:  # noqa: BLE001 — stale beats none on the hot path
            log.warning("[rag] core_context fetch failed — serving cached", err=str(e)[:160])
            return self._core_cache[1] if self._core_cache is not None else None
        self._core_cache = (now, text)
        return text

    def format_context(self, query: str, k: int | None = None, **kw: Any) -> str:
        """Prompt-ready context block with citations + accuracy guardrail.
        Use this when wiring into the agent so high-stakes answers stay grounded."""
        hits = self.search(query, k=k, **kw)
        if not hits:
            return ""
        lines = [
            "KNOWLEDGE BASE (answer ONLY from the passages below; for fees, "
            "scholarships, deadlines and exact dates do not guess — if the figure "
            f"isn't here, tell the student to confirm with {rag_settings.ADMISSIONS_EMAIL} "
            f"or the live portal {rag_settings.LIVE_PORTAL}):",
            "",
        ]
        for n, h in enumerate(hits, 1):
            tag = " [verify-live]" if (h.meta.get("date_sensitive") or h.meta.get("fee_sensitive")) else ""
            lines.append(f"[{n}] {h.heading}{tag}")
            lines.append(h.text)
            lines.append(f"(source: {h.citation()})")
            lines.append("")
        return "\n".join(lines).strip()

    def __len__(self) -> int:
        return len(self._university)


@lru_cache(maxsize=1)
def get_retriever() -> KnowledgeRetriever:
    """Process-wide singleton retriever. University store always; general store
    only when RAG_QDRANT_GENERAL_COLLECTION is set (else routing is a no-op and
    every query goes to the university collection — the previous behaviour)."""
    university = QdrantStore.connect(collection=rag_settings.QDRANT_COLLECTION)
    general = None
    if rag_settings.QDRANT_GENERAL_COLLECTION:
        general = QdrantStore.connect(collection=rag_settings.QDRANT_GENERAL_COLLECTION)
    log.info(
        "[rag] retriever ready",
        university=rag_settings.QDRANT_COLLECTION,
        general=rag_settings.QDRANT_GENERAL_COLLECTION or "(disabled)",
    )
    return KnowledgeRetriever(university, general)


def warmup_rag() -> None:
    """Pre-pay every first-turn cold cost at SERVER BOOT instead of on a live
    candidate's first question. Called from main.py's lifespan in a background
    thread; each step is independently best-effort (a failed step just degrades
    to today's lazy-load behaviour). Warms:
      1. the FastEmbed BM25 sparse model (load / first-ever download),
      2. the Qdrant clients (retriever singleton),
      3. the router's magnet embeddings (one batched OpenAI call),
      4. the core UNIVERSITY block (fills the TTL cache).
    """
    t0 = time.monotonic()
    steps: list[str] = []
    try:
        from agent_backend.rag.qdrant_store import warmup_sparse

        warmup_sparse()
        steps.append("sparse-model")
    except Exception as e:  # noqa: BLE001
        log.warning("[rag] warmup: sparse failed", err=str(e)[:160])
    try:
        # Dense embedder: for provider=fastembed this loads the local ONNX model
        # (and, first-ever run, downloads it) so the first live query doesn't pay
        # it. For provider=openai it's a cheap warm round-trip. Best-effort.
        from agent_backend.rag.embedder import warmup_dense

        warmup_dense()
        steps.append("dense-model")
    except Exception as e:  # noqa: BLE001
        log.warning("[rag] warmup: dense failed", err=str(e)[:160])
    try:
        retriever = get_retriever()
        steps.append("qdrant-clients")
    except Exception as e:  # noqa: BLE001
        log.warning("[rag] warmup: retriever failed", err=str(e)[:160])
        return
    try:
        from agent_backend.rag.router import _magnets

        _magnets()
        steps.append("router-magnets")
    except Exception as e:  # noqa: BLE001
        log.warning("[rag] warmup: magnets failed", err=str(e)[:160])
    try:
        retriever.core_context()
        steps.append("core-context")
    except Exception as e:  # noqa: BLE001
        log.warning("[rag] warmup: core_context failed", err=str(e)[:160])
    log.info(
        "[rag] warmup done",
        steps=steps,
        ms=int((time.monotonic() - t0) * 1000),
    )


def search(query: str, k: int | None = None, **kw: Any) -> list[str]:
    """Convenience: rendered snippets, highest-relevance first."""
    return get_retriever().search_text(query, k=k or rag_settings.TOP_K, **kw)


def core_context() -> str | None:
    """Convenience: always-on UNIVERSITY block from the KB overview."""
    return get_retriever().core_context()
