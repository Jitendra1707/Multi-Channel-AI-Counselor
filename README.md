# Multi-Channel AI Counselor

An end-to-end **agentic AI platform** where a single persona-driven LLM agent counsels student leads across **four channels — outbound phone calls (PSTN), WhatsApp, email, and real-time video avatars** — with shared memory so context carries across every conversation. Built as five cooperating services plus an enterprise admin workbench.

> One brain, every channel: the use case is tuned by the agent's **persona**, not by code branches. The tool surface and output style switch on the session's channel.

```
                                ┌────────────────────────────┐
                                │   admission_counsellor     │
                                │   Angular 19 workbench     │
                                │  CRM · KMS · Live Monitor  │
                                └─────────┬──────────────────┘
                                          │ HTTP
        phone (PSTN) ─┐         ┌─────────▼──────────┐          ┌────────────────┐
        WhatsApp ─────┤ webhook │    AgentBackend    │──HTTP───▶│    live-kit    │
        email ────────┼────────▶│  FastAPI · :8001   │          │ LiveKit control│
        WebRTC avatar ┘         │ one LLM brain, all │          │ plane · :8003  │
                                │ channels + RAG     │          └────────────────┘
                                └──┬──────────────┬──┘
                                   │ HTTP         │ WebSocket
                        ┌──────────▼─────┐  ┌─────▼──────────────┐
                        │ BusinessLayer  │  │   soulx_service    │
                        │ leads · post-  │  │ GPU talking-head   │
                        │ call analysis  │  │ avatar inference   │
                        │ FastAPI · :8002│  │ (H100) · :8011     │
                        └────────────────┘  └────────────────────┘
```

## Services

| Folder | Role | Stack | Port |
|---|---|---|---|
| [`AgentBackend/`](AgentBackend/) | Multi-channel conversation engine — voice (Plivo/ACS), WhatsApp, email, avatar video; persona-driven LLM agent with tools, episodic memory, and hybrid RAG | Python, FastAPI, Pipecat, Qdrant | 8001 |
| [`BusinessLayer/`](BusinessLayer/) | Lead lifecycle orchestration — autonomous dialer, one-pass post-call LLM analysis, idempotent follow-up actions, cross-channel memory API | Python, FastAPI, PostgreSQL (async SQLAlchemy) | 8002 |
| [`live-kit/`](live-kit/) | LiveKit control plane — room creation, JWT minting, webhook verification, behind a Cloud ↔ self-hosted provider switch | Python, FastAPI, livekit-api | 8003 |
| [`soulx_service/`](soulx_service/) | Audio-driven talking-head avatar inference — lip-syncs a reference photo in real time at 25 fps | Python, PyTorch (CUDA), nginx worker pool, k8s | 8011 |
| [`admission_counsellor/`](admission_counsellor/) | Admin workbench — CRM with Excel import, live voice/WhatsApp/email consoles, knowledge management with approvals, guardrails, analytics | Angular 19 (standalone + Signals), TypeScript | 4200 |

## Highlights

- **Real-time voice agents over telephony** — streaming STT → LLM → TTS pipeline with barge-in handling, turn detection, and sentence-level streaming for low-latency conversation.
- **Hybrid RAG grounding** — Qdrant dense + sparse retrieval with reranking; the agent answers **only** from institution-approved knowledge, and escalates to a human when confidence is low.
- **Cross-channel memory** — a phone call, a WhatsApp thread, and a video meeting with the same lead all share one accumulated fact profile (`GET /leads/{id}/memory`).
- **Autonomous funnel loop** — dialer picks due leads → agent calls → analyzer folds one LLM pass over the transcript into interest/confidence/status → action worker executes follow-ups (e.g. WhatsApp brochure), idempotently.
- **GPU avatar at scale** — a non-reentrant diffusion pipeline scaled to **10 concurrent real-time streams on one H100** via a replicated worker pool behind nginx (`least_conn`, `max_conns=1`), validated by load testing.
- **Pluggable infrastructure** — telephony (Plivo ↔ Azure Communication Services), STT/TTS vendors, and LiveKit Cloud ↔ self-hosted are all swappable by env config, not code changes.

## Quickstart

Each Python service: create a venv, install, copy `.env.example` → `.env`, fill in keys.

```bash
# AgentBackend — conversation engine (port 8001)
cd AgentBackend && python -m venv venv && venv/Scripts/pip install -r requirements.txt
venv/Scripts/python -m uvicorn agent_backend.main:app --port 8001 --reload --app-dir .

# BusinessLayer — lead orchestration (port 8002, needs PostgreSQL)
cd BusinessLayer && python -m venv venv && venv/Scripts/pip install -r requirements.txt
venv/Scripts/python -m uvicorn business.main:app --port 8002 --reload

# live-kit — LiveKit control plane (port 8003)
cd live-kit && python -m venv venv && venv/Scripts/pip install -r requirements.txt
venv/Scripts/python -m uvicorn livekit_svc.main:app --port 8003 --reload

# Web workbench (port 4200)
cd admission_counsellor && npm install && npm start
```

Health checks: `GET :8001/health`, `:8002/health`, `:8003/health`. Wire the services together via `AgentBackend/.env`: `BUSINESS_LAYER_URL=http://localhost:8002`, `LIVEKIT_SERVICE_URL=http://localhost:8003` — every integration is best-effort and non-blocking, so any service can run standalone.

`soulx_service` requires an NVIDIA GPU; see its [README](soulx_service/README.md) for the Docker/AKS build.

## Responsible AI

The counselor always discloses it is an AI, never invents fees/scholarships/placements (RAG-grounded answers only), records consent at lead capture, and routes emotionally sensitive conversations to a human via a built-in handoff queue with a non-negotiable care path.

## Tech

Python · FastAPI · OpenAI LLMs · Pipecat · Qdrant (hybrid RAG + reranking) · WebRTC · LiveKit · Plivo / Azure Communication Services · Azure Speech STT/TTS · PostgreSQL (SQLAlchemy async) · PyTorch (CUDA) · Angular 19 (Signals) · TypeScript · Docker · Kubernetes (AKS GPU node pools) · nginx

## License

[MIT](LICENSE)
