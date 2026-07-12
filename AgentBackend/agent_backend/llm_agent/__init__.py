"""Channel-agnostic agent layer.

Exposes one stable contract every channel calls:

    async def run_stream(
        text: str,
        *,
        channel: str,
        session: Session,
    ) -> AsyncIterator[str]: ...

Phase 1 implementation is a thin OpenAI streaming wrapper. The
function signature is intentionally narrow so future phases can swap
the body for a full LangGraph brain (Phase 5) without touching any
channel adapter.

Sub-packages (`tools/`, `memory/`, `prompts/`) are placeholders for
later phases — empty by design today.
"""

from agent_backend.llm_agent.agent import open_call, run_stream
from agent_backend.llm_agent.session import Session

__all__ = ["open_call", "run_stream", "Session"]
