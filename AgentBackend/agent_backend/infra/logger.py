"""Structured logging via structlog. Same shape as LLmLayer's logger
so side-by-side log diffs are clean during the migration.

Call `configure_logging()` once at app boot (FastAPI lifespan); after
that any module gets a logger via `get_logger(__name__)`.
"""

from __future__ import annotations

import logging
import sys

import structlog
from loguru import logger as loguru_logger

from agent_backend.config import get_settings


def configure_logging() -> None:
    """Install structlog processors.
    - LOG_PRETTY=true → colored human-readable (dev)
    - LOG_PRETTY=false → JSON one-line-per-event (prod)
    Also rewires stdlib logging so uvicorn / httpx / pipecat logs
    flow through the same pipeline.
    """
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Force UTF-8 on stdout/stderr so non-ASCII characters in log messages
    # (Unicode arrows, checkmarks, emoji, Hindi/Hinglish text) never crash a
    # `print` / structlog write on Windows consoles which default to cp1252.
    # This bit us once: a `→` in a log.debug call killed an async task mid-
    # execution because the write raised UnicodeEncodeError. Doing this at
    # boot before any processor renders means it can't bite again.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: structlog.types.Processor = (
        structlog.dev.ConsoleRenderer(colors=True)
        if settings.log_pretty
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        level=level,
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )
    # Demote noisy third-party loggers to WARNING so structured events
    # from our code remain readable. Pipecat itself logs via loguru,
    # which we let through at the configured level.
    for quiet in (
        "httpx",
        "httpcore",
        "openai",
        "uvicorn",
        "uvicorn.access",
        "uvicorn.error",
        "watchfiles",
        "websockets",
    ):
        logging.getLogger(quiet).setLevel(logging.WARNING)

    # aiortc / aioice (WebRTC for the avatar_video channel) are extremely
    # chatty at INFO/WARNING and emit COSMETIC noise that is not actionable:
    #   - aiortc.codecs.vpx: "Vp8Decoder() failed to decode, skipping package"
    #     — normal WebRTC cold-start. The VP8 decoder receives inter-frames (P
    #     frames) before the first keyframe (I-frame) arrives and logs a warning
    #     per packet until the keyframe lands, then recovers. Verified harmless
    #     on aiortc>=1.13 (we run 1.14): the browser decodes + renders fine.
    #   - aioice.ice: "Could not bind to 169.254.x.x [WinError 10049]" — ICE
    #     probing link-local APIPA interfaces it then skips; it succeeds on the
    #     real LAN IP. Per-candidate ICE state transitions are also pure noise.
    # Demote both to ERROR so only genuine failures surface. Real connection
    # problems (failed/closed peer connections) still propagate via our own
    # [avatar-video] structured logs in the runner.
    logging.getLogger("aiortc.codecs.vpx").setLevel(logging.ERROR)
    logging.getLogger("aioice.ice").setLevel(logging.ERROR)
    logging.getLogger("aioice.turn").setLevel(logging.ERROR)

    # Pipecat logs via loguru, NOT stdlib. The ProtobufFrameSerializer
    # logs every deserialized Transport message at DEBUG, which on the
    # vision path dumps the entire base64 JPEG into the console once a
    # second per source — drowning out everything useful. Wipe loguru's
    # default handler and re-add at INFO so Pipecat's important events
    # (pipeline links, start/cancel, errors) still show up while the
    # per-frame protobuf noise stops.
    loguru_logger.remove()
    loguru_logger.add(sys.stderr, level="INFO")


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Single import-of-record for getting a logger. Modules call
    `from agent_backend.infra import get_logger` — never structlog
    directly — so swapping backends later is a one-file change."""
    return structlog.get_logger(name)  # type: ignore[return-value]
