"""Provider selector — `get_provider()` returns the active LiveKit backend.

Picks cloud vs self-hosted from `LIVEKIT_PROVIDER` and caches the instance (the
provider is stateless — it builds a fresh `LiveKitAPI` client per call — so one
instance per process is safe). Switching providers = change the env var + restart
this service; nothing else in the stack notices.
"""

from __future__ import annotations

from functools import lru_cache

from livekit_svc.config import get_settings
from livekit_svc.providers.base import (
    LiveKitProvider,
    MeetingConfigError,
    ProviderInfo,
)
from livekit_svc.providers.cloud import CloudProvider
from livekit_svc.providers.selfhosted import SelfHostedProvider


@lru_cache(maxsize=1)
def get_provider() -> LiveKitProvider:
    """Return the configured provider singleton. Clear via
    get_provider.cache_clear() in tests when flipping LIVEKIT_PROVIDER."""
    provider = get_settings().livekit_provider
    if provider == "selfhosted":
        return SelfHostedProvider()
    return CloudProvider()


__all__ = [
    "LiveKitProvider",
    "MeetingConfigError",
    "ProviderInfo",
    "get_provider",
]
