"""WhatsApp 24-hour customer-care window tracking.

Meta only allows free-form messages within 24h of the candidate's LAST inbound
message; outside that window you must send an approved template. We record each
inbound's timestamp here (keyed by digits-only number) so the send path can pick
free-form vs template.

Two layers:
  • An in-memory dict (`_last_inbound`) is the hot cache — every inbound stamps
    it via `mark_inbound`, and the send path reads it first.
  • The DURABLE record lives in the BusinessLayer (`leads.last_whatsapp_inbound_at`,
    stamped on every inbound WhatsApp session open). On a cache MISS — typically
    right after a backend restart, when the dict is empty — `window_open` reads
    that timestamp once and seeds the cache, so a restart no longer silently
    collapses every candidate's window to "closed".

The DEFAULT for an unknown number is still "closed" → the send path uses a
template, which always works to initiate. So the worst case is a template where
free-form would also have worked — never a silent free-form failure.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from agent_backend.infra import get_logger

log = get_logger(__name__)

_lock = threading.Lock()
_last_inbound: dict[str, float] = {}

WINDOW_SECONDS = 24 * 60 * 60


def _digits(number: str) -> str:
    return (number or "").strip().lstrip("+").replace(" ", "").replace("-", "")


def mark_inbound(number: str, *, ts: float | None = None) -> None:
    """Record that `number` just messaged us (opens/refreshes their 24h window)."""
    key = _digits(number)
    if not key:
        return
    with _lock:
        _last_inbound[key] = ts if ts is not None else time.time()


def _parse_utc_epoch(value: str | None) -> float | None:
    """Parse a BusinessLayer naive-UTC ISO timestamp into epoch seconds (the unit
    `time.time()` and `_last_inbound` use). Returns None on anything unparseable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    # BusinessLayer stores naive UTC (datetime.utcnow). Treat a missing tzinfo as
    # UTC so the epoch lines up with time.time().
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


async def _fetch_last_inbound(number: str) -> float | None:
    """Durable fallback: read the candidate's last-inbound timestamp from the
    BusinessLayer lead view. Best-effort — any failure (integration off, unknown
    number, network) returns None and the window defaults to closed."""
    try:
        from agent_backend.integrations import business as _biz

        lead = await _biz.find_lead_by_phone(number)
    except Exception as e:  # noqa: BLE001
        log.debug("[whatsapp] window DB lookup failed", err=str(e)[:160])
        return None
    if not lead:
        return None
    return _parse_utc_epoch(lead.get("last_whatsapp_inbound_at"))


async def window_open(number: str) -> bool:
    """True if `number` messaged us within the last 24h (free-form allowed).

    Checks the in-memory cache first; on a miss falls back to the durable
    BusinessLayer record and seeds the cache so later checks stay in-memory."""
    key = _digits(number)
    if not key:
        return False
    with _lock:
        last = _last_inbound.get(key)
    if last is None:
        # Cache miss (e.g. fresh after a restart) — consult the durable record.
        last = await _fetch_last_inbound(number)
        if last is not None:
            with _lock:
                _last_inbound[key] = last
    return last is not None and (time.time() - last) < WINDOW_SECONDS
