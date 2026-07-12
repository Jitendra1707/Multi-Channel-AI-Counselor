"""Pipecat-channel service factories — STT, TTS, VAD.

Each provider module exposes a single `make_*()` function that returns
a ready-to-wire Pipecat service. Keeping them as factories (not module-
level globals) means the config is re-read at construction time, which
matters for hot-reload during dev.

`make_stt()` and `make_tts()` are the provider-agnostic entry points —
they dispatch to the right factory based on `voice_stt_provider` /
`voice_tts_provider`. Adding a new backend = drop a new
`stt_<provider>.py` or `tts_<provider>.py` module + register one
branch in the matching dispatcher. The pipeline never needs to change.
"""

from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService

from agent_backend.channels.pipecat.services.stt_azure import make_azure_stt
from agent_backend.channels.pipecat.services.stt_deepgram import make_deepgram_stt
from agent_backend.channels.pipecat.services.tts_azure import make_azure_tts
from agent_backend.channels.pipecat.services.tts_elevenlabs import make_elevenlabs_tts
from agent_backend.channels.pipecat.services.tts_sarvam import make_sarvam_tts
from agent_backend.channels.pipecat.services.vad_silero import make_silero_vad
from agent_backend.config import get_settings
from agent_backend.infra import get_logger

log = get_logger(__name__)


def make_stt(*, segmentation_silence_ms: int | None = None) -> STTService:
    """Return the STT service selected by `VOICE_STT_PROVIDER`.

    segmentation_silence_ms: optional end-of-phrase silence override (Azure only;
        ignored by Deepgram). The meeting channel passes a longer value than the
        phone default so far-field conversational speech is finalised on full
        phrases, not chopped fragments.
    """
    provider = get_settings().voice_stt_provider
    log.info("[services] building STT", provider=provider, seg_ms=segmentation_silence_ms)
    match provider:
        case "deepgram":
            return make_deepgram_stt()
        case "azure":
            return make_azure_stt(segmentation_silence_ms=segmentation_silence_ms)
        case _:
            raise ValueError(
                f"Unknown VOICE_STT_PROVIDER={provider!r}. Valid: deepgram | azure."
            )


def make_tts(*, strip_markdown: bool = False) -> TTSService:
    """Return the TTS service selected by `VOICE_TTS_PROVIDER`.

    strip_markdown: when True, attaches a MarkdownTextFilter so any markdown /
        symbols the LLM emits (**bold**, *, #, lists, links, code) are stripped
        BEFORE synthesis — the TTS never reads symbols aloud. Spoken channels
        (avatar video, voice) should set this True as a safety net behind the
        prompt's "no markdown" rule. Default False = no change for callers that
        don't ask for it.
    """
    provider = get_settings().voice_tts_provider

    # Text filters run in order on the fully-aggregated sentence before synthesis.
    # MarkdownTextFilter (optional) strips symbols first; SpokenTextNormalizer
    # ALWAYS runs last so every spoken channel — including voice, which had no
    # filter before — pronounces 'B.Tech', 'AI/ML', '8.9', '6-15' naturally.
    from agent_backend.channels.pipecat.services.text_normalizer import SpokenTextNormalizer

    filters: list = []
    if strip_markdown:
        from pipecat.utils.text.markdown_text_filter import MarkdownTextFilter

        filters.append(
            MarkdownTextFilter(
                MarkdownTextFilter.InputParams(
                    enable_text_filter=True,
                    filter_code=True,    # never read code fences aloud
                    filter_tables=True,  # never read table pipes aloud
                )
            )
        )
    filters.append(SpokenTextNormalizer())

    log.info(
        "[services] building TTS",
        provider=provider,
        strip_markdown=strip_markdown,
        filters=[type(f).__name__ for f in filters],
    )

    match provider:
        case "elevenlabs":
            return make_elevenlabs_tts(text_filters=filters)
        case "azure":
            return make_azure_tts(text_filters=filters)
        case "sarvam":
            return make_sarvam_tts(text_filters=filters)
        case _:
            raise ValueError(
                f"Unknown VOICE_TTS_PROVIDER={provider!r}. Valid: elevenlabs | azure | sarvam."
            )


__all__ = [
    "make_azure_stt",
    "make_azure_tts",
    "make_deepgram_stt",
    "make_elevenlabs_tts",
    "make_sarvam_tts",
    "make_silero_vad",
    "make_stt",
    "make_tts",
]
