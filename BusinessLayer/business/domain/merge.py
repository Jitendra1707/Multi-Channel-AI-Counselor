"""Idempotent merge / rollup rules — the heart of the cross-channel design.

Session analyses are immutable facts; the lead-level "report" is a deterministic
fold over them. These helpers are pure (no I/O) so they're trivially testable and
safe to re-run: applying the same analysis twice lands on the same lead state.
"""

from __future__ import annotations

from typing import Any

from business.models import LeadStatus


def _is_empty(v: Any) -> bool:
    """A value the merge should treat as 'no information' — never overwrites a
    known value with one of these."""
    if v is None:
        return True
    if isinstance(v, str) and not v.strip():
        return True
    if isinstance(v, (list, dict)) and len(v) == 0:
        return True
    return False


def merge_facts(existing: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    """Last-non-empty wins. New keys append; changed values update; a null/empty
    incoming value never erases a known one. Nested dicts merge recursively."""
    out: dict[str, Any] = dict(existing or {})
    for k, v in (new or {}).items():
        if _is_empty(v):
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge_facts(out[k], v)
        else:
            out[k] = v
    return out


def merge_open_concerns(
    existing: list[str] | None,
    new: list[str] | None,
    *,
    resolved: list[str] | None = None,
    cap: int = 12,
) -> list[str]:
    """Accumulate concerns across sessions (order-preserving, de-duped), then
    drop any a later session reports resolved. Case-insensitive de-dup."""
    seen: dict[str, str] = {}  # lower -> original
    for c in (existing or []) + (new or []):
        if not isinstance(c, str) or not c.strip():
            continue
        key = c.strip().lower()
        if key not in seen:
            seen[key] = c.strip()
    for r in resolved or []:
        seen.pop(str(r).strip().lower(), None)
    return list(seen.values())[-cap:]


def fold_status(current: str, proposed: str | None) -> str:
    """Recency wins, EXCEPT terminal states are sticky (never downgraded).

    A late-arriving older analysis can't move a CONVERTED/LOST/CLOSED lead back
    into the funnel. If the proposed status is unknown/empty, keep current.
    """
    if current in LeadStatus.TERMINAL:
        return current
    if not proposed:
        return current
    proposed = proposed.strip().lower()
    # Only accept statuses we recognise; otherwise keep current.
    known = {
        v for k, v in vars(LeadStatus).items()
        if isinstance(v, str) and not k.startswith("_")
    }
    return proposed if proposed in known else current


def clamp_score(v: Any, *, default: int = 0) -> int:
    """Coerce an analyzer score into 0-100."""
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return default
    return max(0, min(100, n))
