"""Ingestion configuration — all from the env layer.

Borrows Qdrant connection + embedding-model settings from the query-side
`agent_backend.rag.settings` (so ingest and query stay in lockstep), and adds
the write-only knobs: target collection, LlamaParse v2 credentials/tier, and
chunking parameters.
"""
from __future__ import annotations

import os

# Reuse the query side's Qdrant + model contract verbatim (single source of
# truth — ingest MUST embed with the same models the query side uses).
from agent_backend.rag.settings import (  # noqa: F401
    DENSE_DIM,
    DENSE_MODEL,
    DENSE_PROVIDER,
    FASTEMBED_CACHE_PATH,
    QDRANT_API_KEY,
    QDRANT_COLLECTION,
    QDRANT_DENSE_VECTOR,
    QDRANT_SPARSE_VECTOR,
    QDRANT_URL,
    SPARSE_MODEL,
)

# --- Target collection ----------------------------------------------------
# Where ingested points land. Defaults to the SAME collection the agent QUERIES
# (RAG_QDRANT_COLLECTION) so uploaded docs are immediately searchable by the
# avatar with NO extra env key — you only manage the existing 3 RAG_QDRANT_*
# keys. Set RAG_INGEST_COLLECTION only if you deliberately want uploads in a
# SEPARATE collection from the agent's KB.
INGEST_COLLECTION = os.environ.get("RAG_INGEST_COLLECTION", QDRANT_COLLECTION)

# Payload tenancy/source stamped on every ingested point so the multi-tenant
# query filters work. Blank tenant = single-tenant (no isolation filter).
INGEST_TENANT = os.environ.get("RAG_INGEST_TENANT", os.environ.get("RAG_DEFAULT_TENANT", ""))
INGEST_SOURCE = os.environ.get("RAG_INGEST_SOURCE", "uploaded")

# --- LlamaParse v2 (default PDF parser) -----------------------------------
# Hosted parser via the `llama-cloud` SDK. Needs an API key; without it the
# pipeline falls back to local pypdf (see parsers/). Tier: fast | cost_effective
# | agentic | agentic_plus (v2 tiers). 'agentic' is the strong table-aware
# default — good for fee tables / brochures.
LLAMA_CLOUD_API_KEY = os.environ.get("LLAMA_CLOUD_API_KEY", "")
LLAMAPARSE_TIER = os.environ.get("RAG_LLAMAPARSE_TIER", "agentic")
LLAMAPARSE_VERSION = os.environ.get("RAG_LLAMAPARSE_VERSION", "latest")
# "auto" → LlamaParse when a key is set, else pypdf. Force with "llamaparse" or
# "local".
PARSER = os.environ.get("RAG_PARSER", "auto").strip().lower()

# --- Chunking (matches the query side's general band) ---------------------
# Token-based, section-first, tables kept atomic. ~450-token target with ~12%
# overlap (the band the team validated for these documents).
CHUNK_TARGET_TOKENS = int(os.environ.get("RAG_CHUNK_TARGET_TOKENS", "450"))
CHUNK_OVERLAP_TOKENS = int(os.environ.get("RAG_CHUNK_OVERLAP_TOKENS", "60"))
CHUNK_HARD_MAX_TOKENS = int(os.environ.get("RAG_CHUNK_HARD_MAX_TOKENS", "1100"))

# --- Upload limits / accepted types (mirrors the FE) ----------------------
MAX_UPLOAD_MB = int(os.environ.get("RAG_MAX_UPLOAD_MB", "500"))
ACCEPTED_EXTS = {"pdf", "txt", "md", "markdown"}

# Temp spool dir — a file is written here only so the parser can read it, then
# DELETED right after ingestion (success or failure). Nothing accumulates here.
UPLOAD_DIR = os.environ.get(
    "RAG_UPLOAD_DIR",
    str(__import__("pathlib").Path(__file__).resolve().parents[3] / "uploads"),
)


def llama_cloud_key() -> str:
    return LLAMA_CLOUD_API_KEY
