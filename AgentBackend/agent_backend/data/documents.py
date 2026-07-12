"""Document catalog — the "resource manager" for sendable materials.

One source of truth (a JSON file, `documents_file`) mapping a stable
`doc_key` → {title, url, description, keywords, template}. Both send paths use
it so a given requirement always resolves to the same document:

  - Scenario 1 (post-call): the BusinessLayer analyzer says the candidate asked
    for, e.g., "fee and scholarship details"; the WhatsApp send endpoint matches
    that free text to a `doc_key` and sends the catalog's URL (free-form if the
    24h window is open, else the doc's approved template).
  - Scenario 2 (live WhatsApp): the brain calls the `send_document` tool with an
    explicit `doc_key` chosen from the catalog and we send it instantly.

URL-based by design (the easier path the user picked): the catalog stores a
PUBLIC HTTPS `url` (Blob / SharePoint) and we hand that link to Plivo
(`send_media`) / the template's document header — no file upload needed.

Hot-reloadable + tolerant: a missing/!malformed file yields an empty catalog and
a logged warning rather than a crash (sends then degrade to a logged failure).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from agent_backend.config import get_settings
from agent_backend.infra import get_logger

log = get_logger(__name__)


class _DocumentRepo:
    _instance: "_DocumentRepo | None" = None
    _instance_lock = threading.Lock()

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._docs: dict[str, dict[str, Any]] = {}
        self._mtime: float | None = None

    @classmethod
    def instance(cls) -> "_DocumentRepo":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(Path(get_settings().documents_file).resolve())
            cls._instance._reload_if_changed()
            return cls._instance

    def _reload_if_changed(self) -> None:
        with self._lock:
            try:
                mtime = self._path.stat().st_mtime
            except FileNotFoundError:
                if self._docs:
                    self._docs = {}
                    log.warning("[documents] catalog file gone — catalog now empty", path=str(self._path))
                return
            if self._mtime == mtime:
                return
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                log.warning("[documents] catalog parse failed — keeping previous", path=str(self._path), err=str(e))
                return
            if not isinstance(raw, dict):
                log.warning("[documents] catalog is not a JSON object — ignoring", path=str(self._path))
                return
            self._docs = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
            self._mtime = mtime
            log.info("[documents] catalog loaded", count=len(self._docs), path=str(self._path))

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._docs.get(key)

    def all(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return dict(self._docs)

    def match(self, text: str) -> str | None:
        """Map free text (e.g. an analyzer's 'fee and scholarship details') to a
        doc_key. Exact key match first; then keyword / title substring scan."""
        if not text:
            return None
        t = text.strip().lower()
        with self._lock:
            if t in self._docs:
                return t
            best: tuple[int, str] | None = None
            for key, doc in self._docs.items():
                score = 0
                if key.lower() in t or t in key.lower():
                    score += 2
                title = str(doc.get("title", "")).lower()
                if title and (title in t or t in title):
                    score += 2
                for kw in doc.get("keywords", []) or []:
                    if isinstance(kw, str) and kw.lower() in t:
                        score += 1
                if score and (best is None or score > best[0]):
                    best = (score, key)
            return best[1] if best else None


def get_document(key: str) -> dict[str, Any] | None:
    """Resolve a document by exact key, or fuzzy-match free text to one."""
    repo = _DocumentRepo.instance()
    return repo.get(key) or (repo.get(repo.match(key) or "") if key else None)


def list_documents() -> list[dict[str, str]]:
    """Catalog summary for prompts / tool descriptions: [{key,title,description}]."""
    out: list[dict[str, str]] = []
    for key, doc in _DocumentRepo.instance().all().items():
        out.append(
            {
                "key": key,
                "title": str(doc.get("title", key)),
                "description": str(doc.get("description", "")),
            }
        )
    return out


def match_document(text: str) -> str | None:
    """Best-effort free-text → doc_key (None if nothing matches)."""
    return _DocumentRepo.instance().match(text)
