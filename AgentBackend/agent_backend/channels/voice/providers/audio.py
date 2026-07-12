"""Audio-format glue for the telephony abstraction.

Why this exists
---------------
Every telephony provider streams audio in a slightly different shape:
  - ACS:        PCM 16 kHz / 16-bit / mono   (linear PCM, little-endian)
  - Twilio:     μ-law 8 kHz / 8-bit / mono   (Media Streams, base64-framed)
  - Exotel:     PCM 8 kHz / 16-bit / mono    (Voicebot Streaming API)
  - Knowlarity: PCM 8 kHz / 16-bit / mono    (KOOKOO Stream)

The brain (run_stream → TTS → audio) and Pipecat's internals always work in
one canonical format: PCM 16 kHz / 16-bit / mono. So every serializer is
just a thin pump that converts to/from canonical on the way in/out of the
provider's WS.

The conversions are mathematically identical across providers (μ-law tables,
linear PCM downsample/upsample), so they live here — each serializer calls
`to_canonical()` on inbound bytes and `from_canonical()` on outbound bytes.

Why stdlib `audioop`
--------------------
- C-implemented (~50 µs per 20 ms frame — negligible in the audio path)
- Zero new dependencies
- Same code path Pipecat / Twilio's own Python samples / LiveKit use
- Acceptable quality: telephony audio is already band-limited to ≤4 kHz
  for 8K and ≤8 kHz for 16K — `ratecv`'s linear interpolation is fine

Deprecation note
----------------
`audioop` is marked deprecated in Python 3.13 and is scheduled for removal
in 3.14. Migration target: `pydub` (pure-Python; wraps the same algorithms)
or `samplerate` (libsamplerate bindings — better quality). Both swap in
behind this module without any caller changes.

Statefulness note
-----------------
`to_canonical()` / `from_canonical()` are stateless: they take in bytes,
return bytes. For STREAMING audio you want state carried across calls so
`audioop.ratecv` doesn't drop ~1 sample per frame at the chunk boundary.
The serializer modules instantiate `Resampler` (below) once per stream
direction and feed frames through it; the stateless helpers are kept for
one-off conversions (tests, single-buffer transforms) where the boundary
loss is irrelevant.
"""
from __future__ import annotations

import audioop
from dataclasses import dataclass
from enum import StrEnum

# ---------------------------------------------------------------------------
# Canonical format — what the pipeline speaks internally.
# Match Pipecat default + Deepgram + Azure STT/TTS sweet spot.
# ---------------------------------------------------------------------------
CANONICAL_SAMPLE_RATE = 16_000
CANONICAL_SAMPLE_WIDTH_BYTES = 2  # 16-bit linear PCM
CANONICAL_CHANNELS = 1            # mono


class AudioFormat(StrEnum):
    """Wire formats the providers stream. Names mirror the on-the-wire encoding."""

    PCM16K_MONO  = "pcm16k_mono"      # ACS — canonical
    PCM8K_MONO   = "pcm8k_mono"       # Exotel, Knowlarity
    MULAW8K_MONO = "mulaw8k_mono"     # Twilio Media Streams


@dataclass(frozen=True)
class AudioSpec:
    """Numeric description of one of the AudioFormat values.

    Carried on `ProviderCapabilities.audio_format_spec` so the pipeline
    composer can size its buffers + report the format in /health without
    hard-coding numbers per provider.
    """

    sample_rate_hz: int
    sample_width_bytes: int  # 1 = μ-law (8-bit), 2 = PCM (16-bit)
    channels: int
    is_mulaw: bool = False


_SPECS: dict[AudioFormat, AudioSpec] = {
    AudioFormat.PCM16K_MONO:  AudioSpec(16_000, 2, 1, is_mulaw=False),
    AudioFormat.PCM8K_MONO:   AudioSpec(8_000,  2, 1, is_mulaw=False),
    AudioFormat.MULAW8K_MONO: AudioSpec(8_000,  1, 1, is_mulaw=True),
}


def spec_for(fmt: AudioFormat) -> AudioSpec:
    return _SPECS[fmt]


# ---------------------------------------------------------------------------
# Conversions to/from the canonical pipeline format.
# Each serializer calls exactly one direction per inbound/outbound frame.
# ---------------------------------------------------------------------------
def to_canonical(payload: bytes, src: AudioFormat) -> bytes:
    """Convert provider-native bytes → canonical PCM 16K / 16-bit / mono."""
    if src is AudioFormat.PCM16K_MONO:
        return payload
    if src is AudioFormat.PCM8K_MONO:
        # 8K PCM → 16K PCM (upsample 2x). `ratecv` keeps no state across calls
        # here because frames are independent — for streaming with continuity
        # you'd carry the `state` tuple; for telephony 20 ms frames the
        # boundary artefacts are inaudible.
        out, _ = audioop.ratecv(payload, 2, 1, 8_000, 16_000, None)
        return out
    if src is AudioFormat.MULAW8K_MONO:
        # μ-law 8K → linear PCM 8K → linear PCM 16K (two-step).
        pcm8k = audioop.ulaw2lin(payload, 2)
        out, _ = audioop.ratecv(pcm8k, 2, 1, 8_000, 16_000, None)
        return out
    raise ValueError(f"Unknown source format: {src!r}")


def from_canonical(payload: bytes, dst: AudioFormat) -> bytes:
    """Convert canonical PCM 16K bytes → provider-native bytes for outbound."""
    if dst is AudioFormat.PCM16K_MONO:
        return payload
    if dst is AudioFormat.PCM8K_MONO:
        out, _ = audioop.ratecv(payload, 2, 1, 16_000, 8_000, None)
        return out
    if dst is AudioFormat.MULAW8K_MONO:
        # PCM 16K → PCM 8K → μ-law.
        pcm8k, _ = audioop.ratecv(payload, 2, 1, 16_000, 8_000, None)
        return audioop.lin2ulaw(pcm8k, 2)
    raise ValueError(f"Unknown destination format: {dst!r}")


# ---------------------------------------------------------------------------
# Frame-size helper — provider serializers use this to slice canonical audio
# into the byte-count their carrier expects per WS message.
# ---------------------------------------------------------------------------
def bytes_per_frame_ms(spec: AudioSpec, frame_ms: int) -> int:
    """How many wire-bytes encode `frame_ms` milliseconds of audio."""
    samples_per_frame = (spec.sample_rate_hz * frame_ms) // 1000
    return samples_per_frame * spec.sample_width_bytes * spec.channels


class Resampler:
    """Stateful per-stream resampler.

    Carries `audioop.ratecv`'s state tuple across calls so boundary samples
    aren't lost when audio is fed in 20 ms chunks (the telephony norm).
    One instance per stream direction (one for inbound, one for outbound).

    Direction-agnostic: configure `src` and `dst`; call `.convert(payload)`
    repeatedly. Internally splits the work into the same steps the stateless
    helpers do (μ-law ↔ PCM via lookup table, then resample).
    """

    def __init__(self, src: AudioFormat, dst: AudioFormat) -> None:
        self._src = src
        self._dst = dst
        self._ratecv_state: tuple | None = None  # carried across calls

    def convert(self, payload: bytes) -> bytes:
        if self._src is self._dst:
            return payload

        # Step 1: get to linear PCM at the source rate.
        if self._src is AudioFormat.MULAW8K_MONO:
            pcm = audioop.ulaw2lin(payload, 2)
            src_rate = 8_000
        else:
            pcm = payload
            src_rate = _SPECS[self._src].sample_rate_hz

        # Step 2: resample to the destination rate (skip if already there).
        dst_rate = _SPECS[self._dst].sample_rate_hz
        if src_rate != dst_rate:
            pcm, self._ratecv_state = audioop.ratecv(
                pcm, 2, 1, src_rate, dst_rate, self._ratecv_state
            )

        # Step 3: encode to μ-law if that's the destination.
        if self._dst is AudioFormat.MULAW8K_MONO:
            return audioop.lin2ulaw(pcm, 2)
        return pcm


__all__ = [
    "AudioFormat",
    "AudioSpec",
    "Resampler",
    "CANONICAL_SAMPLE_RATE",
    "CANONICAL_SAMPLE_WIDTH_BYTES",
    "CANONICAL_CHANNELS",
    "spec_for",
    "to_canonical",
    "from_canonical",
    "bytes_per_frame_ms",
]
