"""Telephony abstraction — exception hierarchy.

Three levels:
  TelephonyError              — base; anything raised by a provider adapter
    ├─ UnsupportedCapability  — caller asked for something this provider can't do
    │                          (e.g. warm transfer on a provider that only does cold).
    │                          Callers should check `provider.capabilities` first;
    │                          this raises only as a defence-in-depth.
    ├─ TransferFailed         — transfer (warm or cold) didn't complete. Carries the
    │                          state the transfer was in when it failed, so the brain
    │                          can decide whether to retry, hand back to the bot, or
    │                          tell the candidate the human is unreachable.
    └─ ProviderHTTPError      — wraps a vendor SDK's HTTP error so callers don't have
                                to import every vendor exception. The original is
                                still available via `__cause__` for debugging.

Keep this file dependency-free so any provider module can import it without
import-cycle risk.
"""
from __future__ import annotations


class TelephonyError(Exception):
    """Base for every exception raised by a telephony provider adapter."""


class UnsupportedCapability(TelephonyError):
    """The provider can't honour the requested method.

    Callers SHOULD inspect `provider.capabilities` before invoking optional
    operations; this exception exists so a missed check fails loudly instead
    of silently doing nothing.
    """

    def __init__(self, provider_name: str, capability: str) -> None:
        self.provider_name = provider_name
        self.capability = capability
        super().__init__(
            f"Provider {provider_name!r} does not support capability {capability!r}. "
            f"Check provider.capabilities.{capability} before calling."
        )


class TransferFailed(TelephonyError):
    """A transfer (warm or cold) failed to complete.

    Attributes:
        call_id: the original call_id; still alive unless `bot_dropped=True`.
        target_e164: the human agent we were transferring to.
        stage: where the failure occurred — one of:
                 'add_participant', 'await_answer', 'consult', 'remove_bot'
        bot_dropped: True if the bot had already left the call when the
                     transfer failed (i.e. candidate is now alone or with a
                     half-bridged agent — recovery is harder).
    """

    def __init__(
        self,
        *,
        call_id: str,
        target_e164: str,
        stage: str,
        bot_dropped: bool = False,
        reason: str = "",
    ) -> None:
        self.call_id = call_id
        self.target_e164 = target_e164
        self.stage = stage
        self.bot_dropped = bot_dropped
        self.reason = reason
        super().__init__(
            f"Transfer to {target_e164} failed at stage={stage!r} "
            f"(bot_dropped={bot_dropped}): {reason or '<no reason>'}"
        )


class ProviderHTTPError(TelephonyError):
    """A provider's HTTP API returned an error.

    Wraps the underlying vendor exception so the channel layer doesn't need
    to import every SDK's exception module. The original is preserved via
    `raise ... from e` so the traceback still shows the root cause.
    """

    def __init__(
        self,
        provider_name: str,
        status_code: int | None,
        message: str,
    ) -> None:
        self.provider_name = provider_name
        self.status_code = status_code
        super().__init__(
            f"[{provider_name}] HTTP error"
            + (f" (status={status_code})" if status_code is not None else "")
            + f": {message}"
        )


__all__ = [
    "TelephonyError",
    "UnsupportedCapability",
    "TransferFailed",
    "ProviderHTTPError",
]
