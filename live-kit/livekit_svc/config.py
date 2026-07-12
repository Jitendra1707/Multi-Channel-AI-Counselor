"""live-kit service settings — loaded from `.env` via pydantic-settings.

This is the control-plane service for the AegisAvatar meeting channel: it owns
room creation, JWT minting, and webhook verification, behind a single provider
abstraction so LiveKit **Cloud** and a **self-hosted open-source** server are
swappable by one env var (`LIVEKIT_PROVIDER`) — AegisBackend never changes.

Mirrors the BusinessLayer config style: absolute `.env` path anchored at the
service root (loads regardless of uvicorn's cwd) + `@lru_cache` singleton.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# livekit_svc/config.py -> service root is the parent of the `livekit_svc` package.
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
    # 8003: 8001 = AegisBackend, 8002 = BusinessLayer, 8003 = this service.
    port: int = Field(default=8003, ge=1, le=65535)

    # --- Provider switch -----------------------------------------------------
    # "cloud"      → LiveKit Cloud (managed SFU + TURN).
    # "selfhosted" → open-source livekit-server you run (Docker / VM / local).
    # Both implement the SAME provider interface; only the URL/keys + a couple of
    # defaults differ. Flip this + restart THIS service to switch — AegisBackend
    # and the web-app are unaffected (they only know this service's URL).
    livekit_provider: Literal["cloud", "selfhosted"] = Field(
        default="cloud",
        alias="LIVEKIT_PROVIDER",
        description="Which LiveKit backend the control plane talks to.",
    )

    # --- LiveKit connection (used by the ACTIVE provider) --------------------
    # One set of vars drives whichever provider is selected. For Cloud these are
    # your Cloud project's values; for self-hosted they're your server's. We keep
    # ONE set (not cloud_* + selfhosted_*) because only one provider is live at a
    # time — switching means changing these three + the provider flag together.
    livekit_url: str = Field(
        default="",
        alias="LIVEKIT_URL",
        description="wss://<proj>.livekit.cloud (cloud) | ws://host:7880 / wss://lk.domain (self-host).",
    )
    livekit_api_key: str = Field(default="", alias="LIVEKIT_API_KEY")
    livekit_api_secret: str = Field(default="", alias="LIVEKIT_API_SECRET")

    # --- Room policy ---------------------------------------------------------
    livekit_room_empty_timeout_s: int = Field(default=300, ge=30, le=3600)
    # N humans + the agent. The agent is ONE entity; humans are unbounded for our
    # use (a counselling group can have several participants). Kept generous;
    # LiveKit handles dozens fine. Raise the ceiling via env if ever needed.
    livekit_room_max_participants: int = Field(default=50, ge=2, le=200)
    livekit_token_ttl_s: int = Field(default=14400, ge=300, le=86400)

    # --- Join links ----------------------------------------------------------
    # Public base URL of the web-app the join links point at. POST /schedule
    # builds <MEETING_JOIN_BASE_URL>/meeting/<room>?token=<jwt>&role=<role>. Set to
    # the tunnel/public origin when sharing externally; defaults to localhost dev.
    meeting_join_base_url: str = Field(
        default="http://localhost:3000",
        alias="MEETING_JOIN_BASE_URL",
        description="Public web-app base for meeting join links (e.g. a devtunnel URL when sharing).",
    )

    # --- Webhook ------------------------------------------------------------
    # When AegisBackend (or anything) wants LiveKit room events, point the LiveKit
    # server's webhook at THIS service's POST /webhook. We verify the signature
    # against the same api_key/secret, then forward the event to AegisBackend.
    # Leave AEGIS_WEBHOOK_URL blank to verify + log only (no forward).
    aegis_webhook_url: str = Field(
        default="",
        alias="AEGIS_WEBHOOK_URL",
        description="If set, verified LiveKit webhook events are forwarded here (e.g. http://localhost:8001/api/meeting/webhook).",
    )
    forward_timeout_s: float = Field(default=10.0, ge=1.0, le=60.0)

    # --- Transcriber (STT-only, no agent) -----------------------------------
    # A subscribe-only participant joins each meeting, transcribes every
    # participant's audio track via Deepgram, tags each line by the speaker
    # (the track's participant identity = diarization, no model needed), and
    # writes ONE transcript file when the meeting ends. Fully independent of the
    # AI agent / AegisBackend — works for 2, 3, N humans, with or without an agent.
    transcribe_auto_start: bool = Field(
        default=True,
        alias="TRANSCRIBE_AUTO_START",
        description="When true, a transcriber joins automatically the moment a meeting is scheduled.",
    )
    transcript_output_dir: str = Field(
        default="transcripts",
        alias="TRANSCRIPT_OUTPUT_DIR",
        description="Local directory where finished transcript files are written.",
    )
    transcriber_identity_prefix: str = Field(
        default="transcriber",
        description="Identity prefix for the hidden transcriber participant (kept out of speaker attribution).",
    )

    # --- Azure Speech streaming STT (reused from the phone-call stack) -------
    # Same provider + credentials AegisBackend's phone calls use, so no new key
    # is needed. The transcriber feeds each participant's PCM track into an Azure
    # PushAudioInputStream + continuous SpeechRecognizer.
    azure_speech_key: str = Field(default="", alias="AZURE_SPEECH_KEY")
    azure_speech_region: str = Field(default="eastus", alias="AZURE_SPEECH_REGION")
    azure_speech_language: str = Field(default="en-US", alias="AZURE_SPEECH_LANGUAGE")
    azure_speech_endpoint_id: str = Field(default="", alias="AZURE_SPEECH_ENDPOINT_ID")

    # --- Logging ---
    log_level: str = Field(default="INFO")
    log_pretty: bool = Field(default=True)

    # ---------------------------------------------------------------------
    def configured(self) -> bool:
        """True when the active provider has everything it needs to operate."""
        return bool(self.livekit_url and self.livekit_api_key and self.livekit_api_secret)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """One Settings instance per process. Tests clear via get_settings.cache_clear()."""
    return Settings()
