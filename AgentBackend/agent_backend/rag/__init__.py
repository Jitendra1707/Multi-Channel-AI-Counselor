"""Qdrant hybrid RAG for the counsellor knowledge base.

The app QUERIES only — ingestion is a standalone script outside avatar-fe.
Retrieval is hybrid: OpenAI dense (text-embedding-3-large) + FastEmbed BM25
sparse, fused with RRF inside Qdrant, with per-chunk metadata + citations.

Public surface (what the brain uses):
    get_retriever()  → process-wide KnowledgeRetriever (Qdrant-backed)
    search(q, k)     → list[str] rendered snippets
    core_context()   → always-on UNIVERSITY block (overview chunks)

Test it standalone:
    python -m agent_backend.rag qdrant-query "fees for CSE AI&ML" --tenant sreenidhi
"""
from agent_backend.rag.retriever import (
    KnowledgeRetriever,
    core_context,
    get_retriever,
    search,
)

__all__ = ["KnowledgeRetriever", "core_context", "get_retriever", "search"]
