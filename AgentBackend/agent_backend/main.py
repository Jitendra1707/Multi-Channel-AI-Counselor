"""AgentBackend FastAPI entry.

Hosts these channels:

  - **Voice (counselor / PSTN)** — real outbound phone call to a lead's mobile
    via a pluggable telephony provider (Azure Communication Services first).
    Mounted under `/api/voice`. Uses the SAME `run_stream` contract but the
    counselor channel family routes it through the counselor brain (leads /
    university / RAG / conversation memory / playbook + voice tools).
  - **WhatsApp** — ACS Advanced Messaging (BSP in front of Meta). Event Grid
    webhook at `POST /channels/whatsapp/webhook`. Inbound text routes through
    the SAME counselor brain as PSTN voice (WhatsApp is in `VOICE_FAMILY`),
    resolving the candidate's lead by phone so memory carries across channels.

Run with:

    python -m uvicorn agent_backend.main:app \\
        --host 0.0.0.0 --port 8001 --app-dir . --reload
"""

from __future__ import annotations

import warnings
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

# Silence langgraph's PendingDeprecationWarning about `allowed_objects`. It's
# an upstream future-warning we can't fix from our side; suppressed at import
# time so it never pollutes startup logs.
warnings.filterwarnings(
    "ignore",
    message=r"The default value of `allowed_objects` will change",
    category=DeprecationWarning,
)
warnings.filterwarnings(
    "ignore",
    message=r"The default value of `allowed_objects` will change",
)

import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent_backend.channels.avatar.routes import router as avatar_router
from agent_backend.channels.avatar_video import router as avatar_video_router
from agent_backend.channels.avatar_video.runner import get_avatar_manager
from agent_backend.channels.meeting import router as meeting_router
from agent_backend.channels.meeting.runner import get_meeting_manager
from agent_backend.channels.voice import router as voice_router
from agent_backend.channels.voice.media_ws import media_router as voice_media_router
from agent_backend.channels.voice.plivo_routes import plivo_router as voice_plivo_router
from agent_backend.channels.whatsapp import router as whatsapp_router
from agent_backend.channels.whatsapp.send_routes import send_router as whatsapp_send_router
from agent_backend.channels.email import router as email_router
from agent_backend.config import get_settings
from agent_backend.data import LeadRepo
from agent_backend.infra import configure_logging, get_logger
from agent_backend.llm_agent.identity import ensure_identity_json

# Configure logging at module import — uvicorn reloads import this file before
# the lifespan starts, and we want every line (including import-time warnings)
# routed through structlog.
configure_logging()
log = get_logger(__name__)


async def _hydrate_persona(name: str) -> None:
    """Pull one persona JSON from Azure Blob (no-op if unset and file exists).
    Persona load is critical for prompt quality but NOT for the pipeline to
    function — the agent falls back to a no-persona prompt. Log loud, keep going.
    """
    s = get_settings()
    try:
        await ensure_identity_json(
            name=name,
            connection_string=s.connection_string,
            container_name=s.container_name,
            folder_path=s.folder_path,
            force_refresh=s.identity_force_refresh,
        )
    except Exception as e:  # noqa: BLE001
        log.error(
            "[identity] hydration failed — running without persona "
            "(prompt will skip WHO YOU ARE block)",
            persona=name,
            err=str(e),
        )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Boot every channel's dependencies in order, then serve."""
    s = get_settings()
    log.info(
        "aegis-backend starting",
        host=s.host,
        port=s.port,
        llm_model=s.llm_model,
        identity_name=s.identity_name,
        voice_provider=s.voice_provider,
    )

    # 1. Hydrate the active persona (one brain, one persona) before any channel
    # starts so the first turn's prompt sees it.
    await _hydrate_persona(s.identity_name)

    # 2. Eagerly load the leads file so a malformed JSON fails fast at boot.
    try:
        log.info("leads loaded", count=LeadRepo.get().count())
    except Exception as e:  # noqa: BLE001
        log.error("[leads] load failed — voice channel will have no lead data", err=str(e))

    # 2.5 Log BusinessLayer integration status (additive; OFF unless URL set).
    if s.business_layer_url:
        log.info("[business] integration configured", url=s.business_layer_url)

    # 2.6 Warm the RAG stack in the background (FastEmbed BM25 sparse model,
    # Qdrant clients, router magnets, core UNIVERSITY block) so the FIRST live
    # turn never pays a model load / download / cold connection. Non-blocking:
    # boot continues immediately; warmup failures only log (lazy load remains
    # the fallback). Local reference keeps the task alive through the lifespan.
    from agent_backend.rag.retriever import warmup_rag

    rag_warmup_task = asyncio.create_task(  # noqa: F841 — held to avoid GC
        asyncio.to_thread(warmup_rag), name="rag-warmup"
    )

    # 2.7 Warm Langfuse tracing in the background too. Resolving it lazily on the
    # first turn imports the langfuse+OTel stack and pings auth_check() INLINE on
    # the event loop, adding seconds to the candidate's first reply. Pre-pay it at
    # boot off-thread; the first live turn then finds the handler cached. Best-
    # effort: a no-op when tracing is disabled, and failures only log.
    from agent_backend.infra import warmup_tracing

    tracing_warmup_task = asyncio.create_task(  # noqa: F841 — held to avoid GC
        asyncio.to_thread(warmup_tracing), name="tracing-warmup"
    )

    # 2.8 Warm the LangGraph LLM stack at boot. The FIRST `_get_graph` per process
    # pays cold langgraph/langchain imports + the first `create_react_agent` compile
    # — ~14 s of SYNCHRONOUS, GIL-bound work. If that lands on the first live turn it
    # blocks the event loop, starving aiortc's media pacing (heartbeat misses, frozen
    # audio/video, RTT spikes — the avatar A/V "lag"). Per-session prewarm-at-connect
    # was tried and reverted (it starved the SoulX handshake via the GIL). Paying it
    # ONCE here, before any peer connects, has nothing to starve. Best-effort; lazy
    # build remains the fallback.
    from agent_backend.llm_agent.agent import warmup_llm_graph

    llm_warmup_task = asyncio.create_task(  # noqa: F841 — held to avoid GC
        asyncio.to_thread(warmup_llm_graph), name="llm-warmup"
    )

    try:
        yield
    finally:
        # Tear down avatar video sessions first (each holds an aiohttp client +
        # possibly a Daily pipeline task); then any live meetings (each finalises
        # its dual analysis on the way out).
        await get_avatar_manager().shutdown()
        await get_meeting_manager().shutdown()
        # Close the shared BusinessLayer HTTP client if one was opened.
        try:
            from agent_backend.integrations import business as _biz

            await _biz.aclose()
        except Exception:  # noqa: BLE001
            pass
        # Close the shared live-kit service HTTP client if one was opened.
        try:
            from agent_backend.integrations import livekit_service as _lk

            await _lk.aclose()
        except Exception:  # noqa: BLE001
            pass
        log.info("aegis-backend stopped")


app = FastAPI(
    title="AgentBackend",
    version="0.3.0",
    description=(
        "Multi-channel agent backend — PSTN voice counselor, WhatsApp, "
        "email, and browser avatar video (Simli/WebRTC)."
    ),
    lifespan=lifespan,
)

# --- CORS (Next.js web-app and any other browser clients) ---
# Reads FRONTEND_URLS from config (comma-separated list of allowed origins).
# The wildcard fallback only kicks in when FRONTEND_URLS is not set — safe
# for local dev but operators MUST set the env var in production.
_s = get_settings()
_allowed_origins = [o.strip() for o in _s.frontend_urls.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-Id"],
)

# --- Voice channel (real PSTN phone call) ---
# /api/voice/dial             — POST {lead_id} — kick off an outbound call
# /api/voice/acs/events       — POST — ACS call-lifecycle webhook
# /api/voice/acs/media        — WS — ACS streams bidirectional audio here
app.include_router(voice_router,       prefix="/api/voice")
app.include_router(voice_media_router, prefix="/api/voice")

# --- Plivo voice (alternative telephony provider; additive to ACS) ---
# /api/voice/plivo/answer — POST — Plivo Answer URL, returns <Stream> XML
# /api/voice/plivo/media  — WS   — Plivo streams bidirectional μ-law audio here
# Routes are always mounted (cheap); they only do real work when a Plivo call
# hits them (i.e. VOICE_PROVIDER=plivo and the number's Application points here).
app.include_router(voice_plivo_router, prefix="/api/voice")

# --- WhatsApp channel (Plivo WhatsApp Business API) ---
# /channels/whatsapp/inbound — POST — Plivo inbound message webhook
# Mounted unconditionally; with empty Plivo WhatsApp config the route still
# exists, acks 200, and logs a clear "outbound disabled" warning rather than
# 404-ing. The router carries its own /channels/whatsapp prefix.
app.include_router(whatsapp_router)

# --- WhatsApp outbound send (called by the BusinessLayer action worker) ---
# /api/whatsapp/send — POST {body, to_phone|lead_id, media_url?} — send a message
app.include_router(whatsapp_send_router)

# --- Avatar video channel (SoulX or Simli renderer over SmallWebRTC; AVATAR_RENDERER) ---
# /api/avatar_video/offer        — POST — WebRTC SDP offer → SDP answer
# /api/avatar_video/session/{id} — DELETE — end session by pc_id
# /api/avatar_video/sessions     — GET  — debug: list active sessions
app.include_router(avatar_video_router, prefix="/api/avatar_video")
app.include_router(avatar_router,       prefix="/api/avatar")

# --- Knowledge-review (post-call governance for director-captured facts) ---
# /api/knowledge/candidates                  — GET  the review queue (from BusinessLayer)
# /api/knowledge/candidates/{id}/edit        — POST edit → re-check → re-pending
# /api/knowledge/candidates/{id}/resolve     — POST approve | supersede | keep_both | reject
# Ingest lives in AgentBackend (it owns Qdrant), so post-call resolve hits here.
from agent_backend.channels.avatar_video.knowledge_routes import router as knowledge_review_router

app.include_router(knowledge_review_router, prefix="/api/knowledge")

# --- Meeting channel (LiveKit room: counsellor + candidate + listening agent) ---
# /api/meeting/schedule        — POST — create room + mint join links + dispatch agent
# /api/meeting/token           — POST — mint a single join token (web-app re-join)
# /api/meeting/agent/join      — POST — dispatch the listening agent into a room
# /api/meeting/session/{room}  — DELETE — agent leaves + finalise dual analysis
# /api/meeting/sessions        — GET  — debug: rooms the agent is sitting in
# Mounted unconditionally; with LIVEKIT_* unset /schedule returns 503 (graceful
# degrade like the other outbound channels), so the route exists rather than 404s.
app.include_router(meeting_router, prefix="/api/meeting")

# --- Email channel (SMTP — OUTBOUND: candidate emails + counsellor lead reports) ---
# /api/email/send        — POST {to, subject, body, html?, cc?}
# /api/email/report-lead — POST {lead_id, to?} — email a good-lead report to counsellors
# Mounted unconditionally; with EMAIL_SMTP_HOST unset, sends are disabled and
# logged (never 404/500), exactly like the WhatsApp/voice outbound paths.
app.include_router(email_router)

# --- RAG ingestion (knowledge-base WRITE surface) ---
# /api/resources        — POST multipart `file` → parse+chunk+embed+upsert to Qdrant
# /api/resources        — GET  list ingested docs
# /api/resources/{id}   — DELETE remove a doc's points
# The route handler lives in agent_backend/rag/ingestion/routes/. Imported lazily
# so the heavy ingestion deps (qdrant-client/fastembed/llama-cloud) load only
# when this router is mounted, never blocking app boot if they're absent.
try:
    from agent_backend.rag.ingestion.routes import router as rag_ingest_router

    app.include_router(rag_ingest_router)
    log.info("[rag] ingestion routes mounted", path="/api/resources")
except Exception as e:  # noqa: BLE001
    log.warning("[rag] ingestion routes NOT mounted (deps/import issue)", err=str(e)[:200])


@app.get("/health")
async def health() -> dict[str, object]:
    """Liveness probe. Reports per-channel configuration so operators can
    verify secrets reached the process without exposing the secrets."""
    s = get_settings()

    # Voice capabilities — interrogate the provider so new capability flags
    # surface here automatically as they're wired.
    voice_caps: dict[str, object] = {}
    try:
        from agent_backend.channels.voice.providers import get_voice_provider

        c = get_voice_provider().capabilities
        voice_caps = {
            "outbound":      c.supports_outbound,
            "inbound":       c.supports_inbound,
            "streaming":     c.supports_streaming,
            "transfer_warm": c.supports_transfer_warm,
            "transfer_cold": c.supports_transfer_cold,
            "dtmf_receive":  c.supports_dtmf_receive,
            "dtmf_send":     c.supports_dtmf_send,
            "recording":     c.supports_recording,
            "audio_format":  c.audio_format.value,
        }
    except Exception:  # noqa: BLE001
        # Provider not built (missing config etc.) — health should still answer.
        pass

    leads_loaded = 0
    try:
        leads_loaded = LeadRepo.get().count()
    except Exception:  # noqa: BLE001
        pass

    return {
        "ok": True,
        "service": "aegis-backend",
        "version": app.version,
        "channels": ["voice", "whatsapp", "avatar_video", "email", "meeting"],
        "voice_provider": s.voice_provider,
        "voice_configured": bool(
            s.acs_connection_string and s.acs_from_number and s.public_base_url
        ),
        "plivo_configured": bool(
            s.plivo_auth_id and s.plivo_auth_token and s.plivo_from_number and s.public_base_url
        ),
        "whatsapp_configured": bool(
            s.plivo_auth_id and s.plivo_auth_token and s.plivo_whatsapp_from
        ),
        "voice_capabilities": voice_caps,
        "leads_loaded": leads_loaded,
        "identity_name": s.identity_name,
        "business_layer_configured": bool(s.business_layer_url),
        "avatar_video_configured": bool(s.simli_api_key and s.simli_face_id),
        "avatar_video_sessions": len(get_avatar_manager().active_sessions()),
        "email_configured": bool(s.email_smtp_host),
        "meeting_configured": bool(
            s.livekit_url and s.livekit_api_key and s.livekit_api_secret
        ),
        "meeting_sessions": len(get_meeting_manager().active_sessions()),
    }
