# Meeting channel — LiveKit counselling room with a listening agent

A virtual counselling meeting hosted on **LiveKit**. Two humans (a counsellor and
a candidate) join from the browser; the AgentBackend agent joins **server-side**
as a third participant. The agent listens to the whole conversation, answers /
suggests / guides **only when addressed by name**, and on meeting end flushes the
speaker-tagged transcript to the BusinessLayer for a **dual analysis** of both the
candidate and the counsellor.

```
   Counsellor (browser) ─┐
                         ├──▶  LiveKit room  ◀──  AgentBackend agent (audio-only,
   Candidate  (browser) ─┘       (SFU)              Pipecat LiveKitTransport)
                                                       │ on room empty
                                                       ▼
                                              BusinessLayer dual analysis
                                              (candidate + counsellor)
```

## Why this design

- **It's `avatar_video` with three changes**: LiveKit transport instead of
  SmallWebRTC (multi-party room vs single browser offer), the addressee gate
  flipped **on** (the agent stays silent through human↔human talk), and a dual
  end-of-meeting analysis. The **one brain** (`llm_agent.run_stream`), RAG,
  persona, conversation/episodic memory, and the STT/TTS factories are reused
  unchanged.
- **Audio-only agent** (v1). It publishes its TTS as an audio track and has no
  video tile. A Simli avatar tile can be added later by reusing the isolated
  avatar Simli service — the channel boundary keeps that change contained.

## Files

| File | Role |
|------|------|
| `scheduler.py` | Create a LiveKit room + mint candidate/counsellor JWTs (`livekit-api`). |
| `routes.py`    | HTTP surface: `/schedule`, `/token`, `/agent/join`, `/session/{room}`, `/sessions`. |
| `runner.py`    | `MeetingSessionManager` — the agent joins/leaves rooms; lifecycle + speaker-role map. |
| `pipeline.py`  | Compose `LiveKit in → STT → name-gated bridge → TTS → LiveKit out`. |
| `bridge.py`    | `MeetingAgentBridge` — name-gated + speaker-aware bridge to the brain. |
| `analysis.py`  | Hand the diarised transcript to the BusinessLayer for dual analysis. |

## How it works

1. **Schedule** — `POST /api/meeting/schedule {candidate_name, counsellor_name,
   candidate_lead_id?}` creates the room, mints two join JWTs (each with the role
   in participant metadata), optionally emails the links, and dispatches the
   agent into the room.
2. **Humans join** — each opens their personal link
   (`<web-app>/meeting/<room>?token=…&role=…`). The agent is already in the room.
3. **Listening + speaker attribution** — LiveKit delivers each human's audio on a
   separate track tagged with the participant id; Pipecat's STT copies that id
   onto every `TranscriptionFrame`. The bridge maps id → role (from participant
   metadata) and tags each turn `[CANDIDATE]` / `[COUNSELLOR]`.
4. **Answer when addressed** — the `AddresseeGate` (persona name + aliases) lets a
   turn reach the brain only when the agent is named or given a direct request;
   everything else is dropped (but still captured to the diarised transcript).
5. **End → dual analysis** — when the last human leaves, the agent leaves and the
   speaker-tagged transcript is posted to the BusinessLayer, which runs the
   candidate rubric (updates the lead) **and** a counsellor-performance rubric.

## Configuration

Backend (`.env`):

```
LIVEKIT_URL=wss://<project>.livekit.cloud   # LiveKit Cloud (or ws://host:7880 self-host)
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
# MEETING_JOIN_BASE_URL=https://counselor.example.com   # defaults to first FRONTEND_URLS origin
# MEETING_REQUIRE_ADDRESS=true                           # keep true in prod
# MEETING_CONSENT_LINE=...                               # optional spoken disclosure on first join
```

Web-app (`.env.local`):

```
NEXT_PUBLIC_LIVEKIT_URL=wss://<project>.livekit.cloud   # same URL the backend uses
```

## Run

```bash
# backend
python -m uvicorn agent_backend.main:app --host 0.0.0.0 --port 8001 --app-dir .

# web-app
cd ../web-app && npm install && npm run dev   # http://localhost:3000/meeting

# BusinessLayer (for the dual analysis; optional)
cd ../BusinessLayer && python -m uvicorn business.main:app --port 8002
```

Then open `http://localhost:3000/meeting`, create a meeting, open the counsellor
link, and share the candidate link. Say the persona's name (e.g. "Aisha, what
are the fees?") to bring the agent in.

## Production notes

- **TURN is mandatory** across corporate NATs. LiveKit Cloud includes it;
  self-hosted needs `coturn`.
- **Recording / compliance** — use LiveKit Egress to record the room server-side
  and set `MEETING_CONSENT_LINE` so the agent states the disclosure on join.
- **Scale** — one Pipecat agent process per active meeting; for high concurrency
  move agent dispatch onto LiveKit's agent framework + a worker pool.
- **Tools** — the meeting channel has its own (empty) tool group. Drop a file
  under `llm_agent/tools/meeting/` to give the agent a meeting-scoped tool (e.g.
  `flag_concern`, `summarize_so_far`) without touching the other channels.
```
