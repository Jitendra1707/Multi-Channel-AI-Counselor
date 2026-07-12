"""Shared helpers for telephony provider adapters.

Every concrete provider (ACS, Twilio, Exotel, Knowlarity) extends BaseProvider
so the same boilerplate doesn't have to repeat in each file:

  - Event-id LRU dedup. Carriers retry webhooks aggressively; without
    dedup a single 'call_disconnected' may be processed 3-5 times and a
    LeadRepo `set_status` call can race itself.
  - Default capability-gated stubs. Unsupported operations raise
    `UnsupportedCapability` with a clean message instead of NotImplementedError
    or — worse — silent no-op.
  - Structured-log enrichment. Provider name + call_id are auto-included
    in every log line emitted by the adapter.

BaseProvider is NOT in the TelephonyProvider Protocol's static type — Protocols
in Python are structural. Concretes match the Protocol because they implement
the right method signatures, not because they inherit from BaseProvider.
That keeps the inheritance optional: a test fake can satisfy the Protocol
without pulling in BaseProvider's machinery.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Mapping
from typing import Any

from agent_backend.channels.voice.providers.base import (
    AnswerRequest,
    CallResult,
    DialRequest,
    ProviderCapabilities,
    TransferRequest,
)
from agent_backend.channels.voice.providers.events import NormalizedEvent
from agent_backend.channels.voice.providers.exceptions import UnsupportedCapability
from agent_backend.infra import get_logger


# ---------------------------------------------------------------------------
# Webhook idempotency — keyed by event_id when the provider supplies one,
# else by a synthesised hash. Cap chosen to cover one busy hour without
# bloating memory; oldest entries fall out FIFO.
# ---------------------------------------------------------------------------
_DEDUP_CAP = 4096


class _EventDedup:
    """Tiny LRU set of recently-seen event ids. Process-local."""

    def __init__(self, cap: int = _DEDUP_CAP) -> None:
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._cap = cap

    def is_dup(self, event_id: str) -> bool:
        if not event_id:
            return False
        if event_id in self._seen:
            self._seen.move_to_end(event_id)
            return True
        self._seen[event_id] = None
        while len(self._seen) > self._cap:
            self._seen.popitem(last=False)
        return False


class BaseProvider:
    """Optional base class providers extend to inherit shared plumbing.

    Subclasses MUST set:
      - `name`: str
      - `capabilities`: ProviderCapabilities

    Subclasses override only the methods they implement. Anything left
    unimplemented raises UnsupportedCapability when called.
    """

    name: str = "base"
    capabilities: ProviderCapabilities

    def __init__(self) -> None:
        self._dedup = _EventDedup()
        self._log = get_logger(f"telephony.{self.name}")

    # ----- call lifecycle (subclass overrides) ----------------------------
    async def dial(self, req: DialRequest) -> CallResult:
        raise UnsupportedCapability(self.name, "outbound")

    async def answer(self, req: AnswerRequest) -> CallResult:
        raise UnsupportedCapability(self.name, "inbound")

    async def hangup(self, call_id: str) -> None:
        raise UnsupportedCapability(self.name, "hangup")

    async def transfer(self, req: TransferRequest) -> None:
        cap = "transfer_warm" if req.mode == "warm" else "transfer_cold"
        raise UnsupportedCapability(self.name, cap)

    # ----- in-call interactions (subclass overrides) ----------------------
    async def send_dtmf(
        self, call_id: str, digits: str, target_e164: str | None = None
    ) -> None:
        raise UnsupportedCapability(self.name, "dtmf_send")

    async def start_dtmf_detection(self, call_id: str, from_e164: str) -> None:
        raise UnsupportedCapability(self.name, "dtmf_receive")

    async def stop_dtmf_detection(self, call_id: str, from_e164: str) -> None:
        raise UnsupportedCapability(self.name, "dtmf_receive")

    async def play_audio_url(self, call_id: str, url: str) -> None:
        raise UnsupportedCapability(self.name, "play_audio_url")

    async def start_recording(self, call_id: str) -> str:
        raise UnsupportedCapability(self.name, "recording")

    async def stop_recording(self, call_id: str) -> None:
        raise UnsupportedCapability(self.name, "recording")

    # ----- event normalisation (subclass overrides) -----------------------
    def parse_event(
        self,
        body: bytes,
        headers: Mapping[str, str],
    ) -> Iterable[NormalizedEvent]:
        raise UnsupportedCapability(self.name, "event_parsing")

    # ----- streaming serializer (subclass overrides) ----------------------
    def frame_serializer(self) -> Any:
        raise UnsupportedCapability(self.name, "streaming")

    # ----- helpers shared across providers --------------------------------
    def _is_duplicate_event(self, event_id: str) -> bool:
        """Idempotency check used inside `parse_event`. Subclasses call this
        once per event in the incoming payload; duplicates are filtered out."""
        return self._dedup.is_dup(event_id)


__all__ = ["BaseProvider"]
