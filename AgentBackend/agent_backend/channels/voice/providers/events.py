"""Normalised telephony events — one shape, all providers.

Every provider's webhook (ACS CloudEvents, Twilio status callbacks, Exotel
ExoML callbacks, Knowlarity events) lands in /api/voice/<provider>/events.
The provider's `parse_event(body, headers)` translates the vendor payload
into zero or more `NormalizedEvent`s so the routes layer + downstream
handlers (LeadRepo updates, FSM transitions, analytics) speak ONE vocabulary.

The provider's vendor-specific envelope is preserved on `.raw` for
debugging and for edge cases the abstraction doesn't yet cover.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

EventType = Literal[
    # --- lifecycle ---
    "call_connected",            # carrier bridged; media stream about to start
    "call_disconnected",         # call ended (any reason)
    "call_failed",               # never reached connected state (no answer, busy, etc.)

    # --- media streaming ---
    "media_streaming_started",
    "media_streaming_stopped",

    # --- transfer ---
    "transfer_initiated",        # add_participant / dial-conference fired
    "transfer_completed",        # human agent now on the call
    "transfer_failed",           # agent unreachable; bot may need to recover

    # --- DTMF ---
    "dtmf_received",             # candidate pressed a key (payload: {"tone": "5"})

    # --- recording ---
    "recording_started",
    "recording_completed",       # payload: {"recording_url": "...", "duration_ms": ...}

    # --- answering-machine detection ---
    "amd_human",
    "amd_machine",

    # --- play (used during transfer hand-off + IVR prompts) ---
    "play_completed",
]


@dataclass(frozen=True)
class NormalizedEvent:
    """Provider-agnostic event. One inbound webhook → 0..N of these."""

    type: EventType
    call_id: str

    # Provider-typed extras. Stable, documented keys per event type:
    #   call_disconnected   → {"reason": str, "duration_ms": int}
    #   transfer_failed     → {"stage": str, "reason": str}
    #   dtmf_received       → {"tone": "0".."9" | "*" | "#"}
    #   recording_completed → {"recording_url": str, "duration_ms": int}
    payload: dict[str, str | int | float | bool] = field(default_factory=dict)

    # Correlation we threaded into the call at dial-time (e.g. lead_id).
    # Recovered from the provider's `operation_context` / custom headers.
    correlation: dict[str, str] = field(default_factory=dict)

    # The provider's full vendor-shaped envelope. Don't read this in business
    # logic — use `payload` / `correlation`. It's here for traceability.
    raw: dict = field(default_factory=dict)

    received_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def short(self) -> str:
        """Compact one-line summary for structured logs."""
        bits = [self.type, self.call_id[:12]]
        if "lead_id" in self.correlation:
            bits.append(f"lead={self.correlation['lead_id'][:10]}")
        if "tone" in self.payload:
            bits.append(f"tone={self.payload['tone']}")
        return ":".join(bits)


__all__ = ["EventType", "NormalizedEvent"]
