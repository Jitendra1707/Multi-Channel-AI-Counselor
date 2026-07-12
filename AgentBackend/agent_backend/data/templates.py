"""Approved WhatsApp template registry — the source of truth for templates.

One hot-reloadable JSON file (`templates_file`) mapping a stable `template_key`
→ {name, languages, params, header}. Decoupled from the document catalog so a
template can be:

  - referenced by many documents (documents.json carries a `template_key`), or
  - sent standalone with no document at all (e.g. the `outreach` template used
    to re-engage a candidate who missed a call or to start a cold WhatsApp
    thread).

Each entry:
  name        EXACT approved Meta template name (what Plivo/Meta matches on).
  languages   lead language_preference → approved BCP-47 code (e.g. "en"→"en_US").
  params      ORDERED body field names for {{1}}..{{n}} — documents/the missed-
              call path build the positional values in this order.
  header      "document" | "image" | "none" — whether the template has a media
              header (the doc link is passed as a header attachment vs in body).

Mirrors data.documents: a missing/malformed file yields an empty registry + a
logged warning rather than a crash (sends then degrade to a logged failure).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from agent_backend.config import get_settings
from agent_backend.infra import get_logger

log = get_logger(__name__)


class _TemplateRepo:
    _instance: "_TemplateRepo | None" = None
    _instance_lock = threading.Lock()

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._templates: dict[str, dict[str, Any]] = {}
        self._mtime: float | None = None

    @classmethod
    def instance(cls) -> "_TemplateRepo":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(Path(get_settings().templates_file).resolve())
            cls._instance._reload_if_changed()
            return cls._instance

    def _reload_if_changed(self) -> None:
        with self._lock:
            try:
                mtime = self._path.stat().st_mtime
            except FileNotFoundError:
                if self._templates:
                    self._templates = {}
                    log.warning("[templates] registry file gone — registry now empty", path=str(self._path))
                return
            if self._mtime == mtime:
                return
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                log.warning("[templates] registry parse failed — keeping previous", path=str(self._path), err=str(e))
                return
            if not isinstance(raw, dict):
                log.warning("[templates] registry is not a JSON object — ignoring", path=str(self._path))
                return
            self._templates = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
            self._mtime = mtime
            log.info("[templates] registry loaded", count=len(self._templates), path=str(self._path))

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._templates.get(key)

    def all(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return dict(self._templates)


def get_template(key: str) -> dict[str, Any] | None:
    """Resolve a template by its registry key (exact match). None if unknown."""
    if not key:
        return None
    return _TemplateRepo.instance().get(key)


def resolve_language(tmpl: dict[str, Any], lead_pref: str | None) -> str:
    """Map a lead's language_preference to the template's approved BCP-47 code,
    falling back to the first configured language, then 'en_US'."""
    langs = tmpl.get("languages") or {}
    if lead_pref and lead_pref in langs:
        return str(langs[lead_pref])
    if langs:
        return str(next(iter(langs.values())))
    return "en_US"


def list_templates() -> list[dict[str, str]]:
    """Registry summary for prompts / debugging: [{key, name, description}]."""
    out: list[dict[str, str]] = []
    for key, tmpl in _TemplateRepo.instance().all().items():
        out.append(
            {
                "key": key,
                "name": str(tmpl.get("name", "")),
                "description": str(tmpl.get("description", "")),
            }
        )
    return out
