"""DENSE embeddings (query + document side) — LOCAL FastEmbed (ONNX).

OpenAI dense embeddings were REMOVED. The local model runs with no network and no
external API dependency, cutting the per-query embed from a remote ~150-500ms call
to ~68ms local (steady-state, no cold start once warmed at boot). The hybrid
dense+sparse+RRF retrieval is unchanged; the sparse (BM25) arm lives in
`qdrant_store`. `RAG_DENSE_MODEL` selects the FastEmbed dense model (default
`BAAI/bge-large-en-v1.5`, 1024-d) and `RAG_DENSE_DIM` must match the queried
collection's `dense` vector size.

Shared by the query path AND the ingestion pipeline, so both embed with the same
local model — keeping query and document vectors in one space. Returns
L2-normalised float32 vectors.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from agent_backend.rag import settings as rag_settings

# Local FastEmbed dense models, keyed by model name. Loaded once per model (first
# use loads the weights from FASTEMBED_CACHE_PATH, downloading them there if
# absent — the same stable cache the BM25 sparse model uses; bake into the Docker
# image for prod, see settings.FASTEMBED_CACHE_PATH).
_FASTEMBED_CACHE: dict[str, Any] = {}


def _model(model: str) -> Any:
    m = _FASTEMBED_CACHE.get(model)
    if m is None:
        try:
            from fastembed import TextEmbedding
        except ModuleNotFoundError as e:  # pragma: no cover
            raise RuntimeError(
                "fastembed is not installed. pip install -r requirements.txt"
            ) from e
        m = TextEmbedding(model_name=model, cache_dir=rag_settings.FASTEMBED_CACHE_PATH)
        _FASTEMBED_CACHE[model] = m
    return m


def _normalize(mat: np.ndarray) -> np.ndarray:
    if mat.size == 0:
        return mat.astype(np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (mat / norms).astype(np.float32)


def embed_texts(texts: list[str], *, model: str | None = None) -> np.ndarray:
    """Embed PASSAGES/documents (ingestion + the router's anchor magnets) →
    (n, dim) L2-normalised float32 matrix."""
    texts = list(texts)
    if not texts:
        return np.zeros((0, rag_settings.DENSE_DIM), dtype=np.float32)
    m = _model(model or rag_settings.DENSE_MODEL)
    return _normalize(np.array(list(m.embed(texts)), dtype=np.float32))


def embed_query(text: str, *, model: str | None = None) -> np.ndarray:
    """Embed a single QUERY → (dim,) L2-normalised float32 vector. Uses the
    model's query encoder (e.g. bge's retrieval query prefix) so it pairs
    correctly with the passage-embedded documents in the collection."""
    m = _model(model or rag_settings.DENSE_MODEL)
    return _normalize(np.array([next(iter(m.query_embed(text)))], dtype=np.float32))[0]


def warmup_dense() -> None:
    """Load the dense model at boot (parallels `qdrant_store.warmup_sparse`) so
    the first live query doesn't pay the lazy load / first-ever download.
    Best-effort: on failure the first query just loads lazily as before."""
    try:
        embed_query("warmup")
        try:
            from agent_backend.infra import get_logger

            get_logger(__name__).info(
                f"[rag] dense embedder warmed (provider={rag_settings.DENSE_PROVIDER}, "
                f"model={rag_settings.DENSE_MODEL}, dim={rag_settings.DENSE_DIM})"
            )
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        try:
            from agent_backend.infra import get_logger

            get_logger(__name__).warning(
                "[rag] dense warmup failed — first query will retry", err=str(e)[:200]
            )
        except Exception:  # noqa: BLE001
            pass
