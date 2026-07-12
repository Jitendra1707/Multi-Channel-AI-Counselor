"""Shared Pipecat service factories.

This package no longer hosts a channel of its own — it survives only as the
home of the provider-agnostic STT / TTS / VAD / text-normalizer factories under
`channels.pipecat.services`, which the live spoken channels (voice, avatar_video)
import to build their pipelines. See `services/__init__.py` for the
`make_stt()` / `make_tts()` entry points.
"""
