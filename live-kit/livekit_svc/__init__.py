"""livekit_svc — AegisAvatar's LiveKit control-plane service.

Owns room creation, JWT minting, and webhook verification behind a provider
abstraction so LiveKit Cloud and a self-hosted open-source server are swappable
by one env var (LIVEKIT_PROVIDER). AegisBackend calls this service's HTTP API
instead of talking to LiveKit directly, keeping the Cloud↔OSS choice in one place.
"""

__version__ = "0.1.0"
