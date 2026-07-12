"""Provider factory.

`get_voice_provider()` returns the concrete VoiceProvider chosen by
`VOICE_PROVIDER` in .env. Add a new provider by:
  1. Creating `providers/<name>.py` exposing `build_provider() -> VoiceProvider`.
  2. Registering it in `_REGISTRY` below.
  3. Setting `VOICE_PROVIDER=<name>` in .env.

No other code touches the SDK — `routes.py` and `media_ws.py` work the
interface, not the implementation.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Callable

from agent_backend.channels.voice.providers.acs import build_provider as _build_acs
from agent_backend.channels.voice.providers.base import VoiceProvider
from agent_backend.channels.voice.providers.plivo import build_provider as _build_plivo
from agent_backend.config import get_settings

# Map env value → factory. New providers register here.
# NOTE: each factory imports its vendor SDK lazily (inside the provider's
# __init__), so registering a provider here does NOT require its SDK to be
# installed — only the SELECTED provider's SDK must be present at runtime.
_REGISTRY: dict[str, Callable[[], VoiceProvider]] = {
    "acs": _build_acs,
    "plivo": _build_plivo,
    # "twilio":     _build_twilio,      # PR 9
    # "exotel":     _build_exotel,      # PR 10
    # "knowlarity": _build_knowlarity,  # PR 11
}


@lru_cache(maxsize=1)
def get_voice_provider() -> VoiceProvider:
    """Return the configured telephony provider as a process singleton.

    Each provider holds heavyweight state (HTTP client + auth pool); creating
    a fresh one per call wastes connections AND breaks the active-calls
    registry pattern (the route would mutate one instance, the WS handler
    would read from another).

    Tests can reset the singleton with `get_voice_provider.cache_clear()`.
    """
    name = (get_settings().voice_provider or "acs").lower()
    factory = _REGISTRY.get(name)
    if factory is None:
        raise RuntimeError(
            f"Unknown VOICE_PROVIDER={name!r}. Available: {sorted(_REGISTRY)}"
        )
    return factory()


__all__ = ["VoiceProvider", "get_voice_provider"]
