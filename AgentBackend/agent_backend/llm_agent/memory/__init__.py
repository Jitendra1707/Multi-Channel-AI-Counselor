"""Memory layer — tiered, kind-agnostic.

Three tiers (Phase 1.6 architecture):

  - **Working memory** (per visual source, latest wins): lives in
    `llm_agent.vision.store.WorkingMemory`. Goes directly into the
    system prompt every turn. Bounded by definition (one record per
    source).

  - **Episodic store** (this package, `episodic.py`): append-only
    log of everything observed in the conversation — visual frames
    AND speech turns — indexed by time + kind + source. NOT in the
    system prompt directly. Phase 1.6c adds a BM25 retriever that
    pulls relevant records into a RECALL slot on demand.

  - **Episodic summary** (Phase 1.6b, `summary.py`): a rolling
    LLM-condensed summary of older records, occupies one bounded
    prompt slot. Bridges the gap between the most-recent working
    snapshot and the "needs explicit recall" deep history.

The store is kind-agnostic so both `kind="visual"` (captioner output)
and `kind="conversation"` (user STT transcripts + bot replies) live in
one timeline. When the retriever lands, the same BM25 index covers
both — a question like "what did Amina say about the budget slide?"
hits visual AND conversation records seamlessly.
"""

from agent_backend.llm_agent.memory.episodic import (
    EpisodicRecord,
    EpisodicStore,
    Kind,
    clear_episodic_store,
    get_episodic_store,
)

__all__ = [
    "EpisodicRecord",
    "EpisodicStore",
    "Kind",
    "get_episodic_store",
    "clear_episodic_store",
]
