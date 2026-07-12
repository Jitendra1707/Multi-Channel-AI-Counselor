"""STT-only meeting transcriber (no agent, no brain, no TTS).

A subscribe-only LiveKit participant that joins a room, transcribes EVERY
participant's audio track via Deepgram, tags each line by the speaker (the
track's participant identity — diarization for free, no model), and writes ONE
transcript file when the meeting ends.

Completely independent of the AI agent and AegisBackend: works for 2, 3, or N
humans, with or without an agent in the room. Lives entirely in the live-kit
service.

    manager.py     — start/stop a transcriber per room; the process registry
    participant.py — joins the room (rtc.Room), one STT task per audio track
    stt_deepgram.py— PCM AudioFrames → Deepgram live socket → final text segments
    transcript.py  — accumulate (ts, speaker, text); write one file on finalize
"""

from livekit_svc.transcriber.manager import get_transcriber_manager

__all__ = ["get_transcriber_manager"]
