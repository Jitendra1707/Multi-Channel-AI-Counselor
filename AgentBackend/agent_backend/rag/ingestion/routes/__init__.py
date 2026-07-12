"""HTTP surface for RAG ingestion. The router is defined in `ingest.py` and
mounted in `agent_backend.main` (the blueprint wiring)."""
from agent_backend.rag.ingestion.routes.ingest import router

__all__ = ["router"]
