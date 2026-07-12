"""RAG ingestion HTTP routes — the WRITE surface for the knowledge base.

Mounted in `agent_backend.main`. Matches the frontend Resources contract
(web-app/src/lib/api.ts): POST/GET/DELETE /api/resources.

  POST   /api/resources        multipart `file` → parse+chunk+embed+upsert → ResourceDoc
  GET    /api/resources        list ingested docs (from Qdrant payloads)
  DELETE /api/resources/{id}   remove a doc's points by doc_id

Ingestion is blocking (parse + embeddings + Qdrant), so it runs in a worker
thread to keep the event loop responsive.
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from agent_backend.rag.ingestion import settings as ing_settings

try:
    from agent_backend.infra import get_logger

    log = get_logger(__name__)
except Exception:  # noqa: BLE001
    import logging

    log = logging.getLogger("agent_backend.rag.ingestion.routes")

router = APIRouter(prefix="/api/resources", tags=["resources"])


def _ext_ok(filename: str) -> bool:
    return (filename.rsplit(".", 1)[-1].lower() if "." in filename else "") in ing_settings.ACCEPTED_EXTS


@router.post("")
async def upload_resource(file: UploadFile = File(...)) -> dict:
    """Upload one document → ingest into the Qdrant KB → return a ResourceDoc."""
    filename = file.filename or "upload"
    if not _ext_ok(filename):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported type. Allowed: {sorted(ing_settings.ACCEPTED_EXTS)}",
        )

    data = await file.read()
    max_bytes = ing_settings.MAX_UPLOAD_MB * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds {ing_settings.MAX_UPLOAD_MB} MB.")

    # Spool to disk ONLY so the parser has a file path to read. The file is
    # transient — once ingest_file() has parsed+embedded it into Qdrant, the
    # original is no longer needed (the knowledge lives in the vector store), so
    # we DELETE it in `finally` whether ingestion succeeds or fails. This keeps
    # the uploads dir from growing without bound. Re-ingest = re-upload.
    upload_dir = Path(ing_settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe = f"{uuid.uuid4().hex[:8]}_{Path(filename).name}"
    dest = upload_dir / safe
    dest.write_bytes(data)

    try:
        # Blocking pipeline → worker thread.
        from agent_backend.rag.ingestion.pipeline import ingest_file

        result = await asyncio.to_thread(ingest_file, str(dest), filename=filename)
    except Exception as e:  # noqa: BLE001
        log.warning("[ingest] failed", filename=filename, err=str(e)[:200])
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {e}") from e
    finally:
        # Always remove the spooled file — disk never accumulates uploads.
        try:
            dest.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            log.debug("[ingest] temp cleanup failed", file=str(dest), err=str(e)[:120])

    return {
        "id": result.doc_id,
        "filename": result.filename,
        "size": len(data),
        "status": result.status,         # "ready"
        "chunks": result.chunks,
    }


@router.get("")
async def list_resources() -> list[dict]:
    """List ingested documents — distinct doc_id/source_doc from Qdrant payloads."""
    try:
        docs = await asyncio.to_thread(_scan_docs)
    except Exception as e:  # noqa: BLE001
        log.warning("[ingest] list failed", err=str(e)[:160])
        return []
    log.info(
        "[ingest] GET /api/resources → returning documents",
        collection=ing_settings.INGEST_COLLECTION,
        documents=len(docs),
        files=[d.get("filename") for d in docs],
    )
    return docs


@router.delete("/{doc_id}")
async def delete_resource(doc_id: str) -> dict:
    try:
        removed = await asyncio.to_thread(_delete_doc, doc_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Delete failed: {e}") from e
    return {"deleted": doc_id, "ok": removed}


# --- helpers (sync; run in a thread) --------------------------------------
def _scan_docs() -> list[dict]:
    from collections import defaultdict

    from qdrant_client import QdrantClient

    client = QdrantClient(url=ing_settings.QDRANT_URL, api_key=ing_settings.QDRANT_API_KEY or None, timeout=30)
    coll = ing_settings.INGEST_COLLECTION
    if not client.collection_exists(coll):
        log.info("[ingest] scan — collection does not exist", collection=coll, documents=0)
        return []
    counts: dict[str, dict] = defaultdict(lambda: {"chunks": 0})
    total_points = 0
    no_docid = 0
    next_page = None
    while True:
        points, next_page = client.scroll(
            collection_name=coll, with_payload=True, limit=256, offset=next_page
        )
        for p in points:
            total_points += 1
            pl = p.payload or {}
            did = pl.get("doc_id")
            if not did:
                no_docid += 1
                continue
            entry = counts[did]
            entry["chunks"] += 1
            entry.setdefault("id", did)
            entry.setdefault("filename", pl.get("source_doc", "document"))
            entry.setdefault("status", "ready")
        if next_page is None:
            break
    docs = list(counts.values())
    log.info(
        "[ingest] scan complete",
        collection=coll,
        points_scanned=total_points,
        points_without_doc_id=no_docid,
        documents=len(docs),
        files=[d.get("filename") for d in docs],
    )
    return docs


def _delete_doc(doc_id: str) -> bool:
    from qdrant_client import QdrantClient, models

    client = QdrantClient(url=ing_settings.QDRANT_URL, api_key=ing_settings.QDRANT_API_KEY or None, timeout=30)
    coll = ing_settings.INGEST_COLLECTION
    if not client.collection_exists(coll):
        return False
    client.delete(
        collection_name=coll,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
            )
        ),
        wait=True,
    )
    return True
