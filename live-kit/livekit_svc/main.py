"""livekit-svc FastAPI entry — the AegisAvatar LiveKit control plane.

Owns room creation, JWT minting, and webhook verification behind a Cloud/
self-hosted provider switch (LIVEKIT_PROVIDER). AegisBackend calls this service's
HTTP API instead of using livekit-api directly, so the Cloud↔OSS choice lives in
ONE place and AegisBackend stays clean + unchanged when you swap backends.

Run with:
    python -m uvicorn livekit_svc.main:app --host 0.0.0.0 --port 8003 --reload
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from livekit_svc.config import get_settings
from livekit_svc.logging import configure_logging, get_logger
from livekit_svc.providers import get_provider
from livekit_svc.routes import router

configure_logging()
log = get_logger(__name__)

app = FastAPI(
    title="livekit-svc",
    version="0.1.0",
    description=(
        "AegisAvatar LiveKit control plane — rooms, tokens, webhooks behind a "
        "Cloud/self-hosted provider switch."
    ),
)

# CORS so the web-app can GET /config for the SFU URL (a single source of truth).
_s = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # control-plane reads only; tighten to FRONTEND_URLS if desired
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(router)


@app.on_event("startup")
async def _log_startup() -> None:
    info = get_provider().info()
    log.info(
        "livekit-svc starting",
        host=_s.host,
        port=_s.port,
        provider=info.provider,
        url=info.url or "(unset)",
        configured=info.configured,
        forward_webhooks_to=_s.aegis_webhook_url or "(disabled)",
    )
    if not info.configured:
        log.warning(
            "LiveKit not configured — set LIVEKIT_URL / LIVEKIT_API_KEY / "
            "LIVEKIT_API_SECRET; /rooms and /token will return 503 until then"
        )
    log.info(
        "transcriber",
        auto_start=_s.transcribe_auto_start,
        azure_speech_set=bool(_s.azure_speech_key),
        output_dir=_s.transcript_output_dir,
    )


@app.on_event("shutdown")
async def _shutdown_transcribers() -> None:
    """Finalize + write any in-flight transcripts on service stop."""
    from livekit_svc.transcriber import get_transcriber_manager

    await get_transcriber_manager().shutdown()
