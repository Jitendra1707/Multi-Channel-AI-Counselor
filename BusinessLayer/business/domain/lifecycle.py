"""Lead lifecycle helpers — who is dialable, what's terminal."""

from __future__ import annotations

from business.models import LeadStatus


def is_terminal(status: str) -> bool:
    return status in LeadStatus.TERMINAL


def is_dialable(status: str) -> bool:
    return status in LeadStatus.DIALABLE
