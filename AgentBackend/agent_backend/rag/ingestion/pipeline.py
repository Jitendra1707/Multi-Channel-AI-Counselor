"""Ingestion pipeline: parse → chunk → embed (dense + sparse) → upsert to Qdrant.

Produces points matching the QUERY-side contract exactly — both sides embed with
the SAME local models via the shared `embedder`/`sparse` modules, so document and
query vectors live in one space:
  - named vector `dense`  : LOCAL FastEmbed (ONNX), RAG_DENSE_MODEL
                            (default BAAI/bge-large-en-v1.5, 1024-d, L2-normalized)
  - named vector `sparse` : FastEmbed Qdrant/bm25 (IDF), passage_embed side
  - payload               : heading, text, topic, source, tenant_id, fee/date
                            flags, source_doc, version, page-ish section
The target collection (default `ramesh-kb`) is created with this contract (dense
size = DENSE_DIM) if it doesn't exist; if it exists, its dense size is verified
against DENSE_DIM (see `_verify_dense_dim`) so an old OpenAI-3072 collection
fails loudly instead of corrupting on upsert.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_backend.rag import settings as rag_settings
from agent_backend.rag.embedder import embed_texts
from agent_backend.rag.ingestion import settings as ing_settings
from agent_backend.rag.ingestion.chunker import chunk_document
from agent_backend.rag.ingestion.parsers import get_parser
from agent_backend.rag.ingestion.sparse import embed_passages

try:
    from agent_backend.infra import get_logger

    log = get_logger(__name__)
except Exception:  # noqa: BLE001
    import logging

    log = logging.getLogger("agent_backend.rag.ingestion")


@dataclass
class IngestResult:
    doc_id: str
    filename: str
    collection: str
    chunks: int
    parser: str
    status: str = "ready"
    # Qdrant point ids created by this ingest — needed so a captured fact can be
    # superseded/unlearned later by exact id. Empty for the legacy file path
    # unless the caller asks for it.
    point_ids: list[str] = field(default_factory=list)


def _qdrant():
    from qdrant_client import QdrantClient, models

    client = QdrantClient(
        url=ing_settings.QDRANT_URL, api_key=ing_settings.QDRANT_API_KEY or None, timeout=60
    )
    return client, models


def _ensure_collection(client: Any, models: Any, collection: str) -> None:
    """Create the collection with the dense+sparse named-vector contract if it's
    not already there. If it EXISTS, verify its `dense` vector size equals the
    local FastEmbed model's DENSE_DIM and fail loudly on a mismatch — this is the
    guard for an OLD collection built with OpenAI 3072-d vectors: upserting the
    new 1024-d local-FastEmbed points into it would otherwise raise a cryptic
    Qdrant error. The fix is to recreate the collection at DENSE_DIM and
    re-ingest every document (vectors from different models aren't comparable)."""
    if client.collection_exists(collection):
        _verify_dense_dim(client, collection)
        return
    client.create_collection(
        collection_name=collection,
        vectors_config={
            rag_settings.QDRANT_DENSE_VECTOR: models.VectorParams(
                size=ing_settings.DENSE_DIM, distance=models.Distance.COSINE
            )
        },
        sparse_vectors_config={
            rag_settings.QDRANT_SPARSE_VECTOR: models.SparseVectorParams(
                modifier=models.Modifier.IDF
            )
        },
    )
    log.info("[ingest] created collection", collection=collection, dim=ing_settings.DENSE_DIM)


def _verify_dense_dim(client: Any, collection: str) -> None:
    """Raise if the existing collection's `dense` vector size != DENSE_DIM.

    Best-effort on the read: if the dense size can't be determined from the
    collection info (SDK shape drift), we skip the check rather than block a
    legitimate ingest — Qdrant will still reject a true dimension mismatch on
    upsert, just less clearly."""
    try:
        info = client.get_collection(collection)
        vectors = info.config.params.vectors  # dict[name -> VectorParams] for named vectors
        dense_params = vectors.get(rag_settings.QDRANT_DENSE_VECTOR) if isinstance(vectors, dict) else None
        size = getattr(dense_params, "size", None)
    except Exception as e:  # noqa: BLE001
        log.debug("[ingest] could not read collection dense dim — skipping guard", err=str(e)[:160])
        return
    if size is not None and int(size) != int(ing_settings.DENSE_DIM):
        raise ValueError(
            f"Collection '{collection}' has dense vector size {size}, but the local "
            f"FastEmbed model '{ing_settings.DENSE_MODEL}' produces {ing_settings.DENSE_DIM}-d "
            f"vectors. This collection was likely built with the old OpenAI embeddings. "
            f"Recreate it at size={ing_settings.DENSE_DIM} (Cosine dense + IDF sparse) and "
            f"re-ingest all documents — embeddings from different models are not comparable."
        )


def ingest_file(
    path: str,
    *,
    filename: str | None = None,
    collection: str | None = None,
    tenant_id: str | None = None,
    source: str | None = None,
) -> IngestResult:
    """Parse, chunk, embed, and upsert one document. Returns a summary."""
    p = Path(path)
    filename = filename or p.name
    ext = p.suffix.lstrip(".").lower()
    if ext not in ing_settings.ACCEPTED_EXTS:
        raise ValueError(f"Unsupported file type '.{ext}'. Allowed: {sorted(ing_settings.ACCEPTED_EXTS)}")

    collection = collection or ing_settings.INGEST_COLLECTION
    tenant_id = tenant_id if tenant_id is not None else ing_settings.INGEST_TENANT
    source = source or ing_settings.INGEST_SOURCE
    doc_id = uuid.uuid4().hex
    version = datetime.now(timezone.utc).date().isoformat()

    # 1. PARSE → markdown/text.
    parser = get_parser()
    md = parser.to_markdown(str(p), ext)
    if not md.strip():
        raise ValueError(f"No extractable text in '{filename}'.")

    # 2. CHUNK.
    chunks = chunk_document(
        md,
        target_tokens=ing_settings.CHUNK_TARGET_TOKENS,
        overlap_tokens=ing_settings.CHUNK_OVERLAP_TOKENS,
        hard_max_tokens=ing_settings.CHUNK_HARD_MAX_TOKENS,
        source_doc=filename,
        source=source,
        tenant_id=tenant_id,
        version=version,
    )
    if not chunks:
        raise ValueError(f"'{filename}' produced no chunks.")

    # 3. EMBED — dense (LOCAL FastEmbed ONNX, the SAME embed_texts + model +
    #    L2-normalization the query side uses) + sparse (FastEmbed bm25 passages).
    dense = embed_texts([c.for_embedding() for c in chunks], model=rag_settings.DENSE_MODEL)
    sparse = embed_passages([c.for_embedding() for c in chunks])

    # 4. UPSERT.
    client, models = _qdrant()
    _ensure_collection(client, models, collection)
    points = []
    for i, c in enumerate(chunks):
        sp_idx, sp_val = sparse[i]
        payload = dict(c.meta)
        payload["doc_id"] = doc_id
        points.append(
            models.PointStruct(
                id=uuid.uuid4().hex,
                vector={
                    rag_settings.QDRANT_DENSE_VECTOR: dense[i].tolist(),
                    rag_settings.QDRANT_SPARSE_VECTOR: models.SparseVector(
                        indices=sp_idx, values=sp_val
                    ),
                },
                payload=payload,
            )
        )
    client.upsert(collection_name=collection, points=points, wait=True)

    log.info(
        "[ingest] upserted",
        filename=filename, collection=collection, chunks=len(points),
        parser=parser.name, source=source, doc_id=doc_id[:8],
    )
    return IngestResult(
        doc_id=doc_id, filename=filename, collection=collection,
        chunks=len(points), parser=parser.name,
    )


def ingest_text(
    text: str,
    *,
    heading: str = "",
    collection: str | None = None,
    tenant_id: str | None = None,
    source: str | None = None,
    topic: str | None = None,
    version: str | None = None,
    source_doc: str | None = None,
) -> IngestResult:
    """Ingest a single free-form snippet (NO file/parser) — used by the
    video-call knowledge-capture flow.

    Mirrors `ingest_file`'s embed+upsert exactly (same models + payload contract,
    so captured facts are retrieved identically to uploaded docs), but feeds the
    text straight into the chunker as one markdown section instead of parsing a
    file. `topic`, when given, overrides the chunker's keyword guess (the
    extractor already classified it). Returns the result WITH `point_ids` so the
    caller can supersede/unlearn the exact points later.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("ingest_text: empty text")

    collection = collection or ing_settings.INGEST_COLLECTION
    tenant_id = tenant_id if tenant_id is not None else ing_settings.INGEST_TENANT
    source = source or "video-transcript"
    source_doc = source_doc or (heading or "video-capture")
    doc_id = uuid.uuid4().hex
    version = version or datetime.now(timezone.utc).date().isoformat()

    # One markdown section so the existing chunker carries the heading breadcrumb
    # + classifies topic/sensitivity exactly like the file path.
    md = f"# {heading}\n\n{text}" if heading else text
    chunks = chunk_document(
        md,
        target_tokens=ing_settings.CHUNK_TARGET_TOKENS,
        overlap_tokens=ing_settings.CHUNK_OVERLAP_TOKENS,
        hard_max_tokens=ing_settings.CHUNK_HARD_MAX_TOKENS,
        source_doc=source_doc,
        source=source,
        tenant_id=tenant_id,
        version=version,
    )
    if not chunks:
        raise ValueError("ingest_text: produced no chunks")

    dense = embed_texts([c.for_embedding() for c in chunks], model=rag_settings.DENSE_MODEL)
    sparse = embed_passages([c.for_embedding() for c in chunks])

    client, models = _qdrant()
    _ensure_collection(client, models, collection)
    points = []
    point_ids: list[str] = []
    for i, c in enumerate(chunks):
        sp_idx, sp_val = sparse[i]
        payload = dict(c.meta)
        payload["doc_id"] = doc_id
        if topic:
            payload["topic"] = topic
        pid = uuid.uuid4().hex
        point_ids.append(pid)
        points.append(
            models.PointStruct(
                id=pid,
                vector={
                    rag_settings.QDRANT_DENSE_VECTOR: dense[i].tolist(),
                    rag_settings.QDRANT_SPARSE_VECTOR: models.SparseVector(
                        indices=sp_idx, values=sp_val
                    ),
                },
                payload=payload,
            )
        )
    client.upsert(collection_name=collection, points=points, wait=True)
    log.info(
        "[ingest] upserted text snippet",
        collection=collection, chunks=len(points), source=source, doc_id=doc_id[:8],
    )
    return IngestResult(
        doc_id=doc_id, filename=source_doc, collection=collection,
        chunks=len(points), parser="text", point_ids=point_ids,
    )


def get_points(point_ids: list[str], *, collection: str | None = None) -> list[dict[str, Any]]:
    """Fetch points (payload only) by exact id — the read side of a surgical
    chunk patch. Returns [{"id", "payload"}] for the ids that exist."""
    if not point_ids:
        return []
    collection = collection or ing_settings.INGEST_COLLECTION
    client, _models = _qdrant()
    records = client.retrieve(
        collection_name=collection, ids=list(point_ids), with_payload=True, with_vectors=False
    )
    return [{"id": str(r.id), "payload": dict(r.payload or {})} for r in records]


def update_point_text(
    point_id: str,
    new_text: str,
    *,
    collection: str | None = None,
    payload_updates: dict[str, Any] | None = None,
) -> None:
    """Surgically replace ONE point's text in place: re-embed (dense + sparse,
    same models as ingest so ranking is unchanged) and upsert under the SAME id,
    keeping the rest of the payload (heading, topic, sensitivity flags, doc_id)
    so the chunk stays itself — just corrected. Used by knowledge-capture
    supersede to fix a stale value WITHOUT nuking the chunk's other facts.
    Raises on a hard embed/Qdrant error so the caller can record it."""
    new_text = (new_text or "").strip()
    if not new_text:
        raise ValueError("update_point_text: empty text")
    collection = collection or ing_settings.INGEST_COLLECTION
    existing = get_points([point_id], collection=collection)
    if not existing:
        raise ValueError(f"update_point_text: point {point_id!r} not found")
    payload = existing[0]["payload"]
    payload["text"] = new_text
    payload["version"] = datetime.now(timezone.utc).date().isoformat()
    payload.update(payload_updates or {})

    # Mirror Chunk.for_embedding() (heading + text) so the patched chunk ranks
    # exactly like its unpatched siblings.
    heading = str(payload.get("heading") or "")
    embed_input = f"{heading}\n{new_text}".strip() if heading else new_text
    dense = embed_texts([embed_input], model=rag_settings.DENSE_MODEL)
    sp_idx, sp_val = embed_passages([embed_input])[0]

    client, models = _qdrant()
    client.upsert(
        collection_name=collection,
        points=[
            models.PointStruct(
                id=point_id,
                vector={
                    rag_settings.QDRANT_DENSE_VECTOR: dense[0].tolist(),
                    rag_settings.QDRANT_SPARSE_VECTOR: models.SparseVector(
                        indices=sp_idx, values=sp_val
                    ),
                },
                payload=payload,
            )
        ],
        wait=True,
    )
    log.info("[ingest] patched point in place", collection=collection, point_id=point_id[:12])


def delete_points(point_ids: list[str], *, collection: str | None = None) -> int:
    """Delete exact Qdrant points by id (used to supersede/unlearn a captured
    fact on approval). Returns the count requested. Best-effort: raises only on a
    hard client error so the caller can record an ingest_error."""
    if not point_ids:
        return 0
    collection = collection or ing_settings.INGEST_COLLECTION
    client, models = _qdrant()
    client.delete(
        collection_name=collection,
        points_selector=models.PointIdsList(points=list(point_ids)),
        wait=True,
    )
    log.info("[ingest] deleted points", collection=collection, n=len(point_ids))
    return len(point_ids)
