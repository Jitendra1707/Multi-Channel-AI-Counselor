"""Voice channel — REAL PSTN phone call to the lead's mobile.

Provider-agnostic. Today: Azure Communication Services (ACS). Add Twilio /
Plivo / Exotel later by dropping a file under `providers/` and flipping
`VOICE_PROVIDER` in .env — no code change in `routes.py` or `media_ws.py`.
"""
from agent_backend.channels.voice.routes import router

__all__ = ["router"]
