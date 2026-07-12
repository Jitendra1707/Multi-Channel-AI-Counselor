"""Azure Communication Services — Call Automation provider.

Two SDK surfaces in use:
  - CallAutomationClient.create_call(...)    → outbound dial
  - MediaStreamingOptions(...)               → tells ACS to open a WS to us,
                                                streaming 16 kHz mono PCM both ways.

Install:  pip install azure-communication-callautomation>=1.3

Required env (see agent_backend/config.py):
  ACS_CONNECTION_STRING   — from the ACS resource (Primary connection string)
  ACS_FROM_NUMBER         — the E.164 ACS number that places the call (must be
                            purchased + linked to the resource)
  PUBLIC_BASE_URL         — public HTTPS base (e.g. ngrok / Azure App Service URL)
                            that ACS can reach. The provider builds callback +
                            media-streaming URLs by appending to this.

Local dev: front the FastAPI with `ngrok http 8000` and set:
  PUBLIC_BASE_URL=https://<random>.ngrok-free.app
The media-stream WS URL is derived as `wss://...` of the same host.
"""
from __future__ import annotations

from agent_backend.channels.voice.providers._common import BaseProvider
from agent_backend.channels.voice.providers.audio import AudioFormat
from agent_backend.channels.voice.providers.base import (
    AnswerRequest,
    CallResult,
    DialRequest,
    DialResult,  # alias kept for legacy imports
    ProviderCapabilities,
    TelephonyProvider,
    VoiceProvider,  # alias of TelephonyProvider
)
from agent_backend.config import get_settings
from agent_backend.infra import get_logger

log = get_logger(__name__)


# Capabilities ACS supports today. Methods not flagged below stay as
# inherited UnsupportedCapability stubs from BaseProvider — they're filled
# in PR 3 (answer, transfer warm, DTMF, recording, AMD).
_ACS_CAPABILITIES = ProviderCapabilities(
    name="acs",
    supports_outbound=True,
    supports_inbound=True,          # PR 2: answer() implemented below
    supports_streaming=True,
    supports_transfer_cold=False,   # PR 7
    supports_transfer_warm=False,   # PR 7
    supports_dtmf_receive=False,    # PR 8
    supports_dtmf_send=False,       # PR 8
    supports_recording=False,
    supports_amd=False,
    supports_play_audio_url=False,
    audio_format=AudioFormat.PCM16K_MONO,
)


class ACSVoiceProvider(BaseProvider):
    name = "acs"
    capabilities = _ACS_CAPABILITIES

    def __init__(self, *, connection_string: str, from_number: str) -> None:
        super().__init__()
        # Late import so the rest of the app loads even if the SDK isn't installed.
        from azure.communication.callautomation import CallAutomationClient

        if not connection_string:
            raise RuntimeError("ACS_CONNECTION_STRING is empty — set it in .env")
        if not from_number:
            raise RuntimeError("ACS_FROM_NUMBER is empty — set it in .env")

        self._client = CallAutomationClient.from_connection_string(connection_string)
        self._from = from_number

    def _build_media_streaming(self, media_ws_url: str):
        """Construct ACS MediaStreamingOptions — shared by dial + answer.

        Both outbound and inbound calls want identical streaming behaviour
        (bidirectional PCM 16K mono on the same WebSocket). Factored out
        so the two SDK call sites agree end-to-end without drift.
        """
        # azure-communication-callautomation 1.5.0 renamed
        # `MediaStreamingTransportType` → `StreamingTransportType`; other Media*
        # names are unchanged.
        from azure.communication.callautomation import (
            AudioFormat as ACSAudioFormat,
            MediaStreamingAudioChannelType,
            MediaStreamingContentType,
            MediaStreamingOptions,
            StreamingTransportType,
        )

        # `enable_bidirectional=True` is the load-bearing flag — without it ACS
        # only PUSHES audio to us (one-way); our outbound packets get silently
        # dropped. With it on, the same WebSocket carries both directions, and
        # the bot's TTS audio is actually played to the candidate.
        #
        # `audio_format=PCM_16K_MONO` matches what Deepgram ingests (PCM 16k 16-bit
        # mono) and what ElevenLabs returns when we ask for `output_format=pcm_16000`
        # — no resampling needed end-to-end.
        #
        # `MIXED` audio channel = one mixed stream of all participants. For a 1:1
        # call (candidate + bot) MIXED is the right choice; UNMIXED is for
        # multi-party calls where we'd want per-speaker streams.
        return MediaStreamingOptions(
            transport_url=media_ws_url,
            transport_type=StreamingTransportType.WEBSOCKET,
            content_type=MediaStreamingContentType.AUDIO,
            audio_channel_type=MediaStreamingAudioChannelType.MIXED,
            start_media_streaming=True,
            enable_bidirectional=True,
            audio_format=ACSAudioFormat.PCM16_K_MONO,
        )

    async def dial(self, req: DialRequest) -> CallResult:
        from azure.communication.callautomation import PhoneNumberIdentifier

        media = self._build_media_streaming(req.media_ws_url)

        # create_call returns a CallConnectionProperties wrapper synchronously
        # in the v1.3 SDK; if you upgrade and it becomes awaitable, just `await` it.
        result = self._client.create_call(
            target_participant=PhoneNumberIdentifier(req.to_e164),
            source_caller_id_number=PhoneNumberIdentifier(self._from),
            callback_url=req.callback_url,
            media_streaming=media,
            operation_context=_pack_correlation(req.correlation),
        )

        call_id = getattr(result, "call_connection_id", None) or getattr(
            result, "call_connection_properties", None
        )
        log.info(
            "[acs] dial initiated",
            to=req.to_e164,
            call_id=call_id,
            lead_id=req.correlation.get("lead_id"),
        )
        return CallResult(call_id=str(call_id), status="queued", raw={"sdk": "acs"})

    async def answer(self, req: AnswerRequest) -> CallResult:
        """Accept an INBOUND call.

        ACS delivers IncomingCall events via Event Grid (NOT via the Call
        Automation callback). The handler that receives those events extracts
        `incomingCallContext` from the payload and passes it on the
        AnswerRequest. We hand it back to `CallAutomationClient.answer_call`
        along with the same MediaStreamingOptions we use for outbound — the
        candidate's leg is identical from the brain's perspective once
        bridged.

        Returns the new `call_connection_id` so the active-calls registry can
        track it like an outbound dial.
        """
        media = self._build_media_streaming(req.media_ws_url)

        result = self._client.answer_call(
            incoming_call_context=req.incoming_call_context,
            callback_url=req.callback_url,
            media_streaming=media,
            operation_context=_pack_correlation(req.correlation),
        )

        call_id = getattr(result, "call_connection_id", None) or getattr(
            result, "call_connection_properties", None
        )
        log.info(
            "[acs] inbound answered",
            call_id=call_id,
            lead_id=req.correlation.get("lead_id"),
        )
        return CallResult(call_id=str(call_id), status="in_progress", raw={"sdk": "acs"})

    async def hangup(self, call_id: str) -> None:
        """Tell ACS to terminate the carrier leg of this call.

        Idempotent: if ACS returns 404 "Call not found" (code 8522) the call
        has already ended on the carrier side — that's a SUCCESS from the
        caller's perspective ("the call is no longer up"), not a failure.
        We log it at info level and return cleanly so the caller doesn't
        retry forever.
        """
        try:
            self._client.get_call_connection(call_id).hang_up(is_for_everyone=True)
            log.info("[acs] hangup ok call_id=%s", call_id)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            # ACS error code 8522 = call not found = already terminated.
            already_gone = "8522" in msg or "Call not found" in msg or "(404)" in msg
            if already_gone:
                log.info(
                    "[acs] hangup no-op (call already terminated) call_id=%s",
                    call_id,
                )
                return
            # Other errors are real and worth a warning.
            log.warning("[acs] hangup failed call_id=%s err=%s", call_id, msg[:200])

    # ------------------------------------------------------------------
    # Streaming serializer — the Pipecat composer asks for this so it
    # doesn't have to know it's ACS. PR 4 of the migration moves the
    # current `channels/voice/serializer.py` under `_serializers/acs.py`;
    # until that lands we import the existing one from its current path.
    # ------------------------------------------------------------------
    def frame_serializer(self):  # -> pipecat FrameSerializer
        # Late import keeps the SDK out of base.py's import surface.
        from agent_backend.channels.voice.serializer import ACSFrameSerializer
        return ACSFrameSerializer()


def build_provider() -> TelephonyProvider:
    s = get_settings()
    return ACSVoiceProvider(
        connection_string=s.acs_connection_string,
        from_number=s.acs_from_number,
    )


def _pack_correlation(corr: dict[str, str]) -> str:
    """ACS lets you stash a short opaque string on a call (`operation_context`)
    that's echoed back in every callback. We pack our lead_id into it so the
    callback handler can recover identity without a side-channel."""
    return "|".join(f"{k}={v}" for k, v in corr.items())[:255]
