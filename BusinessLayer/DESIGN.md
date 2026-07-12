# BusinessLayer — Lead Orchestration & Agent Memory

**Status:** Design (approved approach, pre-implementation)
**Location:** `avatar-fe/BusinessLayer/` — a standalone service, sibling to `AegisBackend/`
**Last updated:** 2026-06-04

---

## 1. What this is

`AegisBackend` is the **conversation engine**: it talks to candidates across channels
(voice / WhatsApp / Teams chat) through one persona-tunable brain. It is good at
*holding a conversation*. It is intentionally **stateless about long-term data**.

`BusinessLayer` is the **operational brain** that sits *around* the conversation engine.
It answers three questions AegisBackend should not have to:

1. **Who do we call, and when?** — campaign / outbound dialing over a lead list.
2. **What happened, and what's the candidate worth?** — post-call analysis →
   interest, confidence, status, extracted facts (marks, entrance scores, budget…).
3. **What do we do next?** — fire follow-up actions (send brochure on WhatsApp,
   schedule a callback) and remember everything for the next conversation.

The two are **separate services** (separate folders, separate processes, separate
deploy units) that communicate over a small HTTP contract (§10).

```
avatar-fe/
├── AegisBackend/         # conversation engine (exists)
├── DockerizedOptimalCode/
└── BusinessLayer/        # ← THIS — lead orchestration, analysis, memory (new)
```

---

## 2. Design principles (the decisions that shaped this)

| Decision | Choice | Why |
|---|---|---|
| **Where it lives** | Separate sibling service in `avatar-fe`, not inside AegisBackend | Clean separation of concerns; the conversation engine stays focused; can scale/deploy independently |
| **DB** | SQLite now, behind swappable repository ports | Small scale today; ports make the Postgres swap a wiring change, not a rewrite |
| **Analysis timing** | **Post-call batch only** — one LLM call per finished conversation | No repeated token spend; the per-turn extractor in AegisBackend stays OFF |
| **Facts storage** | A **`facts` JSON column on `leads`**, merged/appended — no separate fact tables | Simple; "append across channels" = a dict merge |
| **Scale** | Small — in-process async loops, paced, concurrency-capped. No Celery/Redis. | Matches current volume; queue/locks come later via the same ports |
| **System of record** | **BusinessLayer owns leads + sessions + tasks.** AegisBackend reads memory from it and pushes transcripts to it. | One source of truth; AegisBackend becomes a thin, replaceable conversation engine |

**Core mental model**

> Anchor everything on `lead_id`. A **session** is one interaction (one call, one
> WhatsApp thread). When a session ends, **one** LLM pass analyzes the transcript and
> **merges** its findings (facts + scores + status) into the lead. Talk to the same
> candidate on another channel later → another session ends → another merge into the
> same lead. You never overwrite a report; you append into one JSON dict under a lock.

---

## 3. Responsibilities & ownership

| Concern | Owner | Notes |
|---|---|---|
| Lead records (contact, consent, status) | **BusinessLayer** | System of record. Seeded from AegisBackend's `test-data/leads.json` on first boot. |
| Campaign / who-to-call-next | **BusinessLayer** | Dialer loop. |
| Session lifecycle + transcript | **BusinessLayer** (stored) / AegisBackend (produced) | AegisBackend pushes open/turns/close events. |
| Post-call analysis (1 LLM call) | **BusinessLayer** | Analyzer poller. |
| Candidate facts (marks, exams, budget…) | **BusinessLayer** | `leads.facts` JSON, merged by the analyzer. |
| Follow-up actions (brochure, callback) | **BusinessLayer** decides, AegisBackend executes | Outbox `tasks` → calls AegisBackend's send/dial endpoints. |
| Agent memory served to the brain | **BusinessLayer** exposes, AegisBackend consumes | `GET /leads/{id}/memory` at session start. |
| Holding the conversation | **AegisBackend** | Unchanged `run_stream` brain. |
| Channel I/O (dial, WhatsApp send, TTS) | **AegisBackend** | BusinessLayer never reimplements channel transport. |

---

## 4. Folder structure

```
avatar-fe/BusinessLayer/
├── DESIGN.md                      # this doc
├── pyproject.toml
├── .env.example
├── alembic/                       # DB migrations from day one
│   └── versions/
└── business/
    ├── main.py                    # FastAPI app + lifespan that starts the loops
    ├── config.py                  # pydantic-settings (DB url, AEGIS_BASE_URL, caps…)
    ├── api/                       # HTTP surface AegisBackend calls
    │   ├── memory.py              # GET /leads/{id}/memory
    │   ├── sessions.py            # POST /sessions, /turns, /close
    │   └── leads.py               # CRUD / import / list
    ├── services/                  # the background workers (async loops)
    │   ├── dialer.py              # picks leads → calls AegisBackend /api/voice/dial
    │   ├── analyzer.py            # post-call: 1 LLM pass → merge into lead
    │   ├── actions.py             # outbox worker → send brochure / schedule callback
    │   └── scheduler.py           # wires loops into the FastAPI lifespan
    ├── store/                     # persistence (ports + SQLite impl)
    │   ├── base.py                # Protocols: LeadStore, SessionStore, TaskStore
    │   ├── models.py              # SQLModel table definitions
    │   └── sqlite/                # aiosqlite implementation + engine
    ├── clients/
    │   └── aegis.py               # HTTP client → AegisBackend (dial, whatsapp send)
    └── domain/
        ├── lead.py                # Lead model (mirrors AegisBackend's Lead)
        ├── analysis.py            # analyzer output schema + merge rules
        └── lifecycle.py           # LeadStatus state machine
```

---

## 5. Data model — 3 tables (SQLite now, swappable)

Stack: **SQLModel** (Pydantic + SQLAlchemy 2.0) over **aiosqlite**, **Alembic** migrations,
`PRAGMA journal_mode=WAL` so reads don't block the single writer.

### `leads` — system of record + cumulative memory
```
lead_id                        PK   (mirrors AegisBackend Lead.lead_id)
full_name
phone_e164                     UNIQUE INDEX
email
source
language_preference
course_interest / intake_year / city / parent_name / parent_phone_e164
consent_call                   BOOL
consent_whatsapp               BOOL
status                         LeadStatus  (see §11)

-- analysis rollup (written by the analyzer) --
facts                          JSON   ◄── extracted candidate details, MERGED here
interest                       INT    (0-100)
confidence                     INT    (0-100)
summary                        TEXT   (one-paragraph "what we know / last talk")
open_concerns                  JSON   (list[str])
last_analyzed_session_ended_at TIMESTAMP   (guards out-of-order merges)

-- campaign control --
call_attempts                  INT
next_action_at                 TIMESTAMP   (when the dialer may try again)

version                        INT    (optimistic concurrency)
created_at / updated_at
```

### `sessions` — one row per interaction
```
session_id        PK   (== AegisBackend conversation_id)
lead_id           FK
channel                (voice | whatsapp | chat)
direction              (inbound | outbound)
provider_call_id       (ACS callConnectionId / Plivo call uuid)
started_at
ended_at
status                 (active | ended | failed)
end_reason
transcript        JSON ◄── flushed on close: [{role, text, ts}, ...]
analysis          JSON ◄── this session's snapshot {summary, interest, sentiment, actions}
analyzed          BOOL
```

### `tasks` — outbox of side-effects to execute
```
id                PK
lead_id
session_id
type                   (send_brochure | schedule_followup | callback)
payload           JSON
channel
status                 (pending | in_progress | done | failed)
dedupe_key        UNIQUE INDEX   (e.g. "L123:send_brochure:cse_pdf")
attempts
scheduled_for
last_error
created_at
```

**Why no `messages` / `lead_facts` / `candidate_profile` / `session_analyses` tables:**
kept deliberately simple. Transcript lives as JSON on the session; per-session analysis
lives as JSON on the session; cumulative facts live as JSON on the lead. Provenance
(who said what) is recoverable from `sessions.transcript` if ever needed.

---

## 6. The analyzer — one LLM call per finished conversation

Trigger: a poller (every ~10–30 s) claims sessions where `status='ended' AND analyzed=false`.

It makes **one** LLM call (reuse `gpt-4o-mini`) over `sessions.transcript`, producing a
single combined output (analysis **and** facts in the same pass):

```jsonc
{
  "summary":    "Keen on B.Tech CSE; worried about hostel + fees; waiting to talk to father.",
  "interest":   72,                 // 0-100
  "confidence": 80,                 // 0-100 (how sure the model is)
  "sentiment":  "positive",         // positive | neutral | negative | frustrated
  "status":     "FOLLOWUP",         // proposed LeadStatus transition
  "open_concerns": ["hostel distance", "total fees"],
  "facts": {                        // extracted candidate details
    "marks_12th_pct": 88,
    "entrance_exam":  "JEE Main",
    "jee_percentile": 91.4,
    "course_interest":"B.Tech CSE",
    "budget_lakhs":   6,
    "parent_involved": true
  },
  "actions": [
    { "type": "send_brochure", "payload": { "doc": "cse_program_pdf" } }
  ]
}
```

Then, in **one transaction** guarded by a per-lead lock + `version` compare-and-set:

1. `sessions.analysis = {summary, interest, sentiment, actions}`; `analyzed = true`.
2. **Merge `facts` into `leads.facts`** — dict merge, *last-non-null wins* (never overwrite
   a known value with null; new keys append; changed values update).
3. Refresh `leads.interest`, `leads.confidence`, `leads.summary`, `leads.open_concerns`,
   `leads.status` — but only if `session.ended_at >= leads.last_analyzed_session_ended_at`
   (so a late-arriving older call can't stomp a newer call's status).
4. Insert each `action` as a `tasks` row with a `dedupe_key`.
5. Bump `leads.version`, set `last_analyzed_session_ended_at = session.ended_at`.

> The per-turn `ConversationState` extractor inside AegisBackend stays **disabled**
> (`enable_conversation_state=false`) — all extraction now happens here, once, post-call.

---

## 7. Cross-channel merge — "append vs update the report"

The recurring hard question, solved in its simplest form:

- Voice call ends → analyzer merges facts into `leads.facts`, sets interest/status.
- Later WhatsApp chat ends → analyzer runs again → **merges its facts into the same
  `leads.facts`** (adds `neet_score`, updates `budget_lakhs` if it changed), refreshes
  summary/status.

So "appending to the report after another channel" = **a dict merge into one JSON column.**

Correctness guards (cheap on SQLite, correct on Postgres later):
- **Per-lead `asyncio.Lock`** so two sessions finishing together can't interleave merges.
- **`version` compare-and-set** on the `leads` row (`UPDATE … WHERE version = v`; retry on miss).
- **`last_analyzed_session_ended_at`** so out-of-order analysis can't regress newer state.

Trade-off accepted: no per-fact provenance index. The raw transcript is retained on the
session, so you can always trace a fact back by hand.

---

## 8. Agent memory — context-aware follow-ups

Agent memory is a **read-model served by BusinessLayer**, not a new store. When AegisBackend
starts a session it calls `GET /leads/{lead_id}/memory` and gets exactly what the prompt's
**LEAD PROFILE** slot needs:

```jsonc
{
  "lead_id": "L123",
  "full_name": "Rahul",
  "language_preference": "hi",
  "status": "FOLLOWUP",
  "facts": { "marks_12th_pct": 88, "jee_percentile": 91.4, "budget_lakhs": 6, ... },
  "summary": "Last call: keen on CSE, worried about hostel + fees, waiting on father.",
  "open_concerns": ["hostel distance", "total fees"]
}
```

- Composed from `leads.facts` + `leads.summary` + `leads.open_concerns` (+ the live in-call
  buffer, which AegisBackend already holds in RAM).
- **Read once at session start, cached for the call** — adds ~one HTTP call per call, not
  per turn (protects first-turn latency).
- This is what makes the 2nd/3rd conversation context-aware: the analyzer wrote it last
  time, memory serves it this time.

---

## 9. The four internal services (background loops)

All are plain async loops started as `asyncio.Task`s in BusinessLayer's FastAPI lifespan.

### 9.1 Dialer (`services/dialer.py`)
1. `claim_due_for_call(limit)` → leads where `status in (NEW, FOLLOWUP)`, `consent_call=true`,
   `next_action_at <= now`, `call_attempts < max`.
2. Respect a **`Semaphore(MAX_PARALLEL_CALLS)`** (also your Plivo parallel-call ceiling) and
   a **pacing delay** between dials.
3. Mark `status=IN_CALL`, `call_attempts += 1`; `POST {AEGIS}/api/voice/dial {lead_id, phone}`.
4. No-answer/busy → `status=FOLLOWUP`, `next_action_at = now + backoff`.

### 9.2 Analyzer (`services/analyzer.py`)
Post-call, one LLM pass, merge into lead — see §6.

### 9.3 Action worker / outbox (`services/actions.py`)
1. Claim `tasks` where `status='pending' AND scheduled_for <= now`.
2. Dispatch by `type`:
   - `send_brochure` → `POST {AEGIS}` WhatsApp send endpoint.
   - `schedule_followup` → set `lead.next_action_at` + `status=FOLLOWUP` (dialer re-picks it).
   - `callback` → enqueue a dial.
3. Success → `done`; failure → `attempts++`, backoff, `failed` after N.
   Unique `dedupe_key` guarantees a brochure is never sent twice.

### 9.4 API server (`api/`)
The HTTP surface AegisBackend calls — memory, session lifecycle, lead import (§10).

---

## 10. Integration contract (HTTP between the two services)

### BusinessLayer → AegisBackend (commands)
| Method | Endpoint | Purpose | Status |
|---|---|---|---|
| POST | `/api/voice/dial` | Start an outbound call `{lead_id, phone}` | **exists** |
| POST | `/api/whatsapp/send` | Send a template/brochure `{lead_id or phone, doc/template}` | **may need a thin endpoint** (WhatsApp client exists; expose a REST send) |

### AegisBackend → BusinessLayer (memory + session events)
| Method | Endpoint | Purpose |
|---|---|---|
| GET  | `/leads/{lead_id}/memory` | Fetch memory bundle at session start (§8) |
| POST | `/sessions` | Open a session `{session_id, lead_id, channel, direction, provider_call_id}` |
| POST | `/sessions/{session_id}/turns` | (optional) push a turn `{role, text, ts}` as it happens |
| POST | `/sessions/{session_id}/close` | Mark ended `{end_reason, transcript?}` → triggers analysis |

> Two transcript options: push per-turn (durable, crash-safe) **or** send the whole
> transcript on close (simplest). Start with **on-close**; move to per-turn if you want
> crash-safety for calls that drop mid-conversation.

### AegisBackend changes (additive, non-breaking)
- Introduce a `LeadStore` **port** with two impls: existing local JSON (dev/standalone)
  and a new HTTP client → BusinessLayer. Flip via config. Existing flows keep working.
- At session start: call `GET /leads/{id}/memory`, inject into the LEAD PROFILE slot.
- At session open/close: notify BusinessLayer; flush transcript on close.

---

## 11. Lead lifecycle (state machine)

```
NEW ──dialer──► IN_CALL ──hangup──► (ended session) ──analyzer──► one of:
 │                                                                  ├─ FOLLOWUP   (re-dial after next_action_at)
 │                                                                  ├─ SCHEDULED  (campus visit / callback booked)
 ├─ no consent ──► (skipped)                                        ├─ CONVERTED  (terminal, sticky)
 │                                                                  └─ LOST       (terminal, sticky)
 └─ inbound call/chat ──► creates session ──► analyzer ──► same transitions

FOLLOWUP ──next_action_at due & attempts<max──► IN_CALL (re-dial)
```

- **Dialer** is the only writer of `IN_CALL` and `call_attempts`.
- **Analyzer** is the only writer of post-call status.
- `CONVERTED` / `LOST` are terminal and sticky (the merge never downgrades them).

---

## 12. End-to-end flows

### Outbound campaign call
```
dialer picks lead → POST /api/voice/dial → AegisBackend dials (Plivo/ACS)
  → on connect: AegisBackend GET /leads/{id}/memory, POST /sessions
  → conversation (brain uses facts+summary in prompt)
  → hangup: AegisBackend POST /sessions/{id}/close {transcript}
  → analyzer: 1 LLM pass → merge facts/scores/status into lead, emit tasks
  → action worker: POST /api/whatsapp/send (brochure) if requested
  → lead now FOLLOWUP with next_action_at; dialer re-picks later
```

### Inbound WhatsApp (same candidate, later)
```
AegisBackend resolves lead by phone → GET /leads/{id}/memory (knows him already)
  → POST /sessions (channel=whatsapp) → conversation
  → idle/close → POST /sessions/{id}/close → analyzer merges NEW facts into SAME lead
  → leads.facts now spans both channels; summary/status refreshed
```

---

## 13. Tech stack

| Concern | Choice |
|---|---|
| Service | FastAPI (matches AegisBackend) |
| DB | SQLite + aiosqlite now; Postgres + asyncpg later (port swap) |
| ORM / models | SQLModel (Pydantic + SQLAlchemy 2.0) |
| Migrations | Alembic (from day one) |
| HTTP client | httpx (async) for the AegisBackend client |
| Background work | `asyncio` loops in the FastAPI lifespan (no Celery/Redis at small scale) |
| LLM | reuse `gpt-4o-mini` (the existing extractor model) for the single analysis pass |

---

## 14. Configuration (`.env`)

```
# --- BusinessLayer ---
DATABASE_URL=sqlite+aiosqlite:///./business.db
AEGIS_BASE_URL=http://localhost:8001          # AegisBackend
ANALYZER_LLM_MODEL=gpt-4o-mini
ANALYZER_POLL_SECONDS=15
MAX_PARALLEL_CALLS=3                           # also the Plivo concurrency cap
DIAL_PACING_SECONDS=5
CALL_MAX_ATTEMPTS=3
CALL_BACKOFF_MINUTES=120
LEADS_IMPORT_PATH=../AegisBackend/test-data/leads.json   # one-shot seed

# --- AegisBackend (new, additive) ---
LEAD_STORE=http                                # "json" (current) | "http" (BusinessLayer)
BUSINESS_LAYER_URL=http://localhost:8002
```

---

## 15. Build plan (each step independently shippable)

1. **Scaffold + store**: FastAPI app, `config.py`, SQLModel `leads`/`sessions`/`tasks`,
   Alembic, SQLite engine, `LeadStore`/`SessionStore`/`TaskStore` ports.
   One-shot importer: `test-data/leads.json` → `leads`.
2. **Session capture**: `/sessions`, `/sessions/{id}/close` endpoints; AegisBackend pushes
   open/close + transcript on close. *(Now conversations are persisted.)*
3. **Analyzer**: poller + single-LLM-pass analysis + facts merge into `leads.facts` +
   status/score rollup + emit `tasks`. *(Now you get interest/confidence/facts.)*
4. **Memory API**: `GET /leads/{id}/memory`; AegisBackend `LeadStore=http` + LEAD PROFILE
   injection. *(Now follow-ups are context-aware.)*
5. **Action worker**: outbox drain → AegisBackend WhatsApp send / callback.
   *(Now "send brochure" works, idempotently.)*
6. **Dialer**: paced loop, concurrency cap, retry/backoff. *(Now it auto-calls uncalled leads.)*

---

## 16. Robustness checklist

- **Crash mid-call** → if transcript pushed per-turn it survives; otherwise a reaper marks
  stale `active` sessions `ended(end_reason=crash)` so analysis still runs (degraded).
- **Double analysis** → analysis is idempotent (merge under version CAS); re-running is safe.
- **Double send** → `tasks.dedupe_key` unique index.
- **Consent / DND** → dialer filters `consent_call`; action worker checks `consent_whatsapp`
  **at execution time** (consent can change after enqueue).
- **WhatsApp 24 h window** → action worker checks the window; if closed, use a template or defer.
- **Concurrency cap** → `Semaphore` = Plivo parallel-call ceiling; never exceed the number's capacity.
- **Re-dial storms** → `call_attempts` cap + `next_action_at` backoff.
- **Out-of-order analysis** → `last_analyzed_session_ended_at` guard.
- **Timestamps** → store UTC everywhere; convert only for display.

---

## 17. Scaling later (what the ports buy you)

When volume grows, none of the contracts change — only implementations behind the ports:

- **SQLite → Postgres**: new `*Store` impls (asyncpg); per-lead `asyncio.Lock` → Postgres
  advisory lock `pg_advisory_xact_lock(hashtext(lead_id))`.
- **In-process loops → distributed workers**: dialer/analyzer/action loops → a real queue
  (arq/Redis or Postgres `SELECT … FOR UPDATE SKIP LOCKED`); the loop bodies are unchanged.
- **Recency memory → semantic memory**: add pgvector, embed session summaries, retrieve
  top-k into the memory bundle. The `GET /leads/{id}/memory` contract stays the same.
```
