"""Azure Speech TTS factory — Pipecat 0.0.89.

Wraps Pipecat's built-in `AzureTTSService` — the streaming WebSocket
variant of Azure Cognitive Services TTS. `AzureHttpTTSService` (the
batch HTTP variant in the same module) is intentionally NOT used:
voice agents need first-byte latency in the 200-400ms range, which
the WS variant gives and the HTTP variant doesn't.

Credentials are shared with STT — a single Azure Speech resource
handles both. Reusing AZURE_SPEECH_KEY / AZURE_SPEECH_REGION avoids
having to provision a separate resource and keeps billing consolidated.

Voice selection notes:
  - The voice id encodes the language: `en-US-AriaNeural` is a US
    English voice; `en-IN-NeerjaNeural` is Indian English. Voice id
    and AZURE_TTS_LANGUAGE must agree, or Azure errors out at synthesis
    time, not at boot.
  - Styles (cheerful, chat, newscast-casual, etc.) are voice-specific.
    A style set on a voice that doesn't support it is silently ignored.
    Verify in the Speech Studio voice gallery before depending on one.

Empty-string config fields (pitch, style, style_degree) → pass None to
Pipecat so it uses the SSML default. Pydantic-settings can't store
None directly from .env, so we use empty string as the sentinel.
"""

from __future__ import annotations

from pipecat.services.azure.tts import AzureTTSService
from pipecat.transcriptions.language import Language
from pipecat.utils.text.base_text_filter import BaseTextFilter

from agent_backend.config import get_settings


def make_azure_tts(
    *,
    text_filter: BaseTextFilter | None = None,
    text_filters: list[BaseTextFilter] | None = None,
) -> AzureTTSService:
    s = get_settings()
    # Language enum values are BCP-47 codes; raises ValueError on
    # unknown codes (fail fast at boot vs first synthesis).
    language = Language(s.azure_tts_language)

    # Empty-string sentinels → None. Pipecat's InputParams treats None
    # as "use the voice/SSML default", which is the right behavior
    # for unset knobs.
    params = AzureTTSService.InputParams(
        language=language,
        rate=s.azure_tts_rate or None,
        pitch=s.azure_tts_pitch or None,
        style=s.azure_tts_style or None,
        style_degree=s.azure_tts_style_degree or None,
    )

    kwargs: dict = {}
    # text_filters (a list, run in order) is preferred; text_filter (single) is
    # kept for backward compatibility. Filters strip markdown/symbols and
    # normalise numbers/abbreviations to their spoken form before synthesis.
    if text_filters:
        kwargs["text_filters"] = text_filters
    elif text_filter is not None:
        kwargs["text_filter"] = text_filter

    return AzureTTSService(
        api_key=s.azure_speech_key,
        region=s.azure_speech_region,
        voice=s.azure_tts_voice,
        sample_rate=s.pipecat_audio_sample_rate,
        params=params,
        **kwargs,
    )
