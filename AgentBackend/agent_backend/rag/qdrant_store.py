"""Qdrant hybrid retrieval store (dense + sparse, RRF-fused) — the ONLY store.

Query path (writes go through the ingestion subpackage; both sides embed with
the SAME local models — see agent_backend.rag.embedder):
  1. DENSE  — embed the query with LOCAL FastEmbed (ONNX), settings.DENSE_MODEL
     (default `BAAI/bge-large-en-v1.5`) → DENSE_DIM-d vector (default 1024).
     Strong semantic recall, no network call.
  2. SPARSE — embed the query with FastEmbed `Qdrant/bm25` (settings.SPARSE_MODEL)
     → sparse term vector. Catches exact fees/dates/course-codes in tables.
  3. FUSE   — Qdrant's Query API prefetches both arms and fuses with RRF
     server-side, returning the top-k. Same fusion idea as the retired .npz
     path, now done inside Qdrant.

The collection has TWO named vectors: `dense` (size = DENSE_DIM, Cosine) and
`sparse` (modifier=IDF). Multi-tenant: every point carries {source, tenant_id};
search always filters by the active tenant + source.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_backend.rag import settings as rag_settings
from agent_backend.rag.embedder import embed_query

try:
    from agent_backend.infra import get_logger

    log = get_logger(__name__)
except Exception:  # noqa: BLE001 — keep the package importable standalone
    import logging

    log = logging.getLogger("agent_backend.rag")

# Process-wide FastEmbed sparse model — loaded once (tiny; tokenizer-like).
_SPARSE_EMBEDDER: Any = None


@dataclass(frozen=True)
class Hit:
    """One retrieved chunk. `score` is the RRF-fused score from Qdrant."""

    score: float
    heading: str
    text: str
    meta: dict[str, Any] = field(default_factory=dict)
    # Qdrant point id of this chunk. Optional/default so existing call sites and
    # tests that build a Hit without it keep working; populated by `search()` so
    # callers (e.g. knowledge-capture conflict analysis) can supersede/delete the
    # exact point later.
    point_id: str | None = None

    def citation(self) -> str:
        m = self.meta
        return f"{m.get('source_doc','?')} §{self.heading or m.get('section','')} (v{m.get('version','?')})"

    def render(self) -> str:
        """One snippet string for a prompt slot: '[heading] text'."""
        return f"[{self.heading}] {self.text}" if self.heading else self.text


def _models():
    """Lazy import of qdrant_client so importing this package doesn't hard-require
    it until a query actually runs."""
    try:
        from qdrant_client import QdrantClient, models
    except ModuleNotFoundError as e:  # pragma: no cover
        raise RuntimeError(
            "qdrant-client is not installed. pip install -r requirements.txt"
        ) from e
    return QdrantClient, models


def _sparse_query(query: str) -> tuple[list[int], list[float]]:
    """Embed the query into a BM25 sparse vector via FastEmbed. Returns
    (indices, values) for a Qdrant SparseVector. Lazy-loads the model once."""
    global _SPARSE_EMBEDDER
    if _SPARSE_EMBEDDER is None:
        try:
            from fastembed import SparseTextEmbedding
        except ModuleNotFoundError as e:  # pragma: no cover
            raise RuntimeError(
                "fastembed is not installed. pip install -r requirements.txt"
            ) from e
        _SPARSE_EMBEDDER = SparseTextEmbedding(
            model_name=rag_settings.SPARSE_MODEL,
            # Stable project-local cache (NOT %TEMP%) so OS temp-cleanup never
            # re-triggers a HuggingFace download; bake into the Docker image for
            # air-gapped pods (see settings.FASTEMBED_CACHE_PATH note).
            cache_dir=rag_settings.FASTEMBED_CACHE_PATH,
        )
    emb = next(iter(_SPARSE_EMBEDDER.query_embed(query)))
    return emb.indices.tolist(), emb.values.tolist()


def warmup_sparse() -> None:
    """Load the FastEmbed BM25 model at server boot instead of on the first
    live query — the lazy first-load costs seconds (and, first-ever run, a
    HuggingFace download), which otherwise lands on a candidate's first turn.
    Best-effort: on failure the first query just retries lazily as before."""
    try:
        _sparse_query("warmup")
        log.info("[rag] sparse model warmed", model=rag_settings.SPARSE_MODEL)
    except Exception as e:  # noqa: BLE001
        log.warning("[rag] sparse warmup failed — first query will retry", err=str(e)[:200])


class QdrantStore:
    """Active-tenant hybrid view over the shared Qdrant collection."""

    def __init__(self, client: Any, *, collection: str, tenant_id: str, source: str) -> None:
        self._client = client
        self._collection = collection
        self._tenant_id = tenant_id
        self._source = source

    @property
    def collection(self) -> str:
        return self._collection

    @classmethod
    def connect(
        cls,
        *,
        collection: str | None = None,
        tenant_id: str | None = None,
        source: str | None = None,
    ) -> "QdrantStore":
        QdrantClient, _ = _models()
        client = QdrantClient(
            url=rag_settings.QDRANT_URL, api_key=rag_settings.QDRANT_API_KEY or None
        )
        return cls(
            client,
            collection=collection or rag_settings.QDRANT_COLLECTION,
            tenant_id=tenant_id or rag_settings.DEFAULT_TENANT,
            source=source or rag_settings.DEFAULT_SOURCE,
        )

    # --- filters ----------------------------------------------------------
    def _base_conditions(self) -> list[Any]:
        """Tenant/source isolation — ONLY when configured. Empty tenant/source
        (the single-tenant default) means no isolation filter, so a plain
        collection without those payload fields searches normally."""
        _, models = _models()
        conds: list[Any] = []
        if self._tenant_id:
            conds.append(models.FieldCondition(key="tenant_id", match=models.MatchValue(value=self._tenant_id)))
        if self._source:
            conds.append(models.FieldCondition(key="source", match=models.MatchValue(value=self._source)))
        return conds

    def _build_filter(self, where: dict[str, Any] | None) -> Any:
        """Returns a Qdrant Filter, or None when there's nothing to filter on
        (no tenant/source isolation and no caller `where`) — None = match all."""
        _, models = _models()
        must = self._base_conditions()
        for key, val in (where or {}).items():
            must.append(models.FieldCondition(key=key, match=models.MatchValue(value=val)))
        return models.Filter(must=must) if must else None

    # --- query ------------------------------------------------------------
    def search(
        self,
        query: str,
        k: int | None = None,
        *,
        hybrid: bool | None = None,
        rerank: bool | None = None,
        where: dict[str, Any] | None = None,
        dense_vec: Any = None,  # precomputed query embedding (reused from router)
        sparse_only: bool = False,  # degraded mode: BM25 only (dense embed unavailable)
    ) -> list[Hit]:
        query = (query or "").strip()
        if not query:
            return []
        k = k or rag_settings.TOP_K
        hybrid = True if hybrid is None else hybrid
        rerank = rag_settings.RERANK if rerank is None else rerank
        _, models = _models()

        flt = self._build_filter(where)
        pool = rag_settings.CANDIDATE_POOL
        limit = max(k, rag_settings.RERANK_POOL) if rerank else k

        # Degraded mode: BM25 sparse arm ONLY — used as the fallback when the dense
        # embed is unavailable (no network / provider error). No dense vector
        # needed; still answers exact fees/dates/course-codes from the tables.
        if sparse_only:
            sp_idx, sp_val = _sparse_query(query)
            result = self._client.query_points(
                collection_name=self._collection,
                query=models.SparseVector(indices=sp_idx, values=sp_val),
                using=rag_settings.QDRANT_SPARSE_VECTOR,
                query_filter=flt,
                limit=k,
                with_payload=True,
            )
            return [
                Hit(
                    score=float(p.score),
                    heading=(p.payload or {}).get("heading", ""),
                    text=(p.payload or {}).get("text", ""),
                    meta=(p.payload or {}),
                    point_id=str(p.id),
                )
                for p in result.points
            ][:k]

        # Reuse the query embedding the router already computed when given; only
        # embed here if the caller didn't (e.g. the standalone CLI path).
        if dense_vec is None:
            qvec = embed_query(query, model=rag_settings.DENSE_MODEL).tolist()
        elif hasattr(dense_vec, "tolist"):
            qvec = dense_vec.tolist()
        else:
            qvec = list(dense_vec)

        if hybrid:
            # Prefetch dense + sparse arms, fuse with RRF server-side.
            sp_idx, sp_val = _sparse_query(query)
            result = self._client.query_points(
                collection_name=self._collection,
                prefetch=[
                    models.Prefetch(
                        query=qvec,
                        using=rag_settings.QDRANT_DENSE_VECTOR,
                        filter=flt,
                        limit=pool,
                    ),
                    models.Prefetch(
                        query=models.SparseVector(indices=sp_idx, values=sp_val),
                        using=rag_settings.QDRANT_SPARSE_VECTOR,
                        filter=flt,
                        limit=pool,
                    ),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=limit,
                with_payload=True,
            )
        else:
            # Dense-only escape hatch (A/B testing).
            result = self._client.query_points(
                collection_name=self._collection,
                query=qvec,
                using=rag_settings.QDRANT_DENSE_VECTOR,
                query_filter=flt,
                limit=limit,
                with_payload=True,
            )

        hits: list[Hit] = []
        for p in result.points:
            payload = p.payload or {}
            hits.append(
                Hit(
                    score=float(p.score),
                    heading=payload.get("heading", ""),
                    text=payload.get("text", ""),
                    meta=payload,
                    point_id=str(p.id),
                )
            )

        # Optional LLM rerank of the fused shortlist.
        if rerank and len(hits) > 1:
            from agent_backend.rag.rerank import rerank as llm_rerank

            order = llm_rerank(query, [h.render() for h in hits], top_k=k)
            hits = [hits[j] for j in order] + [
                h for idx, h in enumerate(hits) if idx not in set(order)
            ]

        return hits[:k]

    def overview_texts(self) -> list[str]:
        """Always-on UNIVERSITY core block: chunks with topic == 'overview'.

        FALLBACK: the standalone ingestion script's topic classifier doesn't
        always emit 'overview' (observed: the University Overview section landed
        under 'governance'), which silently emptied the UNIVERSITY block. If the
        topic filter finds nothing, scan headings/sections for 'overview'
        client-side — the collection is small and this runs once per TTL window
        (the retriever caches the result), not per turn."""
        _, models = _models()
        flt = models.Filter(
            must=[
                *self._base_conditions(),
                models.FieldCondition(key="topic", match=models.MatchValue(value="overview")),
            ]
        )
        points, _next = self._client.scroll(
            collection_name=self._collection, scroll_filter=flt, with_payload=True, limit=64
        )
        texts = [(p.payload or {}).get("text", "") for p in points if p.payload]
        if texts:
            return texts

        # Fallback: heading/section contains "overview" (case-insensitive).
        base = self._base_conditions()
        all_points, _next = self._client.scroll(
            collection_name=self._collection,
            scroll_filter=models.Filter(must=base) if base else None,
            with_payload=True,
            limit=256,
        )
        def _is_overview(payload: dict) -> bool:
            hay = f"{payload.get('heading','')} {payload.get('section','')}".lower()
            return "overview" in hay

        ordered = sorted(
            (p for p in all_points if p.payload and _is_overview(p.payload)),
            key=lambda p: p.id if isinstance(p.id, int) else 0,
        )
        return [(p.payload or {}).get("text", "") for p in ordered]

    def __len__(self) -> int:
        _, models = _models()
        flt = models.Filter(must=self._base_conditions())
        return self._client.count(
            collection_name=self._collection, count_filter=flt, exact=True
        ).count
