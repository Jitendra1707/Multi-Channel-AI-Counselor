"""Sarvam AI TTS service factory (Pipecat 0.0.89 — stock, no patch).

Uses Pipecat's STREAMING `SarvamTTSService` (WebSocket: wss://api.sarvam.ai/
text-to-speech/ws), not the one-shot HTTP variant — so audio frames start
flowing as soon as the first chunk is synthesised, exactly like the ElevenLabs /
Azure streaming paths. This keeps time-to-first-audio (the mouth-to-ear latency
the rest of the pipeline is tuned around) on par with the other providers.

Sarvam's `bulbul` models are tuned for Indian languages/accents (en-IN, hi-IN,
ta-IN, te-IN, …). Output is requested as `linear16` (PCM 16-bit) at the pinned
pipeline sample rate, so audio passes straight through to the ACS transport with
NO resample on the hot path — same as the other TTS providers.

Voice tuning is env-overridable via SARVAM_TTS_* (speaker, model, language,
pace, pitch, loudness). pitch/loudness apply to bulbul:v2 only (ignored by v3).
"""

from __future__ import annotations

from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.transcriptions.language import Language
from pipecat.utils.text.base_text_filter import BaseTextFilter

from agent_backend.config import get_settings


def _sarvam_language(code: str) -> Language:
    """Map a BCP-47 code (en-IN, hi-IN, …) to the pipecat `Language` member that
    pipecat 0.0.89's Sarvam service knows how to convert.

    IMPORTANT: pipecat's `language_to_sarvam_language` keys on the BASE language
    members — `Language.EN` → 'en-IN', `Language.HI` → 'hi-IN', etc. The REGIONAL
    members (`Language.EN_IN`) map to None, which makes the WS config send
    `target_language_code: null` → Sarvam rejects it with "Input parameters has
    to be a valid dictionary". So normalise 'en-IN' → 'en' first. (Odia is the
    one mismatch: Sarvam 'od-IN' ↔ pipecat `Language.OR`.)
    """
    base = (code or "en").split("-")[0].strip().lower()
    if base == "od":
        base = "or"
    try:
        return Language(base)
    except ValueError:
        return Language.EN


def make_sarvam_tts(
    *,
    text_filter: BaseTextFilter | None = None,
    text_filters: list[BaseTextFilter] | None = None,
) -> SarvamTTSService:
    s = get_settings()

    # Language code is BCP-47 (en-IN, hi-IN, ta-IN, …). Normalise to the base
    # pipecat Language member the Sarvam service maps correctly (see helper) —
    # passing the regional member would send target_language_code=null and the
    # Sarvam WS would reject the whole config.
    language = _sarvam_language(s.sarvam_tts_language)

    # output_audio_codec=linear16 → PCM 16-bit, matching the pipeline's pinned
    # 16 kHz / 16-bit / mono so nothing resamples downstream. pitch/loudness are
    # bulbul:v2-only knobs (v3 ignores them); pace works on both.
    params = SarvamTTSService.InputParams(
        language=language,
        pace=s.sarvam_tts_pace,
        pitch=s.sarvam_tts_pitch,
        loudness=s.sarvam_tts_loudness,
        output_audio_codec="linear16",
    )

    kwargs: dict = {}
    # text_filters (a list, run in order) is preferred; text_filter (single) is
    # kept for backward compatibility — identical handling to the other
    # providers. Filters strip markdown/symbols and normalise numbers/
    # abbreviations to their spoken form before synthesis.
    if text_filters:
        kwargs["text_filters"] = text_filters
    elif text_filter is not None:
        kwargs["text_filter"] = text_filter

    return SarvamTTSService(
        api_key=s.sarvam_api_key,
        model=s.sarvam_tts_model,
        voice_id=s.sarvam_tts_speaker,
        sample_rate=s.pipecat_audio_sample_rate,
        params=params,
        **kwargs,
    )
