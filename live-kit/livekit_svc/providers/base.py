"""LiveKitProvider — the contract every provider (cloud / self-hosted) implements.

The control plane is exactly three operations; defining them as a `Protocol`
(structural interface) lets us swap implementations by config without the rest of
the service knowing which backend is live. This mirrors AegisBackend's pluggable
telephony pattern (a VoiceProvider Protocol with ACS / Twilio / Plivo impls).

    create_room(name)               -> room name (idempotent)
    mint_token(room, identity, ...) -> signed join JWT
    verify_webhook(body, auth)      -> validated event dict

`MeetingConfigError` is raised when the active provider isn't configured; the
route layer turns it into a clean 503 (graceful degrade, never a 500).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class MeetingConfigError(RuntimeError):
    """The active LiveKit provider is missing URL / API key / secret."""


@dataclass(frozen=True)
class ProviderInfo:
    """What this service advertises about the active provider (for /health and
    so the web-app can fetch the wss URL from one source of truth)."""

    provider: str          # "cloud" | "selfhosted"
    url: str               # the wss:// / ws:// URL clients connect to
    configured: bool


@runtime_checkable
class LiveKitProvider(Protocol):
    """Structural interface for a LiveKit control-plane backend."""

    name: str

    def info(self) -> ProviderInfo:
        """Provider summary for /health + the web-app's URL lookup."""
        ...

    async def create_room(self, name: str | None = None) -> str:
        """Create (or reuse — idempotent) a room; return its name. Generates a
        fresh name when `name` is None."""
        ...

    def mint_token(
        self,
        *,
        room: str,
        identity: str,
        display_name: str,
        role: str,
        can_publish: bool = True,
        can_subscribe: bool = True,
    ) -> str:
        """Mint a join JWT. `role` is stamped into participant metadata so the
        agent can attribute transcripts (candidate / counsellor / agent)."""
        ...

    async def delete_room(self, room: str) -> None:
        """Delete a room (best-effort; absent room is not an error)."""
        ...

    def verify_webhook(self, body: str, auth_header: str) -> dict:
        """Verify a LiveKit webhook's signature and return the event as a dict.
        Raises if the signature is invalid."""
        ...


__all__ = ["LiveKitProvider", "MeetingConfigError", "ProviderInfo"]
