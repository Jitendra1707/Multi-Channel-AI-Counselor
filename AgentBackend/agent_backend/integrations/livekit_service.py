"""Client for the live-kit control-plane service (the `live-kit/` sibling).

When `LIVEKIT_SERVICE_URL` is set, the meeting channel asks this service for
rooms + tokens + the SFU URL instead of using `livekit-api` directly. That keeps
the Cloud↔self-hosted choice in ONE place (the service's LIVEKIT_PROVIDER) and
AgentBackend unchanged when you swap backends.

Mirrors `integrations/business.py`: a lazily-built shared httpx client + a short
timeout. Unlike business.py these calls are NOT best-effort-swallow — minting a
token is on the critical path of scheduling a meeting, so failures raise
`LiveKitServiceError` and the caller decides (the scheduler surfaces it as a 502/
503). `enabled()` is the switch the scheduler/runner use to pick service mode vs
the legacy direct-mint fallback.
"""

from __future__ import annotations

from typing import Any

from agent_backend.config import get_settings
from agent_backend.infra import get_logger

log = get_logger(__name__)

_client: Any = None


class LiveKitServiceError(RuntimeError):
    """The live-kit service call failed (network, non-2xx, or service 503)."""


def enabled() -> bool:
    """True when AgentBackend should route LiveKit ops through the service."""
    return bool(get_settings().livekit_service_url)


async def _get_client() -> Any:
    global _client
    if _client is not None:
        return _client
    import httpx

    s = get_settings()
    _client = httpx.AsyncClient(
        base_url=s.livekit_service_url.rstrip("/"),
        timeout=s.livekit_service_timeout_s,
    )
    log.info("[livekit-svc] client enabled", url=s.livekit_service_url)
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:  # noqa: BLE001
            pass
        _client = None


async def _post(path: str, json: dict | None = None) -> dict:
    client = await _get_client()
    try:
        resp = await client.post(path, json=json or {})
    except Exception as e:  # noqa: BLE001
        raise LiveKitServiceError(f"live-kit service unreachable: {e}") from e
    if resp.status_code == 503:
        # Service up but its provider isn't configured — surface as config error.
        raise LiveKitServiceError(f"live-kit service not configured: {resp.text}")
    if resp.status_code >= 300:
        raise LiveKitServiceError(f"live-kit service {resp.status_code}: {resp.text}")
    return resp.json()


async def _get(path: str) -> dict:
    client = await _get_client()
    try:
        resp = await client.get(path)
    except Exception as e:  # noqa: BLE001
        raise LiveKitServiceError(f"live-kit service unreachable: {e}") from e
    if resp.status_code >= 300:
        raise LiveKitServiceError(f"live-kit service {resp.status_code}: {resp.text}")
    return resp.json()


# ---------------------------------------------------------------------------
# Control-plane operations (1:1 with the service endpoints)
# ---------------------------------------------------------------------------
async def create_room(room: str | None = None) -> str:
    data = await _post("/rooms", {"room": room})
    return data["room"]


async def mint_token(
    *,
    room: str,
    identity: str,
    display_name: str,
    role: str,
    can_publish: bool = True,
    can_subscribe: bool = True,
) -> dict:
    """Returns {room, identity, token, url}. `url` is the SFU wss:// URL the
    holder connects to (single source of truth from the service)."""
    return await _post(
        "/token",
        {
            "room": room,
            "identity": identity,
            "display_name": display_name,
            "role": role,
            "can_publish": can_publish,
            "can_subscribe": can_subscribe,
        },
    )


async def delete_room(room: str) -> None:
    try:
        await _post(f"/rooms/{room}/delete")
    except LiveKitServiceError as e:
        # Deletion is best-effort — log, don't propagate into teardown.
        log.debug("[livekit-svc] delete_room non-fatal", room=room, err=str(e))


async def get_url() -> str:
    """The SFU URL the active provider advertises (for the agent's transport)."""
    data = await _get("/config")
    return data.get("url") or ""


__all__ = [
    "LiveKitServiceError",
    "enabled",
    "aclose",
    "create_room",
    "mint_token",
    "delete_room",
    "get_url",
]
