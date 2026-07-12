# BusinessLayer

Lead orchestration, post-call analysis, and agent memory for the **AegisBackend**
conversation engine. Standalone service (sibling to `AegisBackend/`). It owns the
operational DB (leads / sessions / tasks), drives AegisBackend over HTTP, and runs
the background workers. Full design: [DESIGN.md](DESIGN.md).

```
avatar-fe/
├── AegisBackend/     conversation engine (talks to candidates)
└── BusinessLayer/    THIS — who to call, what happened, what's next
```

## What it does

1. **Dialer** (off by default) — picks leads due for a call and asks AegisBackend to dial them.
2. **Analyzer** — when a conversation ends, runs **one** LLM pass over the transcript →
   interest / confidence / status + extracted facts (marks, entrance scores, budget…),
   folded into the lead. Facts merge across channels into one `leads.facts` JSON.
3. **Action worker** — executes follow-ups the analyzer queued (e.g. send a brochure
   on WhatsApp via AegisBackend), idempotently.
4. **Memory API** — serves each lead's accumulated facts/summary so the next
   conversation is context-aware (`GET /leads/{id}/memory`).

## Setup

```bash
cd avatar-fe/BusinessLayer
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt   # Windows
cp .env.example .env                                     # then edit
```

Set `DATABASE_URL` (PostgreSQL) and at least `LLM_API_KEY` (reuse your
AegisBackend OpenAI key) so the analyzer can run:

```
DATABASE_URL=postgresql+asyncpg://USER:PASS@HOST:5432/aegis
```
In Kubernetes inject `DATABASE_URL` as an env var / secret pointing at your
Postgres service.

## Run

```bash
venv\Scripts\python -m uvicorn business.main:app --host 0.0.0.0 --port 8002 --reload
```

On boot it creates the schema in Postgres (idempotent `create_all`). It does
**not** seed any data — load leads manually with the SQL script:

```bash
psql "$DATABASE_URL" -f sql/seed_leads.sql        # run after the first boot
```

Health: `GET http://localhost:8002/health`

## Wiring AegisBackend to it (additive, non-breaking)

In **AegisBackend's** `.env` add:

```
BUSINESS_LAYER_URL=http://localhost:8002
```

That single line turns on the integration. With it unset, AegisBackend behaves
exactly as before. When set, AegisBackend:
- fetches a lead's memory at session start (fills the LEAD PROFILE prompt slot), and
- pushes session lifecycle + transcript so the analyzer can run.

Every such call is best-effort with a short timeout — a down/absent BusinessLayer
never blocks or slows a live call.

## HTTP surface

| Method | Path | Purpose |
|---|---|---|
| GET  | `/health` | liveness + config + lead count |
| GET  | `/leads` | list leads (`?status=`, paging) |
| POST | `/leads` | create a lead |
| GET  | `/leads/{id}` | full lead view |
| GET  | `/leads/{id}/memory` | memory bundle (AegisBackend reads this) |
| POST | `/sessions` | open a session |
| POST | `/sessions/{id}/turns` | append a turn |
| POST | `/sessions/{id}/close` | end + queue for analysis |
| GET  | `/sessions/{id}` | inspect a session |

## Workers (env flags)

| Flag | Default | Notes |
|---|---|---|
| `ANALYZER_ENABLED` | `true` | post-call analysis (needs `LLM_API_KEY`) |
| `ACTIONS_ENABLED` | `true` | outbox / follow-up execution |
| `DIALER_ENABLED` | `false` | autonomous outbound calling — turn on deliberately |

## Scaling to multiple workers

The store is SQLAlchemy-async over PostgreSQL. The only thing to revisit when you
run multiple worker processes is the per-lead in-process lock in `store.py` —
swap it for a Postgres advisory lock; nothing else changes.
