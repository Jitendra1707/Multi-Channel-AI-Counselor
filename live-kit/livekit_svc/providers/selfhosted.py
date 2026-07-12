"""Self-hosted (open-source) LiveKit provider.

Talks to a livekit-server you run yourself (Docker / VM / local — CPU-only, no
GPU; it's an SFU that forwards packets, it does not transcode). Same server API
as Cloud, so it's the shared implementation with `name = "selfhosted"`.

Operational notes live in the live-kit-opensource skill + this service's README;
the canonical run/config reference is the cloned repo at
`Music/livekit-opensource` (config-sample.yaml). The #1 self-host gotcha:
`rtc.use_external_ip: true` on a NATed VM, or media connects then goes silent.
"""

from __future__ import annotations

from livekit_svc.providers._common import BaseLiveKitProvider


class SelfHostedProvider(BaseLiveKitProvider):
    name = "selfhosted"


__all__ = ["SelfHostedProvider"]
