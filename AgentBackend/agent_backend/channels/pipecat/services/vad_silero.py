"""Silero VAD analyzer factory.

Tuning rationale (validated in LLmLayer through several rounds):
  - start_secs=0.10  fast onset → quick interrupt detection
  - stop_secs=0.80   silence to declare end-of-turn. Pulled back from
                     library default 1.20 (slow) but NOT to 0.60 which
                     was too aggressive and chopped mid-sentence pauses.
  - confidence=0.65  Silero output threshold. 0.55 was too sensitive
                     (let breathing/typing through and Deepgram
                     hallucinated phrases). 0.65 is the balanced spot.
  - min_volume=0.60  RMS floor. Library default — safe for noisy rooms.

Each value is env-overridable via VOICE_VAD_* (see config.py).
"""

from __future__ import annotations

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams

from agent_backend.config import get_settings


def make_silero_vad() -> SileroVADAnalyzer:
    s = get_settings()
    params = VADParams(
        start_secs=s.voice_vad_start_secs,
        stop_secs=s.voice_vad_stop_secs,
        confidence=s.voice_vad_confidence,
        min_volume=s.voice_vad_min_volume,
    )
    return SileroVADAnalyzer(params=params)
