# AgentBackend

Multi-channel agent backend. One persona-driven brain (`llm_agent.run_stream`)
shared across every channel; the use case is tuned by the PERSONA, not by a code
branch. The tool surface and OUTPUT STYLE switch on the session's channel.

## Channels

- **Voice (counselor / PSTN)** — real outbound phone call to a lead's mobile via
  a pluggable telephony provider (ACS or Plivo). Mounted under `/api/voice`.
- **WhatsApp** — Plivo/ACS messaging webhook. Inbound text routes through the
  same counselor brain as PSTN voice (lead resolved by phone, so memory carries
  across channels).
- **Email** — email reply channel.
- **Avatar video** — browser-rendered avatar (Simli + SmallWebRTC). The
  director-briefing analytics presenter; `POST /api/avatar_video/offer`.

```
caller ──► transport ──► STT ──► AgentBridge ──► llm_agent.run_stream ──► TTS ──► caller
                                                  (one brain, every channel)
```

## Layout

```
agent_backend/
  main.py                    # FastAPI app + lifespan (boots avatar manager, RAG warmup)
  config.py                  # Pydantic settings
  infra/                     # structlog setup, tracing
  channels/
    voice/                   # PSTN counselor (ACS + Plivo)
    whatsapp/                # WhatsApp messaging
    email/                   # email reply
    avatar_video/            # Simli/WebRTC avatar presenter (self-contained)
    pipecat/
      services/              # shared STT/TTS/VAD factories (voice + avatar_video)
  llm_agent/
    agent.py                 # run_stream(text, channel, session) → AsyncIterator[str]
    session.py               # Session dataclass + channel constants
    tools/                   # auto-discovered tools (voice/, director/)
    memory/                  # conversation + episodic memory
    prompts/                 # persona-driven system-prompt assembly
  rag/                       # Qdrant hybrid retrieval + ingestion
```

## Local dev

```powershell
cd AgentBackend
python -m venv venv
.\venv\Scripts\activate
pip install -e ".[dev]"

# Fill in keys
copy .env.example .env
notepad .env

# Run
python -m uvicorn agent_backend.main:app --host 0.0.0.0 --port 8001 --reload --app-dir .
```
