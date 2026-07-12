"""LiveKit Cloud provider.

Managed SFU + global TURN + autoscaling. Uses the standard server API, so it's
the shared implementation with `name = "cloud"`. Override here only if Cloud ever
needs behaviour the OSS server doesn't.
"""

from __future__ import annotations

from livekit_svc.providers._common import BaseLiveKitProvider


class CloudProvider(BaseLiveKitProvider):
    name = "cloud"


__all__ = ["CloudProvider"]
