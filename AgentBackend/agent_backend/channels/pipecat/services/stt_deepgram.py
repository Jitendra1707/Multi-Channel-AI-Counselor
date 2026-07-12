"""Deepgram STT service factory — production-tuned (Pipecat 0.0.89).

The non-obvious choices, captured here so they don't regress:

  - vad_events=False
    Silero VAD on the transport is the SINGLE source of truth for
    turn boundaries. Enabling Deepgram's own VAD here creates a
    dual-VAD race: Pipecat sends `Finalize` on the upstream
    UserStoppedSpeakingFrame, AND Deepgram fires its own UtteranceEnd.
    The second one lands on a WS state that's already moved on →
    error "finalize failed" every utterance. Keep False.

  - endpointing=DEEPGRAM_ENDPOINTING_MS (default 300ms)
    How long Deepgram waits inside an utterance before marking a
    partial transcript `is_final=True`. Was 500ms in earlier builds;
    300ms is the latency sweet spot now that the upstream VAD floor
    (min_volume=0.60) already filters the noise that used to require
    the longer wait. Production Twilio voice agents use 300ms.

  - profanity_filter=False
    Enterprise meetings need verbatim transcripts. Filtering rewrites
    words as **** and breaks downstream recall.

  - linear16 + sample_rate=16000 + channels=1
    Pinned to match what Node's bridge.js sends. Mismatches silently
    distort the audio Deepgram processes, leading to bad transcripts.
"""

from __future__ import annotations

from deepgram import LiveOptions
from pipecat.services.deepgram.stt import DeepgramSTTService

from agent_backend.config import get_settings


def make_deepgram_stt() -> DeepgramSTTService:
    s = get_settings()

    live = LiveOptions(
        model=s.deepgram_model,
        language=s.deepgram_language,
        # Wire format — match Node's bridge.js output exactly.
        encoding="linear16",
        sample_rate=s.pipecat_audio_sample_rate,
        channels=1,
        # Streaming behaviour.
        interim_results=True,
        smart_format=True,
        punctuate=True,
        profanity_filter=False,
        # Single-VAD discipline (see module docstring).
        vad_events=False,
        # How long to wait for stability before committing a partial.
        # Config-driven (DEEPGRAM_ENDPOINTING_MS); 300ms is the latency
        # default. Bump only if you observe noise-derived false finals.
        endpointing=s.deepgram_endpointing_ms,
        # no_delay=True is for typing-style UIs; keep False so
        # noise-derived fragments don't get pushed downstream.
        no_delay=False,
    )

    return DeepgramSTTService(
        api_key=s.deepgram_api_key,
        live_options=live,
        sample_rate=s.pipecat_audio_sample_rate,
    )
