"""ElevenLabs TTS service factory (Pipecat 0.0.89 — stock, no patch).

LLmLayer originally shipped a `_PatchedElevenLabsTTSService` subclass
that injected `xi-api-key` into the WS handshake because Pipecat
0.0.67 didn't. Pipecat 0.0.89's upstream now passes that header
natively, so the patch has been removed — AgentBackend uses the stock
service directly.

Voice tuning is env-overridable via ELEVENLABS_STABILITY,
ELEVENLABS_SIMILARITY_BOOST, ELEVENLABS_STYLE,
ELEVENLABS_USE_SPEAKER_BOOST. None values mean "use ElevenLabs's
per-voice defaults" — which are themselves tuned per voice id and
are usually right.

Sample rate is pinned to match the rest of the pipeline.
"""

from __future__ import annotations

from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.utils.text.base_text_filter import BaseTextFilter

from agent_backend.config import get_settings


def make_elevenlabs_tts(
    *,
    text_filter: BaseTextFilter | None = None,
    text_filters: list[BaseTextFilter] | None = None,
) -> ElevenLabsTTSService:
    s = get_settings()

    # InputParams with None entries left in place. None = "use the
    # vendor default for this voice", which is the right choice for
    # most voices — they're pre-tuned at ElevenLabs.
    params = ElevenLabsTTSService.InputParams(
        stability=s.elevenlabs_stability,
        similarity_boost=s.elevenlabs_similarity_boost,
        style=s.elevenlabs_style,
        use_speaker_boost=s.elevenlabs_use_speaker_boost,
    )

    kwargs: dict = {}
    if text_filters:
        kwargs["text_filters"] = text_filters
    elif text_filter is not None:
        kwargs["text_filter"] = text_filter

    return ElevenLabsTTSService(
        api_key=s.elevenlabs_api_key,
        voice_id=s.elevenlabs_voice_id,
        model=s.elevenlabs_model,
        sample_rate=s.pipecat_audio_sample_rate,
        params=params,
        **kwargs,
    )
