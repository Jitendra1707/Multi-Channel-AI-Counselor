"""CLI to exercise the Qdrant hybrid retriever (query/verify only).

The collection is created in the Qdrant UI (or by the standalone ingestion
script's --create-collection); documents are ingested by that script. This CLI
only QUERIES, so you can sanity-check retrieval from the terminal.

    python -m agent_backend.rag qdrant-query "fees for CSE AI&ML" --tenant sreenidhi
    python -m agent_backend.rag qdrant-query "..." -k 6 --no-hybrid     # dense-only A/B
    python -m agent_backend.rag qdrant-query "..." --rerank             # + LLM rerank
    python -m agent_backend.rag qdrant-query "..." --topic fees         # metadata filter
"""
from __future__ import annotations

import argparse
import sys

from agent_backend.rag import settings as rag_settings


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _cmd_qdrant_query(a: argparse.Namespace) -> int:
    from agent_backend.rag.qdrant_store import QdrantStore

    store = QdrantStore.connect(collection=a.collection, tenant_id=a.tenant, source=a.source)
    where = {"topic": a.topic} if a.topic else None
    hits = store.search(a.text, k=a.k, hybrid=not a.no_hybrid, rerank=a.rerank, where=where)
    mode = "dense-only" if a.no_hybrid else "hybrid(dense+bm25+rrf)"
    if a.rerank:
        mode += "+rerank"
    print(f"Query: {a.text!r}   tenant={a.tenant}   mode={mode}   (top {len(hits)} of {len(store)})\n")
    for rank, h in enumerate(hits, 1):
        flags = ",".join(k for k in ("fee_sensitive", "date_sensitive") if h.meta.get(k))
        print(f"#{rank}  score={h.score:.4f}  topic={h.meta.get('topic')}"
              f"{'  ['+flags+']' if flags else ''}")
        print(f"     [{h.heading}]")
        body = h.text if len(h.text) <= 500 else h.text[:500] + " …"
        print("     " + body.replace("\n", "\n     "))
        print("-" * 88)
    return 0


def main() -> int:
    _utf8_stdout()
    p = argparse.ArgumentParser(prog="python -m agent_backend.rag")
    sub = p.add_subparsers(dest="cmd", required=True)

    qp = sub.add_parser("qdrant-query", help="query the Qdrant hybrid backend")
    qp.add_argument("text", help="the query")
    qp.add_argument("--collection", default=None,
                    help="collection to query (default: RAG_QDRANT_COLLECTION; pass the "
                         "general-KB collection to test it directly)")
    qp.add_argument("--tenant", default=rag_settings.DEFAULT_TENANT, help="tenant_id to search")
    qp.add_argument("--source", default=rag_settings.DEFAULT_SOURCE, help="source bucket")
    qp.add_argument("-k", type=int, default=rag_settings.TOP_K)
    qp.add_argument("--no-hybrid", action="store_true", help="dense-only (disable sparse fusion)")
    qp.add_argument("--rerank", action="store_true", help="LLM rerank the fused shortlist")
    qp.add_argument("--topic", default=None, help="filter by metadata topic (e.g. fees)")
    qp.set_defaults(fn=_cmd_qdrant_query)

    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
