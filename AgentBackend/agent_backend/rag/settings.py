"""RAG configuration — Qdrant hybrid retrieval (dense + sparse).

Standalone from `agent_backend.config`. Both the dense and sparse arms now run on
LOCAL FastEmbed models (no external embedding API), so this package is
self-contained apart from the optional LLM rerank.

Embedding choice:
  - DENSE  = LOCAL FastEmbed (ONNX), `BAAI/bge-large-en-v1.5` (1024-dim) by
    default. Replaced OpenAI `text-embedding-3-large` (3072-dim): comparable
    recall on this hybrid KB, ~68ms local steady-state vs a remote ~150-500ms
    call, and no network/API dependency. Swap models via DENSE_MODEL + DENSE_DIM,
    then recreate the collection at the new size and re-ingest.
  - SPARSE = FastEmbed `Qdrant/bm25` (keyword/BM25). This is what nails exact
    fees/dates/course-codes in tables. Fused with the dense results via RRF
    inside Qdrant.

Documents are written by the `agent_backend.rag.ingestion` subpackage; the query
side here must use the SAME models the documents were embedded with (it does —
both import the shared `embedder`/`sparse`), or retrieval is meaningless.
"""
from __future__ import annotations

import os
from pathlib import Path

# This module reads its RAG_* knobs straight from os.environ — but the app's
# config lives in AgentBackend/.env, which pydantic loads into its Settings
# OBJECT, not into os.environ. So without this, RAG_QDRANT_URL (and friends)
# from .env are invisible here and we'd silently fall back to the localhost
# defaults below (→ connection-refused against a remote Qdrant). Load that .env
# into os.environ now. override=False so real OS env vars still win.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
except Exception:  # noqa: BLE001 — .env is optional; flags/real env still work
    pass

# --- Qdrant connection ----------------------------------------------------
# URL of the Qdrant you deployed. For Qdrant Cloud, set RAG_QDRANT_API_KEY too.
QDRANT_URL = os.environ.get("RAG_QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.environ.get("RAG_QDRANT_API_KEY", "")  # blank for self-hosted
# ONE collection holds every source + every university; rows are isolated by the
# `source` and `tenant_id` payload fields (Qdrant's multi-tenant pattern).
# Created MANUALLY in the UI (Custom: dense 3072 Cosine + sparse idf) or by the
# ingestion script's --create-collection. The app only queries it.
QDRANT_COLLECTION = os.environ.get("RAG_QDRANT_COLLECTION", "aegis_kb")
# General KB collection (education industry / concepts / trends), a SECOND
# collection ingested with the same script. Set RAG_QDRANT_GENERAL_COLLECTION to
# its name to ENABLE query routing (university vs general). Leave empty to
# disable — then every query goes to QDRANT_COLLECTION (university) as before.
QDRANT_GENERAL_COLLECTION = os.environ.get("RAG_QDRANT_GENERAL_COLLECTION", "")
# Named vectors in the collection — the contract shared with the ingestion
# script. Hybrid needs BOTH named (a single default vector can't carry sparse).
QDRANT_DENSE_VECTOR = os.environ.get("RAG_QDRANT_DENSE_VECTOR", "dense")
QDRANT_SPARSE_VECTOR = os.environ.get("RAG_QDRANT_SPARSE_VECTOR", "sparse")

# --- Embedding models -----------------------------------------------------
# DENSE: LOCAL FastEmbed (ONNX) — no network, no external API dependency. OpenAI
# dense embeddings were REMOVED (benchmarked ~68ms local steady-state, no cold
# start, vs a remote ~150-500ms call, with comparable recall on this hybrid KB).
# DENSE_MODEL must be a FastEmbed-supported dense model; the queried collection's
# `dense` vector size MUST equal DENSE_DIM.
DENSE_MODEL = os.environ.get("RAG_DENSE_MODEL", "BAAI/bge-large-en-v1.5")
DENSE_DIM = int(os.environ.get("RAG_DENSE_DIM", "1024"))
# Retained for forward-compat + logs; only "fastembed" is wired (openai removed).
DENSE_PROVIDER = os.environ.get("RAG_DENSE_PROVIDER", "fastembed").strip().lower()
# SPARSE: FastEmbed BM25 vectorizer (tiny, CPU, no neural weights). The
# collection's sparse vector uses modifier=IDF so Qdrant computes global IDF.
SPARSE_MODEL = os.environ.get("RAG_SPARSE_MODEL", "Qdrant/bm25")
# Where FastEmbed caches the downloaded BM25 model. Default is a STABLE
# project-local dir — fastembed's own default is %TEMP%/fastembed_cache, which
# OS temp-cleanup purges and silently re-triggers a HuggingFace download on the
# next boot (or fails in an air-gapped pod). For Docker images, bake the model
# at build time:  ENV FASTEMBED_CACHE_PATH=/app/.fastembed_cache  +
# RUN python -c "from fastembed import SparseTextEmbedding; SparseTextEmbedding('Qdrant/bm25')"
# Relative values (e.g. "./.fastembed_cache" from .env) are anchored to the
# PROJECT ROOT, not the process CWD — uvicorn may be launched from anywhere.
_cache_env = os.environ.get("FASTEMBED_CACHE_PATH", "")
_cache_path = Path(_cache_env) if _cache_env else Path(".fastembed_cache")
if not _cache_path.is_absolute():
    _cache_path = Path(__file__).resolve().parents[2] / _cache_path
FASTEMBED_CACHE_PATH = str(_cache_path)

# --- Hybrid retrieval -----------------------------------------------------
# Dense + sparse candidate lists fused with Reciprocal Rank Fusion (RRF) inside
# Qdrant. CANDIDATE_POOL is the per-arm prefetch depth before fusion.
TOP_K = int(os.environ.get("RAG_TOP_K", "4"))
CANDIDATE_POOL = int(os.environ.get("RAG_CANDIDATE_POOL", "20"))

# --- Debug ----------------------------------------------------------------
# When true, the retriever logs each retrieved chunk every turn ([rag] hit
# lines: score, topic, heading, FULL text). Default OFF: it's noisy and puts KB
# content in production logs. Set RAG_DEBUG=1 while tuning retrieval.
RAG_DEBUG = os.environ.get("RAG_DEBUG", "0") not in ("0", "false", "False")

# --- Source routing (embedding-based) -------------------------------------
# The router embeds the query and compares it to representative anchor phrases
# for each KB (university vs general). It routes to GENERAL only when general's
# similarity beats university's by at least this margin; otherwise it defaults
# to university. Raise to bias harder toward university; lower to let general
# win more easily. The query embedding is REUSED from the dense search, so
# routing adds no extra embedding call.
ROUTE_MARGIN = float(os.environ.get("RAG_ROUTE_MARGIN", "0.02"))

# --- Reranker (optional; off by default to protect latency) ---------------
# LLM cross-encoder-style rerank of the fused shortlist (~300-600ms gpt-4o-mini).
RERANK = os.environ.get("RAG_RERANK", "0") not in ("0", "false", "False")
RERANK_MODEL = os.environ.get("RAG_RERANK_MODEL", "gpt-4o-mini")
RERANK_POOL = int(os.environ.get("RAG_RERANK_POOL", "12"))

# --- Tenancy (OFF by default — single-tenant) -----------------------------
# Empty = NO filtering: every query searches the whole collection. This is the
# right setting for one university in a plain collection.
# To turn ON multi-tenant isolation later (multiple universities in one
# collection), set RAG_DEFAULT_TENANT to the university id you ingested under
# (and optionally RAG_DEFAULT_SOURCE). The search then filters by those payload
# fields. No re-ingest needed if your points already carry tenant_id/source.
DEFAULT_TENANT = os.environ.get("RAG_DEFAULT_TENANT", "")  # "" = no tenant filter
DEFAULT_SOURCE = os.environ.get("RAG_DEFAULT_SOURCE", "")  # "" = no source filter

# --- Guardrail copy (for format_context) ----------------------------------
ADMISSIONS_EMAIL = os.environ.get("RAG_ADMISSIONS_EMAIL", "admissions@suh.edu.in")
LIVE_PORTAL = os.environ.get("RAG_LIVE_PORTAL", "apply.suh.edu.in")


def openai_credentials() -> tuple[str, str]:
    """(api_key, base_url) from the app settings so the dense query embedding
    uses the same OpenAI key as the rest of the stack. Imported lazily so this
    module stays usable in isolation."""
    from agent_backend.config import get_settings

    s = get_settings()
    return s.llm_api_key, s.llm_api_url
