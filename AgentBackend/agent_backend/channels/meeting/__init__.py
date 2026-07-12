"""Meeting channel — a LiveKit counselling room with a listening agent.

A virtual counselling meeting hosted on LiveKit:

    Counsellor (human, browser) ─┐
                                 ├──▶  LiveKit room  ◀──  AgentBackend agent
    Candidate  (human, browser) ─┘                       (server-side participant,
                                                          Pipecat LiveKitTransport)

The agent LISTENS to the whole conversation, answers / suggests / guides ONLY
when it's addressed by name (the same addressee gate the Teams meeting bridge
uses), and on meeting end flushes the speaker-tagged transcript to the
BusinessLayer for DUAL analysis (candidate + counsellor).

This package mirrors `channels/avatar_video/` in shape:
    scheduler.py — create room + mint candidate/counsellor JWTs (livekit-api)
    routes.py    — HTTP surface (/schedule, /token, /agent/join, /sessions)
    pipeline.py  — compose the Pipecat graph (LiveKit in → STT → bridge → TTS → out)
    runner.py    — MeetingSessionManager: agent joins/leaves rooms, lifecycle
    bridge.py    — name-gated AgentBridge (speaker-aware) → llm_agent.run_stream

Reused unchanged from the rest of the stack: the one brain (`run_stream`), RAG /
persona / university knowledge, conversation + episodic memory, the STT/TTS
factories, and the `Session` contract. Nothing here imports the avatar or voice
channels — it's a self-contained channel like every other one.
"""

from agent_backend.channels.meeting.routes import router

__all__ = ["router"]
