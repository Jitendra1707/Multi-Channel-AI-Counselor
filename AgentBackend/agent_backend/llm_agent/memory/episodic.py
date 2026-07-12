"""Episodic store — append-only timeline of everything the bot observed.

Holds BOTH visual frame transcripts and conversation turns in one
chronological list, distinguished by the `kind` field. This unification
is deliberate: a question like "what did Amina say when slide 4 was
up?" needs to correlate across both kinds, and a single store with
one timestamp index makes that join trivial.

Phase 1.6a scope:
  - Append + bounded retention (deque maxlen).
  - Read APIs: all / since-timestamp / by-kind.
  - NOT YET: BM25 index (Phase 1.6c), rolling summary (Phase 1.6b).
    The append API is final; later phases just add indexes ON TOP.

The store is per-conversation, registry-managed by `conversation_id`,
mirroring how `WorkingMemory` is keyed. Phase 6 (multi-process Pipecat
scaling) will subprocess-isolate naturally because each meeting gets
its own process and therefore its own store registry.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal


Kind = Literal["visual", "conversation"]


@dataclass(frozen=True)
class EpisodicRecord:
    """One observation in the timeline.

    Attributes:
        kind: 'visual' (captioner output) or 'conversation' (a speech
            turn). Used by retrievers to filter and by the formatter
            to render appropriate prefixes.
        source: Disambiguator within a kind. For visual: 'page' /
            'screenshare' / 'cameras'. For conversation: 'user' /
            'bot' / 'system'. Free-form so future kinds can extend.
        content: The actual text. For visual, the captioner's
            structured transcript. For conversation, the spoken
            utterance.
        ts: Wall-clock `time.time()` — used for recency ordering and
            "since-timestamp" queries.
        metadata: Free-form key/value bag. Visual records carry
            `{"phash": ...}`. Conversation records may carry
            `{"display_name": ..., "channel": ...}`. Keep it small —
            anything large belongs in `content`.
    """

    kind: Kind
    source: str
    content: str
    ts: float
    metadata: dict[str, Any] = field(default_factory=dict)


class EpisodicStore:
    """Append-only ring buffer over EpisodicRecord. Thread-safe.

    Capacity is bounded by `max_records`; oldest drops out when full.
    A 60-minute meeting at ~1 deduped visual frame/sec per source +
    ~10 conversation turns/min fits in ~3000-4000 records, so 2000
    is the floor for usable sessions; tune via config.
    """

    def __init__(self, *, max_records: int) -> None:
        self._records: deque[EpisodicRecord] = deque(maxlen=max_records)
        self._lock = threading.Lock()

    def append(self, record: EpisodicRecord) -> None:
        with self._lock:
            self._records.append(record)

    def all(self) -> list[EpisodicRecord]:
        """Full timeline, oldest-first."""
        with self._lock:
            return list(self._records)

    def recent_since(self, ts: float) -> list[EpisodicRecord]:
        """All records with timestamp ≥ `ts`, oldest-first."""
        with self._lock:
            return [r for r in self._records if r.ts >= ts]

    def by_kind(self, kind: Kind) -> list[EpisodicRecord]:
        """All records of one kind, oldest-first."""
        with self._lock:
            return [r for r in self._records if r.kind == kind]

    def size(self) -> int:
        with self._lock:
            return len(self._records)


# ---------------------------------------------------------------------------
# Per-conversation registry
# ---------------------------------------------------------------------------
# Mirrors `vision.store.get_working_memory`'s shape: one store per
# conversation_id, lazy-created, lifetime = process lifetime. The
# registry is the seam Phase 6's multi-process scaling will hook —
# spawning a subprocess per meeting gives each its own registry copy
# automatically without touching call sites.

_EPISODIC: dict[str, EpisodicStore] = {}
_EPISODIC_LOCK = threading.Lock()


def get_episodic_store(conversation_id: str) -> EpisodicStore:
    """Get-or-create the episodic store for one conversation."""
    with _EPISODIC_LOCK:
        store = _EPISODIC.get(conversation_id)
        if store is None:
            # Import inside the function so the registry doesn't pull
            # config at module-import time (config reads .env, which
            # tests like to keep deferred).
            from agent_backend.config import get_settings

            max_records = get_settings().episodic_max_records
            store = EpisodicStore(max_records=max_records)
            _EPISODIC[conversation_id] = store
        return store


def clear_episodic_store(conversation_id: str) -> None:
    """Drop the store for one conversation (called on session teardown)."""
    with _EPISODIC_LOCK:
        _EPISODIC.pop(conversation_id, None)


def make_visual_record(
    *, source: str, content: str, phash: str, ts: float | None = None
) -> EpisodicRecord:
    """Helper: build a kind='visual' record. Mirrors the shape that
    `vision.store.make_record` returns so call sites stay tidy."""
    return EpisodicRecord(
        kind="visual",
        source=source,
        content=content,
        ts=ts if ts is not None else time.time(),
        metadata={"phash": phash} if phash else {},
    )


def make_conversation_record(
    *,
    source: Literal["user", "bot", "system"],
    content: str,
    display_name: str | None = None,
    channel: str | None = None,
    ts: float | None = None,
) -> EpisodicRecord:
    """Helper: build a kind='conversation' record. Used by AgentBridge
    when a STT transcript arrives and when a bot response completes."""
    metadata: dict[str, Any] = {}
    if display_name:
        metadata["display_name"] = display_name
    if channel:
        metadata["channel"] = channel
    return EpisodicRecord(
        kind="conversation",
        source=source,
        content=content,
        ts=ts if ts is not None else time.time(),
        metadata=metadata,
    )
