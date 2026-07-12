"""RAG ingestion — the WRITE side of the Qdrant knowledge base.

The rest of `agent_backend.rag` is query-only; THIS subpackage is the missing
write path: parse a document → chunk → embed (dense + sparse) → upsert into the
Qdrant collection with the SAME named-vector contract the query side reads
(`dense` = DENSE_DIM Cosine, default 1024 for the local FastEmbed bge-large model
+ `sparse` IDF) and the same payload (`heading`, `text`, `topic`, `tenant_id`,
`source`, …).

Layout:
  settings.py        ingestion env knobs (target collection, parser tier, chunking)
  parsers/           pluggable document parsers (LlamaParse v2 default, pypdf fallback)
  chunker.py         token-based, table-atomic chunking + topic classification
  sparse.py          FastEmbed BM25 doc-side embedding (matches the query side)
  pipeline.py        parse → chunk → embed → upsert (create collection if missing)
  routes/            FastAPI router (POST /api/rag/ingest), mounted in main.py

Everything is driven by the env layer (RAG_* + LLAMA_CLOUD_*); see settings.py.
"""
from agent_backend.rag.ingestion.pipeline import IngestResult, ingest_file

__all__ = ["ingest_file", "IngestResult"]
