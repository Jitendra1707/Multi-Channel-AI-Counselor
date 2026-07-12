"""Plivo — Voice API provider (call-control surface only).

Plivo is ANSWER-URL driven, unlike ACS (which takes media-streaming options
inside the create-call API request):

  - Outbound: `calls.create(answer_url=…)` → when the callee picks up Plivo
    POSTs our answer URL → we return Plivo XML containing a `<Stream>` that
    opens a bidirectional audio WebSocket back to us.
  - Inbound:  the number's Plivo Application points its Answer URL at the same
    endpoint → same XML → same media WebSocket.

The answer XML + media WebSocket live in `channels/voice/plivo_routes.py`.
THIS module is only the call-control surface (dial / hangup) behind the
`TelephonyProvider` Protocol, so the rest of the voice channel never imports
the Plivo SDK. Selected when `VOICE_PROVIDER=plivo`; ACS is untouched.

Install:  pip install plivo
Env:      PLIVO_AUTH_ID / PLIVO_AUTH_TOKEN / PLIVO_FROM_NUMBER (+ PUBLIC_BASE_URL)

Audio: Plivo Audio Streaming is μ-law 8 kHz mono. The Pipecat
`PlivoFrameSerializer` (used in plivo_routes.py) resamples to/from the
canonical 16 kHz the brain + STT/TTS speak, so the pipeline is unchanged.
"""
from __future__ import annotations

from typing import Any

from agent_backend.channels.voice.providers._common import BaseProvider
from agent_backend.channels.voice.providers.audio import AudioFormat
from agent_backend.channels.voice.providers.base import (
    CallResult,
    DialRequest,
    ProviderCapabilities,
    TelephonyProvider,
)
from agent_backend.config import get_settings
from agent_backend.infra import get_logger

log = get_logger(__name__)


# Capabilities Plivo supports today. Inbound works via the answer-URL route
# (not an SDK answer_call), so it's flagged here even though `answer()` isn't
# implemented as an SDK call. Transfer / DTMF / recording stay as inherited
# UnsupportedCapability stubs until wired.
_PLIVO_CAPABILITIES = ProviderCapabilities(
    name="plivo",
    supports_outbound=True,
    supports_inbound=True,
    supports_streaming=True,
    audio_format=AudioFormat.MULAW8K_MONO,   # Plivo Audio Streaming = μ-law 8 kHz
)


class PlivoVoiceProvider(BaseProvider):
    name = "plivo"
    capabilities = _PLIVO_CAPABILITIES

    def __init__(self, *, auth_id: str, auth_token: str, from_number: str) -> None:
        super().__init__()
        # Late import so the rest of the app loads even if the SDK isn't
        # installed (mirrors the ACS provider). Only hit when VOICE_PROVIDER=plivo.
        from plivo import RestClient

        if not auth_id or not auth_token:
            raise RuntimeError("PLIVO_AUTH_ID / PLIVO_AUTH_TOKEN are empty — set them in .env")
        if not from_number:
            raise RuntimeError("PLIVO_FROM_NUMBER is empty — set it in .env")

        self._client = RestClient(auth_id, auth_token)
        self._from = from_number

    def _answer_url(self, lead_id: str) -> str:
        """The Answer URL Plivo POSTs when the callee picks up. Carries lead_id
        so the answer route can mint the media-WS URL for the right lead."""
        s = get_settings()
        base = (s.public_base_url or "").rstrip("/")
        https = base if base.startswith("http") else f"https://{base}"
        return f"{https}/api/voice/plivo/answer?lead_id={lead_id}"

    def _hangup_url(self) -> str:
        """The Hangup URL Plivo POSTs when the call ends (any party). Lets us
        cancel the local pipeline the instant the candidate hangs up instead of
        bleeding ~60-80 s of dead air while the silence monitor keeps firing —
        see plivo_routes.plivo_hangup. No lead_id needed: Plivo sends CallUUID,
        which is what the live-pipeline registry is keyed by."""
        s = get_settings()
        base = (s.public_base_url or "").rstrip("/")
        https = base if base.startswith("http") else f"https://{base}"
        return f"{https}/api/voice/plivo/hangup"

    async def dial(self, req: DialRequest) -> CallResult:
        """Place an OUTBOUND call. Plivo's create-call is answer-URL driven —
        the media-streaming wiring happens later in the answer XML, so unlike
        ACS we ignore `req.media_ws_url` / `req.callback_url` and build our own
        answer URL from PUBLIC_BASE_URL + the correlation's lead_id.

        `create()` returns a `request_uuid`; the actual call UUID (used for
        hangup) is assigned later and arrives on the media-stream `start` event,
        where plivo_routes.py stashes it on the Session as `call_id`.
        """
        lead_id = req.correlation.get("lead_id", "")
        answer_url = self._answer_url(lead_id)
        # plivo's SDK is synchronous; the create call is a quick HTTP POST.
        resp = self._client.calls.create(
            from_=self._from,
            to_=req.to_e164,
            answer_url=answer_url,
            answer_method="POST",
            # Plivo POSTs this when the call ends (incl. candidate-initiated
            # hangup) → /plivo/hangup cancels the live pipeline immediately so
            # the brain/silence monitor stop talking into a dead line.
            hangup_url=self._hangup_url(),
            hangup_method="POST",
        )
        request_uuid = _extract(resp, "request_uuid")
        log.info(
            "[plivo] dial initiated",
            to=req.to_e164,
            request_uuid=request_uuid,
            lead_id=lead_id,
        )
        return CallResult(call_id=str(request_uuid or ""), status="queued", raw={"sdk": "plivo"})

    async def hangup(self, call_id: str) -> None:
        """Terminate the call leg AND tear down the local pipeline.

        Idempotent on the carrier side: a 404 / "not found" means the call
        already ended — success from our standpoint.

        Plivo's REST hangup doesn't reliably close our media-stream WebSocket,
        so the Pipecat runner would otherwise keep blocking (and the silence
        monitor keep firing the brain) after we've hung up. We therefore also
        cancel the registered pipeline task for this call so teardown is
        deterministic. (ACS gets this via its CallDisconnected webhook instead.)
        """
        if not call_id:
            return
        try:
            self._client.calls.hangup(call_id)
            log.info("[plivo] hangup ok call_uuid=%s", call_id)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "404" in msg or "not found" in msg.lower():
                log.info("[plivo] hangup no-op (call already ended) call_uuid=%s", call_id)
            else:
                log.warning("[plivo] hangup failed call_uuid=%s err=%s", call_id, msg[:200])

        # Stop the local Pipecat pipeline for this call. Lazy import so the
        # provider module doesn't create a provider<->routes import cycle.
        try:
            from agent_backend.channels.voice.routes import cancel_live_pipeline

            await cancel_live_pipeline(call_id)
        except Exception as e:  # noqa: BLE001
            log.warning("[plivo] pipeline cancel failed call_uuid=%s err=%s", call_id, str(e)[:200])


def build_provider() -> TelephonyProvider:
    s = get_settings()
    return PlivoVoiceProvider(
        auth_id=s.plivo_auth_id,
        auth_token=s.plivo_auth_token,
        from_number=s.plivo_from_number,
    )


def _extract(resp: Any, key: str) -> Any:
    """Plivo's create() may return an SDK object or a dict depending on version."""
    if resp is None:
        return None
    if isinstance(resp, dict):
        return resp.get(key)
    return getattr(resp, key, None)
