"""Per-conversation message history.

Keyed by `session.conversation_id` (one row per active call / chat thread).
The brain reads `recent_messages(n)` on every turn and prepends them to the
LangGraph input — so the model sees what it just said and the candidate's
prior utterances, instead of treating every turn as first contact.

Today: in-RAM, process-local, bounded ring buffer per session.
Tomorrow: write-through to Postgres / mem0 keyed on lead_id so the
candidate's history survives across calls. The public API stays the same.
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage


Role = Literal["user", "bot"]


class ConversationStore:
    """Bounded message-history buffer for one active conversation."""

    def __init__(self, *, max_turns: int = 40) -> None:
        # Each entry is already a LangChain message — no conversion on read.
        self._buf: deque[BaseMessage] = deque(maxlen=max_turns)
        self._lock = threading.Lock()

    def append_user(self, text: str) -> None:
        if not text.strip():
            return
        with self._lock:
            self._buf.append(HumanMessage(content=text))

    def append_bot(self, text: str) -> None:
        if not text.strip():
            return
        with self._lock:
            self._buf.append(AIMessage(content=text))

    def recent(self, n: int = 20) -> list[BaseMessage]:
        """The last `n` messages, oldest-first (LangGraph wants chrono order)."""
        with self._lock:
            return list(self._buf)[-n:]

    def turn_count(self) -> int:
        """Number of turns in the buffer (user + bot combined)."""
        with self._lock:
            return len(self._buf)


# ---------------------------------------------------------------------------
# Per-conversation registry — singleton-per-conversation_id.
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, ConversationStore] = {}
_REGISTRY_LOCK = threading.Lock()


def get_conversation(conversation_id: str) -> ConversationStore:
    """Get-or-create the conversation memory for one session."""
    with _REGISTRY_LOCK:
        store = _REGISTRY.get(conversation_id)
        if store is None:
            store = ConversationStore()
            _REGISTRY[conversation_id] = store
        return store


def clear_conversation(conversation_id: str) -> None:
    """Drop the buffer when a call ends — frees the entry."""
    with _REGISTRY_LOCK:
        _REGISTRY.pop(conversation_id, None)
