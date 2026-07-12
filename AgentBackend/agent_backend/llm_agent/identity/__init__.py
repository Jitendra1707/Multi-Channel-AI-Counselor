"""Agent identity / persona.

The active persona's JSON lives at `<this dir>/<identity_name>.json`,
where `identity_name` comes from `IDENTITY_NAME` in .env. On startup
the FastAPI lifespan calls `ensure_identity_json(...)` to hydrate that
file from Azure Blob Storage (mirroring Node's avatar-fetcher.ts and
LLmLayer's identity loader). After hydration, `get_identity()` reads
the JSON through an lru_cache.

Adding a new persona:
  1. Upload <my-persona>.json to the Azure Blob folder.
  2. Set IDENTITY_NAME=my-persona in .env.
  3. Restart Python.

For local dev you can also drop a JSON file directly into this
folder and skip Azure entirely — the fetcher's branch 1 handles that
case (see fetcher.py docstring).
"""

from agent_backend.llm_agent.identity.fetcher import ensure_identity_json
from agent_backend.llm_agent.identity.loader import (
    gender_reminder,
    get_identity,
    list_identities,
    render_identity_block,
)

__all__ = [
    "ensure_identity_json",
    "gender_reminder",
    "get_identity",
    "list_identities",
    "render_identity_block",
]
