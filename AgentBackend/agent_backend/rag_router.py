"""RAG backend switch — selects the legacy float32 retriever or the TurboQuant
LangChain store, chosen by the RAG_BACKEND setting.

    RAG_BACKEND=legacy  (default)  -> agent_backend.rag         (raw float32 + BM25 + RRF + rerank)
    RAG_BACKEND=turbo              -> agent_backend.rag_turbo   (TurboQuant LangChain VectorStore)

Both backends expose `get_retriever()` / `core_context()` with a retriever that
has `search_text(query, k)` + `core_context()`. Callers import from HERE and
never care which is active. The TurboQuant store speaks a LangChain VectorStore
surface, so a thin adapter re-implements `search_text`/`core_context` on it.

The backend is resolved via `get_settings().rag_backend` (pydantic loads `.env`),
with an exported RAG_BACKEND env var winning as a one-off override.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any


def _backend() -> str:
    env = os.environ.get("RAG_BACKEND")
    if env:
        return env.strip().lower()
    try:
        from agent_backend.config import get_settings

        return str(get_settings().rag_backend).strip().lower()
    except Exception:
        return "legacy"


def _is_turbo() -> bool:
    return _backend() in ("turbo", "turboquant", "tq")


def backend_name() -> str:
    return "turbo" if _is_turbo() else "legacy"


def engine_label() -> str:
    """Backend + concrete engine, for logs/health:
    'turbo/turbovec', 'turbo/numpy-fallback', or 'legacy/float32'."""
    if not _is_turbo():
        return "legacy/float32"
    try:
        from agent_backend.rag_turbo import engine_name

        return f"turbo/{engine_name()}"
    except Exception:
        return "turbo/unknown"


# ---------------------------------------------------------------------------
# TurboQuant adapter: LangChain VectorStore -> the agent's retriever surface.
# ---------------------------------------------------------------------------
class _TurboRetrieverAdapter:
    """Gives a TurboQuant store the surface the agent expects: `search_text`,
    `core_context`."""

    def __init__(self, store: Any):
        self._store = store

    def search_text(self, query: str, k: int | None = None, **kw: Any) -> list[str]:
        query = (query or "").strip()
        if not query:
            return []
        out: list[str] = []
        for d in self._store.similarity_search(query, k=k or 4):
            heading = d.metadata.get("heading", "")
            out.append(f"[{heading}] {d.page_content}" if heading else d.page_content)
        return out

    def core_context(self) -> str | None:
        docs = self._store.get_by_ids(
            [i for i, (_t, m) in self._store._docs.items()
             if "overview" in str(m.get("heading", "")).lower()]
        )
        if not docs:
            return None
        body = "\n\n".join(d.page_content for d in docs)
        return "UNIVERSITY (authoritative facts — ground every claim in this block)\n" + body


@lru_cache(maxsize=1)
def _turbo_retriever() -> _TurboRetrieverAdapter:
    from agent_backend.rag_turbo import AgentEmbeddings, load_store
    from agent_backend.rag_turbo import settings as tq_settings

    store = load_store(tq_settings.INDEX_DIR, AgentEmbeddings())
    return _TurboRetrieverAdapter(store)


# ---------------------------------------------------------------------------
# Public surface (identical for both backends).
# ---------------------------------------------------------------------------
def get_retriever() -> Any:
    if _is_turbo():
        return _turbo_retriever()
    from agent_backend.rag import get_retriever as _g
    return _g()


def core_context() -> str | None:
    if _is_turbo():
        return _turbo_retriever().core_context()
    from agent_backend.rag import core_context as _c
    return _c()


def search(query: str, k: int | None = None, **kw: Any) -> list[str]:
    if _is_turbo():
        return _turbo_retriever().search_text(query, k=k, **kw)
    from agent_backend.rag import search as _s
    return _s(query, k=k, **kw)
