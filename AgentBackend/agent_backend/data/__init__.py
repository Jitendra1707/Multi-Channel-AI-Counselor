"""Data access — leads.

JSON-file backed today (see `test-data/leads.json`). Same public API will swap
to Postgres later without channel-layer changes:

    from agent_backend.data import LeadRepo, Lead, LeadStatus

University knowledge is NOT here — it lives in the RAG knowledge base
(`agent_backend.rag`, sourced from `knowledge-base/`), which is the single
source of truth for university facts.
"""
from agent_backend.data.leads import Lead, LeadRepo, LeadStatus

__all__ = [
    "Lead",
    "LeadRepo",
    "LeadStatus",
]
