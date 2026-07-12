"""Azure Speech streaming STT for one audio track.

Wraps one Azure continuous SpeechRecognizer fed by a PushAudioInputStream. You
push raw PCM16 bytes frame-by-frame; Azure fires `recognized` with finalized
text, which we hand to `on_final(text, start_s, end_s)`. One instance per
participant track — each recognizer only ever hears one person, so the speaker
is unambiguous (diarization with no model).

Reuses the same Azure Speech resource (AZURE_SPEECH_KEY / REGION) the phone-call
channel uses — no new account/key. Azure's SDK callbacks run on its own native
thread, so we marshal results back onto the asyncio loop with
`run_coroutine_threadsafe`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import azure.cognitiveservices.speech as speechsdk

from livekit_svc.config import get_settings
from livekit_svc.logging import get_logger

log = get_logger(__name__)

# on_final(text, start_s, end_s)
FinalHandler = Callable[[str, float, float], Awaitable[None]]


class AzureTrackSTT:
    """One Azure continuous recognizer for one participant's audio track."""

    def __init__(
        self,
        *,
        sample_rate: int,
        num_channels: int,
        on_final: FinalHandler,
        speaker: str,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._sample_rate = sample_rate
        self._num_channels = num_channels
        self._on_final = on_final
        self._speaker = speaker
        self._loop = loop
        self._push: speechsdk.audio.PushAudioInputStream | None = None
        self._recognizer: speechsdk.SpeechRecognizer | None = None
        self._started = False

    async def start(self) -> None:
        s = get_settings()
        if not s.azure_speech_key:
            raise RuntimeError("AZURE_SPEECH_KEY is not set — transcriber needs it.")

        speech_config = speechsdk.SpeechConfig(
            subscription=s.azure_speech_key, region=s.azure_speech_region
        )
        speech_config.speech_recognition_language = s.azure_speech_language
        if s.azure_speech_endpoint_id:
            speech_config.endpoint_id = s.azure_speech_endpoint_id

        # PCM16 at the track's real rate/channels — no resample, no mismatch.
        fmt = speechsdk.audio.AudioStreamFormat(
            samples_per_second=self._sample_rate,
            bits_per_sample=16,
            channels=self._num_channels,
        )
        self._push = speechsdk.audio.PushAudioInputStream(stream_format=fmt)
        audio_config = speechsdk.audio.AudioConfig(stream=self._push)
        self._recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config, audio_config=audio_config
        )

        def _on_recognized(evt: speechsdk.SpeechRecognitionEventArgs) -> None:
            # Fires on Azure's native thread. Only commit final results with text.
            if evt.result.reason != speechsdk.ResultReason.RecognizedSpeech:
                return
            text = (evt.result.text or "").strip()
            if not text:
                return
            # Azure offsets/durations are in 100-ns ticks → seconds.
            start = float(getattr(evt.result, "offset", 0) or 0) / 1e7
            dur = float(getattr(evt.result, "duration", 0) or 0) / 1e7
            # Marshal the async handler back onto the event loop thread.
            try:
                asyncio.run_coroutine_threadsafe(
                    self._on_final(text, start, start + dur), self._loop
                )
            except Exception as e:  # noqa: BLE001
                log.debug("azure result marshal failed", speaker=self._speaker, err=str(e))

        def _on_canceled(evt: speechsdk.SpeechRecognitionCanceledEventArgs) -> None:
            log.warning(
                "azure recognizer canceled",
                speaker=self._speaker,
                reason=str(getattr(evt, "reason", "")),
                detail=str(getattr(evt, "error_details", "")),
            )

        self._recognizer.recognized.connect(_on_recognized)
        self._recognizer.canceled.connect(_on_canceled)
        # Continuous (not single-shot) — a meeting is many utterances.
        self._recognizer.start_continuous_recognition_async().get()
        self._started = True
        log.info("azure recognizer open", speaker=self._speaker, sr=self._sample_rate, ch=self._num_channels)

    async def send(self, pcm: bytes) -> None:
        """Feed one chunk of raw PCM16 audio."""
        if self._started and self._push is not None:
            # write() is non-blocking (buffers into the SDK); safe to call from async.
            self._push.write(pcm)

    async def finish(self) -> None:
        """Flush + stop the recognizer (call when the track ends / meeting closes).
        Idempotent — a second call is a no-op (avoids 'recognizer canceled' noise
        when both the track-task finally and an explicit stop() fire)."""
        if not self._started and self._recognizer is None and self._push is None:
            return
        if self._push is not None:
            try:
                self._push.close()
            except Exception:  # noqa: BLE001
                pass
        if self._recognizer is not None:
            try:
                # stop on a worker thread so we don't block the event loop.
                await asyncio.to_thread(
                    lambda: self._recognizer.stop_continuous_recognition_async().get()
                )
            except Exception as e:  # noqa: BLE001
                log.debug("azure stop error", speaker=self._speaker, err=str(e))
        self._started = False
        self._push = None
        self._recognizer = None


# Public alias so the participant imports a provider-neutral name.
TrackSTT = AzureTrackSTT

__all__ = ["AzureTrackSTT", "TrackSTT", "FinalHandler"]
