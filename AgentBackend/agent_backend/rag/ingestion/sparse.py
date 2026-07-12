"""Doc-side sparse (BM25) embedding via FastEmbed — matches the query side.

The query side (qdrant_store) uses `Qdrant/bm25` with `.query_embed(...)`. For
documents we use the SAME model with `.passage_embed(...)` (BM25 treats docs and
queries slightly differently). Same model name = compatible sparse vectors in
the `sparse` named vector, so Qdrant's IDF fusion works as intended.
"""
from __future__ import annotations

from typing import Any

from agent_backend.rag.ingestion import settings as ing_settings

_EMBEDDER: Any = None


def _embedder() -> Any:
    global _EMBEDDER
    if _EMBEDDER is None:
        try:
            from fastembed import SparseTextEmbedding
        except ModuleNotFoundError as e:  # pragma: no cover
            raise RuntimeError("fastembed is not installed. pip install -r requirements.txt") from e
        _EMBEDDER = SparseTextEmbedding(
            model_name=ing_settings.SPARSE_MODEL,
            # SAME stable project-local cache the query side uses (NOT %TEMP%) so
            # OS temp-cleanup never re-triggers a HuggingFace download and the
            # model is available in air-gapped pods. See settings.FASTEMBED_CACHE_PATH.
            cache_dir=ing_settings.FASTEMBED_CACHE_PATH,
        )
    return _EMBEDDER


def embed_passages(texts: list[str]) -> list[tuple[list[int], list[float]]]:
    """Return [(indices, values), ...] sparse vectors for the given passages."""
    emb = _embedder()
    out: list[tuple[list[int], list[float]]] = []
    for e in emb.passage_embed(texts):
        out.append((e.indices.tolist(), e.values.tolist()))
    return out
