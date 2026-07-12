"""TelephonyProvider Protocol — the seam every telephony backend implements.

The voice channel depends only on this Protocol, never on a vendor SDK.
Adding Twilio / Exotel / Knowlarity / Plivo is one file in this directory
and one line in `providers/__init__.py`.

This module defines:
  - Dataclasses for every request/response shape:
      DialRequest, AnswerRequest, TransferRequest, CallResult
  - ProviderCapabilities — per-provider feature flags consumed at runtime
  - TelephonyProvider — the Protocol the voice channel depends on
  - Backwards-compat aliases (VoiceProvider, the old DialRequest fields)

Backwards-compat
----------------
The pre-abstraction code knew only `VoiceProvider`, `DialRequest`, `DialResult`.
We preserve those names — `VoiceProvider` is an alias for `TelephonyProvider`,
and `DialRequest` keeps its old field order; new fields are optional with
sensible defaults. Every existing import keeps resolving.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from agent_backend.channels.voice.providers.audio import AudioFormat
from agent_backend.channels.voice.providers.events import NormalizedEvent


# ---------------------------------------------------------------------------
# Capability descriptor — runtime introspection of what a provider supports.
# Callers gate optional operations on these flags; provider methods can
# additionally raise UnsupportedCapability as defence-in-depth.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProviderCapabilities:
    name: str

    supports_outbound:       bool = True
    supports_inbound:        bool = False
    supports_streaming:      bool = True   # bidirectional WS audio
    supports_transfer_cold:  bool = False  # blind transfer
    supports_transfer_warm:  bool = False  # consult-then-merge
    supports_dtmf_receive:   bool = False
    supports_dtmf_send:      bool = False
    supports_recording:      bool = False
    supports_amd:            bool = False  # answering-machine detection
    supports_play_audio_url: bool = False  # play remote audio file inline

    # The wire format this provider's media WS speaks (canonical = PCM16K mono).
    audio_format: AudioFormat = AudioFormat.PCM16K_MONO


# ---------------------------------------------------------------------------
# Request / response shapes.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DialRequest:
    """What the channel hands to a provider to start an OUTBOUND call.

    Backwards-compat: the original four fields (`to_e164`, `from_e164`,
    `callback_url`, `media_ws_url`) keep their meaning and order. New
    fields are optional with safe defaults so older call sites compile
    unchanged.
    """

    to_e164: str                      # E.164 destination (the lead's mobile)
    from_e164: str                    # E.164 CLI (DLT-registered for India)
    callback_url: str                 # https URL provider POSTs lifecycle events to
    media_ws_url: str                 # wss URL provider streams audio over

    # Echoed in every callback so the webhook handler can recover identity
    # without a side-channel. Common keys: {"lead_id", "session_id"}.
    correlation: dict[str, str] = field(default_factory=dict)

    record: bool = False              # start recording when call connects
    amd: bool = False                 # detect answering-machine; emit amd_human/amd_machine


@dataclass(frozen=True)
class AnswerRequest:
    """Accept an INBOUND call the provider has handed us.

    Each provider gives us an opaque handle in its inbound-call webhook
    that's required to answer (ACS: incomingCallContext; Twilio: CallSid +
    a TwiML response; Exotel/Knowlarity: provider-specific). We carry it
    on `incoming_call_context` so the adapter can pass it back to the SDK.
    """

    incoming_call_context: str        # opaque provider handle
    callback_url: str
    media_ws_url: str
    correlation: dict[str, str] = field(default_factory=dict)
    record: bool = False


@dataclass(frozen=True)
class TransferRequest:
    """Hand the candidate to a human agent.

    Cold: bot drops out immediately, candidate is connected to agent.
    Warm: bot adds agent as a 3rd party, optionally speaks a handoff brief,
          waits `consult_secs` for a verbal hand-off, then drops out.

    If the transfer fails (agent unreachable, busy, etc.) the adapter
    raises TransferFailed with the stage it failed in so the brain can
    decide whether to recover (apologise + offer call-back) or retry.
    """

    call_id: str
    target_e164: str                  # the human agent's number
    callback_url: str
    mode: Literal["cold", "warm"] = "warm"

    # Warm-only: what the bot says to the agent during 3-way before exiting.
    # Plain text — provider's TTS is used inline if the provider supports it,
    # otherwise the bot's own TTS frames the message through the media WS.
    handoff_text: str | None = None

    # Warm-only: seconds to stay in the 3-way bridge before removing the bot.
    # 0 = drop immediately after `handoff_text` finishes playing.
    consult_secs: float = 3.0

    correlation: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CallResult:
    """Provider's response after dial / answer / transfer.

    Status is a coarse vendor-agnostic state; for the actual carrier-level
    state machine, listen for NormalizedEvent's on the events route.
    """

    call_id: str
    status: Literal["queued", "ringing", "in_progress", "completed", "failed"] = "queued"
    raw: dict | None = None


# Old name, same shape. Existing imports of DialResult keep working.
DialResult = CallResult


# ---------------------------------------------------------------------------
# The Protocol every provider satisfies.
# ---------------------------------------------------------------------------
@runtime_checkable
class TelephonyProvider(Protocol):
    """The single interface the voice channel depends on.

    Every method is async — even synchronous SDKs (ACS 1.5.0) get adapted
    behind an async wrapper so callers don't branch on provider.
    """

    name: str
    capabilities: ProviderCapabilities

    # --- call lifecycle ----------------------------------------------------
    async def dial(self, req: DialRequest) -> CallResult: ...
    async def answer(self, req: AnswerRequest) -> CallResult: ...
    async def hangup(self, call_id: str) -> None: ...
    async def transfer(self, req: TransferRequest) -> None: ...

    # --- in-call interactions ---------------------------------------------
    async def send_dtmf(self, call_id: str, digits: str, target_e164: str | None = None) -> None: ...
    async def start_dtmf_detection(self, call_id: str, from_e164: str) -> None: ...
    async def stop_dtmf_detection(self, call_id: str, from_e164: str) -> None: ...
    async def play_audio_url(self, call_id: str, url: str) -> None: ...
    async def start_recording(self, call_id: str) -> str: ...
    async def stop_recording(self, call_id: str) -> None: ...

    # --- event normalisation (called from the webhook route) -------------
    def parse_event(
        self,
        body: bytes,
        headers: Mapping[str, str],
    ) -> Iterable[NormalizedEvent]:
        """Translate one vendor webhook payload into 0..N NormalizedEvents."""
        ...

    # --- streaming-audio glue (called by the pipeline composer) -----------
    def frame_serializer(self) -> Any:
        """Return the Pipecat FrameSerializer instance paired with this provider.

        Typed `Any` here to keep this Protocol Pipecat-free (so test
        environments without Pipecat installed can still import base.py).
        Real return type: pipecat.serializers.base_serializer.FrameSerializer.
        """
        ...


# ---------------------------------------------------------------------------
# Backwards-compat alias — the pre-abstraction code knew this as VoiceProvider.
# Existing imports like `from ...providers.base import VoiceProvider` keep
# working. New code should import `TelephonyProvider` directly.
# ---------------------------------------------------------------------------
VoiceProvider = TelephonyProvider


__all__ = [
    "ProviderCapabilities",
    "DialRequest",
    "AnswerRequest",
    "TransferRequest",
    "CallResult",
    "DialResult",          # alias for CallResult
    "TelephonyProvider",
    "VoiceProvider",       # alias for TelephonyProvider
]
