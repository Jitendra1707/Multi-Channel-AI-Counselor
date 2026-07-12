"""Azure Speech STT factory — Pipecat 0.0.89.

Wraps Pipecat's built-in `AzureSTTService` (Azure Cognitive Services
Speech SDK) with our config defaults. Continuous streaming recognition
— emits both `InterimTranscriptionFrame` and `TranscriptionFrame` from
the same WS-like push stream, so it slots into the same place in the
pipeline as Deepgram.

Why Azure for the local-team build:
  - We already use Azure (Blob for identity) so credentials and billing
    pool are already in place.
  - Streaming partials are ~150-300ms which keeps the voice loop snappy.
  - Neural voices on the TTS side are 10x cheaper than ElevenLabs at
    comparable quality — pairs naturally with switching TTS too.

Single-VAD discipline (same rule as Deepgram): the upstream Silero VAD
on the transport is the SINGLE source of truth for turn boundaries.
Azure's continuous recognizer has its own endpointing too — that's
fine, the two operate independently here; we use Azure's results but
Pipecat's `UserStoppedSpeakingFrame` (from Silero) still drives the
agent turn. If you ever wire AzureSTTService inside a
SegmentedSTTService wrapper, revisit this comment.

Language: env keeps a BCP-47 string (e.g. "en-US", "en-IN", "hi-IN")
which we resolve to Pipecat's `Language` enum via value lookup. Any
code listed in pipecat/transcriptions/language.py works.
"""

from __future__ import annotations

from pipecat.services.azure.stt import AzureSTTService
from pipecat.transcriptions.language import Language

from agent_backend.config import get_settings
from agent_backend.infra import get_logger

log = get_logger(__name__)


def make_azure_stt(*, segmentation_silence_ms: int | None = None) -> AzureSTTService:
    """Build the Azure STT service.

    segmentation_silence_ms: override Azure's end-of-phrase silence timeout for
        this instance. None → use the global `azure_stt_segmentation_silence_ms`
        (300ms, tuned for snappy PHONE turn-taking). A LONGER value (e.g. 800ms)
        is better for far-field / laptop-mic conversational audio (the meeting
        channel): Azure hears the WHOLE phrase before finalising, so its language
        model has enough context to pick the right words instead of guessing
        homophones on tiny fragments ("offers session" / "offer station").
    """
    s = get_settings()
    # Language enum values are BCP-47 codes (EN_US = "en-US"), so this
    # round-trips cleanly. Raises ValueError on unknown codes — fail
    # fast at boot rather than at the first transcript.
    language = Language(s.azure_speech_language)

    svc = AzureSTTService(
        api_key=s.azure_speech_key,
        region=s.azure_speech_region,
        language=language,
        sample_rate=s.pipecat_audio_sample_rate,
        # endpoint_id is only used for Azure Custom Speech (org-trained
        # acoustic/language models). Leave empty for the default model.
        endpoint_id=s.azure_speech_endpoint_id or None,
    )

    seg_ms = segmentation_silence_ms or s.azure_stt_segmentation_silence_ms

    # Azure's continuous recognizer waits this long after speech stops before
    # emitting the FINAL transcript. Too short (300ms) on a far-field mic chops
    # mid-phrase and the language model commits wrong words; longer lets it hear
    # the full phrase. We ALSO widen the initial-silence timeout so a brief pause
    # before speaking doesn't trigger a premature (often garbled) finalisation,
    # and force dictation mode for more natural full-sentence recognition.
    try:
        from azure.cognitiveservices.speech import PropertyId

        svc._speech_config.set_property(
            PropertyId.Speech_SegmentationSilenceTimeoutMs, str(seg_ms)
        )
        # Don't bail out / finalise on a short lead-in silence (default ~5s is
        # fine, but be explicit so a slow start isn't mis-segmented).
        svc._speech_config.set_property(
            PropertyId.SpeechServiceConnection_InitialSilenceTimeoutMs, "10000"
        )
        # Dictation mode: Azure recognises continuous, naturally-phrased speech
        # (full sentences) rather than short command-style utterances — markedly
        # better word accuracy on conversational meeting speech.
        try:
            svc._speech_config.enable_dictation()
        except Exception:  # noqa: BLE001
            pass
        log.info("[stt-azure] segmentation silence timeout set", ms=seg_ms)
    except Exception as e:  # noqa: BLE001
        # Non-fatal: if the SDK enum/name shifts, fall back to Azure's default
        # rather than failing STT construction.
        log.warning("[stt-azure] could not set segmentation timeout", err=str(e))

    return svc
