"""BusinessLayer FastAPI entry.

Owns the operational DB (leads / sessions / tasks), exposes the HTTP surface
AegisBackend calls (session lifecycle + memory + leads), and runs the background
workers (analyzer, actions, optional dialer) over the app lifespan.

Run with:
    python -m uvicorn business.main:app --host 0.0.0.0 --port 8002 --reload
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

# Windows: Playwright launches Chromium via a subprocess, which the default
# SelectorEventLoop cannot spawn (raises NotImplementedError). The Proactor
# loop supports subprocesses. Set the policy at import time — before uvicorn
# creates the loop — so the web extractor can start a browser. No effect on
# the DB / analyzer / actions workers, which run fine on either loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from business.api import extractor_router, knowledge_router, leads_router, sessions_router
from business.config import get_settings
from business.db import init_db
from business.logging import configure_logging, get_logger
from business.services.scheduler import WorkerManager

configure_logging()
log = get_logger(__name__)

_workers = WorkerManager()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    log.info(
        "business-layer starting",
        host=s.host,
        port=s.port,
        aegis=s.aegis_base_url,
        analyzer_model=s.analyzer_llm_model,
    )

    # 1. Create the schema if absent (idempotent). Lead DATA is loaded manually
    #    via sql/seed_leads.sql — the app does not seed.
    await init_db()

    # 2. Background workers.
    _workers.start()
    try:
        yield
    finally:
        await _workers.stop()
        log.info("business-layer stopped")


app = FastAPI(
    title="BusinessLayer",
    version="0.1.0",
    description="Lead orchestration, post-call analysis, and agent memory for AegisBackend.",
    lifespan=lifespan,
)

# --- CORS — the Next.js web-app uploads leads directly to this service ---
# Comma-separated allowed origins from FRONTEND_URLS (default localhost:3000 for
# dev). The leads-upload page (and any future direct browser call) needs this.
_cors_origins = [o.strip() for o in get_settings().frontend_urls.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or ["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-Id"],
)

app.include_router(sessions_router)
app.include_router(leads_router)
app.include_router(extractor_router)
app.include_router(knowledge_router)


@app.get("/health")
async def health() -> dict:
    from business.store import get_store

    s = get_settings()
    leads = 0
    try:
        leads = await get_store().count_leads()
    except Exception:  # noqa: BLE001
        pass
    return {
        "ok": True,
        "service": "business-layer",
        "version": app.version,
        "leads": leads,
        "llm_configured": bool(s.llm_api_key),
        "aegis_base_url": s.aegis_base_url,
        "workers": {
            "analyzer": s.analyzer_enabled,
            "actions": s.actions_enabled,
            "dialer": s.dialer_enabled,
        },
    }
