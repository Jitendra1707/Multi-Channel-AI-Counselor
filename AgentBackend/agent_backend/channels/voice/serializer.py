"""ACS Call Automation FrameSerializer for Pipecat.

ACS has no first-party Pipecat transport — but Pipecat's transport layer is
generic over a `FrameSerializer`, so we get full Pipecat integration by
implementing the ACS-specific JSON envelope here. The pattern mirrors
Pipecat's built-in `TwilioFrameSerializer` / `PlivoFrameSerializer`.

Envelope formats (per Microsoft docs):

  carrier → bot  (lowercase, what ACS sends):
      {"kind": "AudioMetadata", "audioMetadata": {...}}                     # one-shot at start
      {"kind": "AudioData",     "audioData":     {"data": "<base64 PCM>"}}  # per 20 ms chunk

  bot → carrier  (PascalCase, what ACS expects from us):
      {"Kind": "AudioData", "AudioData": {"Data": "<base64 PCM>"}, "StopAudio": null}
      {"Kind": "StopAudio", "AudioData": null, "StopAudio": {}}             # barge-in: drop queued audio

Format: PCM 16 kHz / 16-bit / mono. We pin the pipeline to this rate so the
serializer does NO resampling — bytes pass straight through (unlike Twilio,
which is 8 kHz μ-law and needs conversion).
"""
from __future__ import annotations

import base64
import json

from pipecat.frames.frames import (
    AudioRawFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
    StartFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer, FrameSerializerType

# ACS bidirectional streaming is PCM 16 kHz / 16-bit / mono. Pinned end-to-end.
_ACS_SAMPLE_RATE = 16000


class ACSFrameSerializer(FrameSerializer):
    """Translate Pipecat frames ↔ ACS Call Automation media-stream envelopes.

    Wire format is JSON over WebSocket TEXT frames in BOTH directions. ACS
    sends camelCase keys to us; we MUST send PascalCase back — that asymmetry
    is documented in Microsoft's Python sample for callautomation-az-openai-voice.
    """

    @property
    def type(self) -> FrameSerializerType:
        # ACS uses JSON-text frames on the WebSocket (not binary).
        return FrameSerializerType.TEXT

    async def setup(self, frame: StartFrame) -> None:
        """Called once when the pipeline starts. We have nothing to compute
        upfront because the sample rate is pinned at 16 kHz everywhere."""
        # Optionally we could pick up frame.audio_in_sample_rate etc., but
        # the pipeline is configured to 16 kHz already and ACS only speaks
        # 16 kHz on this stream type. Keep it strict.
        return None

    # ------------------------------------------------------------------
    # bot → carrier (out)
    # ------------------------------------------------------------------
    async def serialize(self, frame: Frame) -> str | bytes | None:
        """Convert a Pipecat frame into an ACS JSON envelope (or None to drop)."""
        # Barge-in: tell ACS to drop everything it has queued. This is how we
        # cut the bot off mid-sentence when the candidate starts talking.
        if isinstance(frame, InterruptionFrame):
            return json.dumps(
                {"Kind": "StopAudio", "AudioData": None, "StopAudio": {}}
            )

        # End-of-call lifecycle frames — let the WS handler hang up the
        # ACS call via the provider's REST API; nothing to serialise here.
        if isinstance(frame, (EndFrame, CancelFrame)):
            return None

        # Bot audio out. Pipecat emits OutputAudioRawFrame inheriting from
        # AudioRawFrame; both have `.audio` bytes. We pin sample rate so
        # there's no resampling on the hot path.
        if isinstance(frame, AudioRawFrame):
            data = frame.audio
            if not data:
                return None
            return json.dumps(
                {
                    "Kind": "AudioData",
                    "AudioData": {"Data": base64.b64encode(data).decode("ascii")},
                    "StopAudio": None,
                }
            )

        # Passthrough for arbitrary out-of-band messages (DTMF, control).
        if isinstance(frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)):
            try:
                return json.dumps(frame.message)
            except Exception:
                return None

        # Anything else: ignore.
        return None

    # ------------------------------------------------------------------
    # carrier → bot (in)
    # ------------------------------------------------------------------
    async def deserialize(self, data: str | bytes) -> Frame | None:
        """Convert an ACS JSON envelope into a Pipecat frame (or None to drop)."""
        try:
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            env = json.loads(data)
        except Exception:
            return None

        kind = env.get("kind")
        if kind == "AudioData":
            b64 = (env.get("audioData") or {}).get("data")
            if not b64:
                return None
            try:
                pcm = base64.b64decode(b64)
            except Exception:
                return None
            if not pcm:
                return None
            return InputAudioRawFrame(
                audio=pcm,
                sample_rate=_ACS_SAMPLE_RATE,
                num_channels=1,
            )

        # AudioMetadata fires once at stream start. We could surface the
        # subscriptionId here if downstream needs it; for now log+drop.
        # DTMF / other events: ignore at V1 — add when we wire DTMF flows.
        return None
