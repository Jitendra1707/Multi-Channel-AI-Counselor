"""Shared LiveKit control-plane implementation.

Cloud and self-hosted LiveKit speak the IDENTICAL server API (both use the
`livekit-api` SDK) — the only real differences are the URL/keys (config) and any
provider-specific defaults. So the actual logic lives here once, in
`BaseLiveKitProvider`, and `cloud.py` / `selfhosted.py` are thin subclasses that
set `name` and can override behaviour later if the two ever diverge.

Keeping them as separate classes (rather than one) is deliberate: the day Cloud
or OSS grows a quirk the other lacks, it's a one-file change with nothing else in
the codebase affected — the provider Protocol shields every caller.
"""

from __future__ import annotations

import datetime
import uuid

from google.protobuf.json_format import MessageToDict
from livekit import api

from livekit_svc.config import get_settings
from livekit_svc.logging import get_logger
from livekit_svc.providers.base import MeetingConfigError, ProviderInfo

log = get_logger(__name__)


class BaseLiveKitProvider:
    """Concrete LiveKit control plane shared by cloud + self-hosted."""

    name: str = "base"

    # ------------------------------------------------------------------
    def _creds(self) -> tuple[str, str, str]:
        """(url, api_key, api_secret) or raise MeetingConfigError."""
        s = get_settings()
        if not s.configured():
            raise MeetingConfigError(
                "LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set "
                f"for the '{self.name}' provider."
            )
        return s.livekit_url, s.livekit_api_key, s.livekit_api_secret

    def info(self) -> ProviderInfo:
        s = get_settings()
        return ProviderInfo(
            provider=self.name,
            url=s.livekit_url,
            configured=s.configured(),
        )

    # ------------------------------------------------------------------
    async def create_room(self, name: str | None = None) -> str:
        url, api_key, api_secret = self._creds()
        s = get_settings()
        room_name = name or f"meet-{uuid.uuid4().hex[:12]}"
        lk = api.LiveKitAPI(url, api_key, api_secret)
        try:
            await lk.room.create_room(
                api.CreateRoomRequest(
                    name=room_name,
                    empty_timeout=s.livekit_room_empty_timeout_s,
                    max_participants=s.livekit_room_max_participants,
                )
            )
            log.info("room created", provider=self.name, room=room_name)
        finally:
            await lk.aclose()
        return room_name

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
        _, api_key, api_secret = self._creds()
        s = get_settings()
        grants = api.VideoGrants(
            room_join=True,
            room=room,
            can_publish=can_publish,
            can_subscribe=can_subscribe,
            can_publish_data=True,
        )
        token = (
            api.AccessToken(api_key, api_secret)
            .with_identity(identity)
            .with_name(display_name)
            # bare role string — the agent reads participant.metadata for speaker
            # attribution. Richer JSON can go here later without changing readers.
            .with_metadata(role)
            .with_grants(grants)
            .with_ttl(datetime.timedelta(seconds=s.livekit_token_ttl_s))
        )
        return token.to_jwt()

    async def delete_room(self, room: str) -> None:
        url, api_key, api_secret = self._creds()
        lk = api.LiveKitAPI(url, api_key, api_secret)
        try:
            await lk.room.delete_room(api.DeleteRoomRequest(room=room))
            log.info("room deleted", provider=self.name, room=room)
        except Exception as e:  # noqa: BLE001
            # Deleting an absent / already-closed room is fine — log + move on.
            log.debug("delete_room non-fatal", provider=self.name, room=room, err=str(e))
        finally:
            await lk.aclose()

    def verify_webhook(self, body: str, auth_header: str) -> dict:
        """Verify the webhook signature against our key/secret and return the
        event as a plain dict. Raises if the signature is invalid."""
        _, api_key, api_secret = self._creds()
        receiver = api.WebhookReceiver(api.TokenVerifier(api_key, api_secret))
        event = receiver.receive(body, auth_header)  # raises on bad signature
        return MessageToDict(event, preserving_proto_field_name=True)


__all__ = ["BaseLiveKitProvider"]
