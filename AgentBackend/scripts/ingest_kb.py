"""Ingest a knowledge-base document into a Qdrant collection.

Thin CLI over the existing ingestion pipeline (parse -> chunk -> embed -> upsert,
`agent_backend.rag.ingestion.pipeline.ingest_file`). Its purpose: ingest into a
**NEW** collection — e.g. when switching the dense embedding model — WITHOUT
touching the collection the live app currently queries, so the change is fully
reversible (point the app's RAG_QDRANT_COLLECTION back to the old one to roll
back).

The embedding PROVIDER / MODEL / DIM are taken from the environment (the same
RAG_* knobs the app reads via agent_backend.rag.settings), so the new collection
is created at the right vector size with the right embeddings.

Examples
--------
# Re-ingest the Sreenidhi KB with the LOCAL FastEmbed model into a NEW bge
# collection (the live srinidhi-kb at 3072 is left intact):
#   (run from the AgentBackend/ directory)
RAG_DENSE_PROVIDER=fastembed RAG_DENSE_MODEL=BAAI/bge-large-en-v1.5 RAG_DENSE_DIM=1024 \
  python scripts/ingest_kb.py knowledge-base/sreenidhi_knowledge_base.md \
    --collection srinidhi-kb-bge

# Same, on Windows PowerShell:
#   $env:RAG_DENSE_PROVIDER="fastembed"; $env:RAG_DENSE_MODEL="BAAI/bge-large-en-v1.5"; $env:RAG_DENSE_DIM="1024"
#   python scripts/ingest_kb.py knowledge-base/sreenidhi_knowledge_base.md --collection srinidhi-kb-bge

# Re-ingest a fresh copy with the CURRENT OpenAI embeddings into a new collection:
#   python scripts/ingest_kb.py knowledge-base/sreenidhi_knowledge_base.md --collection srinidhi-kb-copy

After ingesting, point the app at the new collection (AgentBackend/.env):
  RAG_QDRANT_COLLECTION=srinidhi-kb-bge   (+ the matching RAG_DENSE_PROVIDER/MODEL/DIM)
and restart. Roll back by reverting those env values — the old collection is untouched.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `agent_backend` importable regardless of the current working directory
# (this file lives in AgentBackend/scripts/; the package root is its parent's parent).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _drop_collection(collection: str) -> None:
    from qdrant_client import QdrantClient

    from agent_backend.rag import settings as rag_settings

    client = QdrantClient(
        url=rag_settings.QDRANT_URL, api_key=rag_settings.QDRANT_API_KEY or None, timeout=60
    )
    if client.collection_exists(collection):
        client.delete_collection(collection)
        print(f"[ingest-kb] dropped existing collection '{collection}' (--recreate)")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Ingest a KB document into a Qdrant collection (parse/chunk/embed/upsert).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("path", help="Path to the KB file (.md / .markdown / .txt / .pdf).")
    ap.add_argument(
        "--collection",
        required=True,
        help="Target Qdrant collection. Use a NEW name (e.g. srinidhi-kb-bge) to avoid "
        "touching the live one; it is auto-created at the configured RAG_DENSE_DIM.",
    )
    ap.add_argument("--source", default=None, help="source payload tag (default: RAG_INGEST_SOURCE).")
    ap.add_argument("--tenant", default=None, help="tenant_id payload (default: RAG_INGEST_TENANT).")
    ap.add_argument(
        "--recreate",
        action="store_true",
        help="Delete the target collection first if it exists, then ingest fresh at the "
        "configured dim (use when re-ingesting an existing collection at a new model/dim).",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Allow targeting the collection the LIVE app currently queries "
        "(RAG_QDRANT_COLLECTION). Refused by default to protect current functionality.",
    )
    args = ap.parse_args()

    from agent_backend.rag import settings as rag_settings
    from agent_backend.rag.ingestion.pipeline import ingest_file

    p = Path(args.path)
    if not p.exists():
        print(f"ERROR: file not found: {p}", file=sys.stderr)
        return 2

    # Guard: don't clobber the live collection unless explicitly forced.
    if args.collection == rag_settings.QDRANT_COLLECTION and not args.force:
        print(
            f"ERROR: --collection '{args.collection}' is the collection the live app queries "
            f"(RAG_QDRANT_COLLECTION). Use a new name (recommended) or pass --force to override.",
            file=sys.stderr,
        )
        return 2

    print(
        "[ingest-kb] starting | "
        f"provider={rag_settings.DENSE_PROVIDER} model={rag_settings.DENSE_MODEL} "
        f"dim={rag_settings.DENSE_DIM} | collection={args.collection} | qdrant={rag_settings.QDRANT_URL}"
    )
    print(f"[ingest-kb] file={p}")

    if args.recreate:
        _drop_collection(args.collection)

    try:
        res = ingest_file(
            str(p),
            collection=args.collection,
            source=args.source,
            tenant_id=args.tenant,
        )
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: ingestion failed: {e}", file=sys.stderr)
        return 1

    print(
        "[ingest-kb] DONE | "
        f"doc_id={res.doc_id} chunks={res.chunks} parser={res.parser} "
        f"collection={res.collection} status={res.status}"
    )
    print(
        "[ingest-kb] next: set RAG_QDRANT_COLLECTION="
        f"{args.collection} (+ matching RAG_DENSE_PROVIDER/MODEL/DIM) in .env and restart. "
        "Roll back by reverting those env values; the old collection is untouched."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
