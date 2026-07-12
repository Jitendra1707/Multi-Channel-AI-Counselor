"""Web lead-extraction endpoint.

  GET  /extractor/config   what's configured (URL, headless, model) — no secrets
  POST /extractor/run      run the navigator against the configured URL, parse
                           the result into leads, and append them to the leads
                           JSON file. Pass ?background=true to fire-and-forget.

The URL and headless flag come from .env (EXTRACTOR_URL / EXTRACTOR_HEADLESS),
not the request body — matching the requirement to drive them from config.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query

from business.config import get_settings
from business.logging import get_logger
from business.services.extractor import run_extraction

log = get_logger(__name__)
router = APIRouter(prefix="/extractor", tags=["extractor"])


@router.get("/config")
async def extractor_config() -> dict:
    """Show the effective extraction config (no credentials)."""
    s = get_settings()
    return {
        "url": s.extractor_url,
        "headless": s.extractor_headless,
        "model": s.extractor_llm_model,
        "max_steps": s.extractor_max_steps,
        "goal": s.extractor_goal,
        "output_path": s.extractor_output_path,
        "has_credentials": bool(s.extractor_username),
        "llm_configured": bool(s.llm_api_key),
    }


@router.post("/run")
async def extractor_run(background: bool = Query(default=False)) -> dict:
    """Run the extraction pipeline.

    Synchronous by default (returns the result). With ?background=true the run
    is launched as a task and the endpoint returns immediately — useful since a
    full navigation can take a while.
    """
    s = get_settings()
    if not s.extractor_url:
        raise HTTPException(400, "EXTRACTOR_URL is not configured in .env")
    if not s.llm_api_key:
        raise HTTPException(400, "LLM_API_KEY is not configured in .env")

    if background:
        asyncio.create_task(_run_safe())
        return {"ok": True, "started": True, "background": True, "url": s.extractor_url}

    try:
        return await run_extraction()
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        log.warning("[extractor] run failed", err=str(e))
        raise HTTPException(500, f"extraction failed: {e}") from e


async def _run_safe() -> None:
    """Background wrapper — log instead of raising (no caller to receive it)."""
    try:
        result = await run_extraction()
        log.info("[extractor] background run finished", **{k: result[k] for k in ("parsed_count", "appended", "updated")})
    except Exception as e:  # noqa: BLE001
        log.warning("[extractor] background run failed", err=str(e))
