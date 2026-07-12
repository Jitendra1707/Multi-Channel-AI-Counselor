"""BusinessLayer settings — loaded from `.env` via pydantic-settings.

Cached with `@lru_cache` so every module shares one instance. The `.env` is
resolved to an absolute path anchored at the service root, so it loads no
matter which directory uvicorn is launched from (same trick AegisBackend uses).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# business/config.py -> service root is parent of the `business` package.
_SERVICE_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _SERVICE_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_PATH),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- HTTP server ---
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8002, ge=1, le=65535)

    # Browser origins allowed to call this service directly (CORS) — e.g. the
    # Next.js leads-upload page. Comma-separated.
    frontend_urls: str = Field(default="http://localhost:3000", alias="FRONTEND_URLS")

    # --- Database (PostgreSQL via asyncpg) ---
    # In Kubernetes, inject DATABASE_URL as an env var / secret pointing at your
    # Postgres service, e.g.
    #   postgresql+asyncpg://USER:PASS@postgres.svc.cluster.local:5432/aegis
    database_url: str = Field(
        default="postgresql+asyncpg://aegis:aegis@localhost:5432/aegis",
        alias="DATABASE_URL",
    )

    # --- AegisBackend (conversation engine) ---
    aegis_base_url: str = Field(default="http://localhost:8001")
    aegis_timeout_s: float = Field(default=15.0, ge=1.0, le=120.0)

    # --- Analyzer LLM (OpenAI-compatible) ---
    llm_api_url: str = Field(default="https://api.openai.com/v1")
    llm_api_key: str = Field(default="")
    analyzer_llm_model: str = Field(default="gpt-4o-mini")
    # Output budget for the analyzer JSON. Must comfortably fit a 4-6 sentence
    # session summary + a 4-8 sentence cumulative summary + facts + the actions
    # list — and Hinglish romanised text tokenises ~2-3x heavier than English, so
    # a small cap TRUNCATES the JSON mid-string (→ "LLM returned non-JSON" + a lost
    # analysis / dropped tasks). 700 was far too low; 3000 leaves headroom.
    analyzer_max_tokens: int = Field(default=3000, ge=64, le=8192)

    # --- Web extractor (Playwright lead extraction) ---
    # The site to scrape leads from and whether the browser runs headless.
    # Both come from .env (not the CLI). The goal/credentials are optional
    # overrides — sensible defaults let `POST /extractor/run` work with just
    # a URL configured.
    extractor_url: str = Field(default="", description="Website to extract leads from.")
    extractor_headless: bool = Field(default=True, description="Run Chromium without a window.")
    extractor_llm_model: str = Field(
        default="gpt-4o-mini",
        description="Model the navigator + parser use. Kept separate from the analyzer's.",
    )
    extractor_max_steps: int = Field(default=20, ge=1, le=100)
    extractor_goal: str = Field(
        default=(
            "Extract every lead / prospective candidate visible on this site — "
            "for each, capture full name, email, phone number, city, course of "
            "interest, and the source. Read tables and forms as needed."
        ),
        description="Plain-English goal handed to the navigator.",
    )
    extractor_username: str = Field(default="", description="Optional login username/email.")
    extractor_password: str = Field(default="", description="Optional login password.")
    # Where extracted leads are appended (the existing seed file by default).
    extractor_output_path: str = Field(
        default=str(_SERVICE_ROOT / "business" / "data" / "leads.json"),
        description="JSON array file that extracted leads are upserted into.",
    )

    # --- Analyzer worker ---
    analyzer_enabled: bool = Field(default=True)
    analyzer_poll_seconds: float = Field(default=15.0, ge=1.0, le=600.0)
    # A WhatsApp/chat thread has no hangup — it's "closed" (and analyzed) once
    # it's been idle (no new turn) for this long. Text channels refresh the
    # session on EVERY turn, so 5 min cleanly ends a finished thread (a longer
    # gap just starts a new session). This applies to TEXT channels only — see
    # voice_session_idle_close_minutes below. Lower it (e.g. 2) to test quickly.
    session_idle_close_minutes: int = Field(default=5, ge=1, le=1440)
    # VOICE backstop — SEPARATE on purpose. A voice call closes explicitly on
    # hangup, so this only ever catches a CRASHED call. Critically, voice does
    # NOT refresh the session per turn (the transcript is flushed once at close),
    # so this MUST stay longer than the longest real call — otherwise an
    # in-progress call would be reaped mid-conversation and lose its post-call
    # analysis. Keep it generous (30 min); do not lower it to the text value.
    voice_session_idle_close_minutes: int = Field(default=30, ge=5, le=1440)

    # --- Action/outbox worker ---
    actions_enabled: bool = Field(default=True)
    actions_poll_seconds: float = Field(default=10.0, ge=1.0, le=600.0)
    actions_max_attempts: int = Field(default=4, ge=1, le=20)

    # --- Admissions next-steps links (post-payment messaging, Module F) ---
    # Where the entrance-exam slot-booking page lives, and the token-amount
    # payment page. Both optional: if blank, the message still sends with a
    # "our team will share the link shortly" placeholder instead of a URL.
    admission_exam_slot_url: str = Field(default="", alias="ADMISSION_EXAM_SLOT_URL")
    admission_token_payment_url: str = Field(default="", alias="ADMISSION_TOKEN_PAYMENT_URL")

    # --- Dialer worker (OFF by default — autonomous outbound calling) ---
    dialer_enabled: bool = Field(default=False)
    dialer_poll_seconds: float = Field(default=20.0, ge=1.0, le=600.0)
    dialer_max_parallel_calls: int = Field(default=3, ge=1, le=100)
    dialer_pacing_seconds: float = Field(default=5.0, ge=0.0, le=120.0)
    dialer_call_max_attempts: int = Field(default=3, ge=1, le=20)
    dialer_call_backoff_minutes: int = Field(default=120, ge=1, le=10080)
    dialer_batch_size: int = Field(default=10, ge=1, le=500)

    # --- Logging ---
    log_level: str = Field(default="INFO")
    log_pretty: bool = Field(default=True)

    @property
    def service_root(self) -> Path:
        return _SERVICE_ROOT


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """One Settings instance per process. Tests clear via `get_settings.cache_clear()`."""
    return Settings()