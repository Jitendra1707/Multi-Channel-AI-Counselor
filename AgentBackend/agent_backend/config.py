"""AgentBackend Phase 1 settings.

The surface is intentionally minimal — only what Pipecat (the one
channel implemented) and the thin streaming-LLM agent need. Memory /
tools / identity / Microsoft Graph / Bot Framework keys land in their
respective future-phase plans, not here.

Settings are loaded from `.env` via pydantic-settings. The Pydantic
class is cached via `@lru_cache` so every module sees the same
instance without re-reading the file.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Resolve `.env` to an ABSOLUTE path anchored to the project root,
# not the process CWD. Previously `env_file=".env"` would silently
# fail to load when uvicorn was launched from `agent_backend/`
# (or any directory other than the project root) — every secret
# would come back empty and Deepgram / ElevenLabs / Bot Framework
# would all 401 with no obvious cause. Anchoring to the file's
# parent.parent (this file lives in AgentBackend/agent_backend/) means
# the .env is found regardless of where `python -m uvicorn ...` is
# invoked from.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Absolute path so cwd-of-uvicorn doesn't matter.
        env_file=str(_ENV_PATH),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- HTTP server ---
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8001, ge=1, le=65535)

    # --- LLM (OpenAI-compatible streaming) ---
    # Phase 1 uses a direct streaming chat-completions call from
    # `llm_agent.agent.run_stream`. Any provider that speaks OpenAI's
    # protocol works — point `LLM_API_URL` at it.
    llm_api_url: str = Field(default="https://api.openai.com/v1")
    llm_api_key: str = Field(default="")
    llm_model: str = Field(
        default="gpt-4o-mini",
        description="Must support streaming + function calling (function calling lands in Phase 4).",
    )
    # Generation knobs — temperature controls variability, max_tokens
    # caps a single reply. Defaults match LLmLayer (0.3 temp, 200 tok)
    # which were tuned for short voice replies. Bump max_tokens for
    # chat / email channels in Phase 2 where longer answers are fine.
    llm_temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(default=200, ge=16, le=8192)
    # --- Reasoning-model knob (gpt-5.x / o-series only; ignored for gpt-4o) ---
    # Reasoning models spend hidden tokens THINKING before they emit the reply,
    # all counted against the output cap — so a 200-token cap (fine for gpt-4o)
    # starves the visible answer and produces truncated/odd output. For these
    # models we send `max_completion_tokens` floored at the value below.
    # (We intentionally do NOT set `reasoning_effort`: gpt-5.x rejects it
    # alongside function tools on /v1/chat/completions, and the agent always
    # binds tools — see llm.py.)
    llm_reasoning_max_tokens: int = Field(
        default=2048,
        ge=256,
        le=16384,
        description="Output budget (reasoning + reply) for reasoning models.",
    )
    # --- Token streaming (LLM → TTS pipelining) ---
    # When True, the brain streams its reply to TTS incrementally so synthesis
    # overlaps generation (TTS starts on the first words instead of waiting for
    # the whole reply) — big cut to time-to-first-word. The anti-hallucination
    # guard is preserved: a tool-calling message's text is still suppressed.
    # Flip to False to fall back to emitting the whole reply at once (the prior
    # behaviour) if you ever suspect a pre-tool answer is being spoken.
    enable_token_streaming: bool = Field(
        default=True, alias="ENABLE_TOKEN_STREAMING",
        description="Stream LLM tokens to TTS as generated (lower latency).",
    )

    # --- STT provider switch ---
    # Picks which factory `services.make_stt()` returns. Adding a new
    # provider = one new factory module + one new enum value here +
    # one new branch in services/__init__.py. The rest of the pipeline
    # is unchanged because every provider implements Pipecat's
    # `STTService` interface.
    voice_stt_provider: Literal["deepgram", "azure"] = Field(
        default="deepgram",
        description="Which STT backend to wire into the voice pipeline.",
    )

    # --- Deepgram STT ---
    deepgram_api_key: str = Field(default="")
    deepgram_model: str = Field(
        default="nova-3",
        description="Streaming model. nova-3 = current best quality/latency.",
    )
    # Endpointing: how long Deepgram waits inside an utterance before
    # marking a transcript final. 500ms was conservative for noisy
    # environments; 300ms is what production Twilio voice agents use
    # and saves 200ms per turn. The min_volume VAD floor above already
    # filters the noise-derived fragments that used to require the
    # longer endpoint wait.
    deepgram_endpointing_ms: int = Field(default=300, ge=100, le=2000)
    deepgram_language: str = Field(default="en-US")

    # --- Azure Speech STT ---
    # Credentials live in your Azure Speech resource:
    #   Portal → Speech service → Keys and Endpoint
    # Region is short-code (eastus, centralindia, westeurope, etc.) —
    # NOT the full hostname. Language is BCP-47 (en-US, en-IN, hi-IN,
    # etc.); must be a value in pipecat.transcriptions.language.Language.
    # endpoint_id is only used for Azure Custom Speech (org-trained
    # acoustic / language models). Leave blank for the base model.
    azure_speech_key: str = Field(default="")
    azure_speech_region: str = Field(
        default="eastus",
        description="Azure region short-code, e.g. 'eastus', 'centralindia'.",
    )
    azure_speech_language: str = Field(
        default="en-US",
        description="BCP-47 code; must exist in pipecat's Language enum.",
    )
    azure_speech_endpoint_id: str = Field(
        default="",
        description="Optional Azure Custom Speech endpoint id; leave blank for base model.",
    )
    # How long Azure's continuous recognizer waits after speech stops before
    # emitting the FINAL transcript. Azure default is 500ms; the agent turn
    # waits on this final, so 500ms adds tail latency every turn. 300ms makes
    # the final land ~when Silero declares end-of-turn. Range 100-5000; don't
    # go below ~300 (Azure gets unreliable on short pauses).
    azure_stt_segmentation_silence_ms: int = Field(default=300, ge=100, le=5000)

    # --- TTS provider switch ---
    # "elevenlabs" → cloud, paid, premium voices (current default).
    # "azure"      → Azure Speech neural voices, same Azure tenant as
    #                STT and Blob. ~10x cheaper than ElevenLabs at
    #                comparable quality; shares the Speech resource
    #                credentials (AZURE_SPEECH_KEY / AZURE_SPEECH_REGION).
    voice_tts_provider: Literal["elevenlabs", "azure", "sarvam"] = Field(
        default="elevenlabs",
        description="Which TTS backend to wire into the voice pipeline.",
    )

    # --- Azure TTS (uses azure_speech_key + azure_speech_region) ---
    # Voice name is Azure's full neural-voice id, e.g.:
    #   en-US-AriaNeural          (US English, conversational, chat-tuned)
    #   en-US-JennyNeural         (US English, natural)
    #   en-US-GuyNeural           (US English, male)
    #   en-IN-NeerjaNeural        (Indian English, female)
    #   en-IN-PrabhatNeural       (Indian English, male)
    #   en-US-JennyMultilingualNeural  (multilingual)
    # Browse the full list at https://speech.microsoft.com/portal/voicegallery.
    #
    # rate / pitch / style / style_degree are SSML knobs applied per
    # utterance. Leave style empty for neutral; set e.g. "cheerful",
    # "chat", "newscast-casual" if the voice supports the style.
    azure_tts_voice: str = Field(
        default="en-US-AriaNeural",
        description="Azure neural voice id; must match the language.",
    )
    azure_tts_language: str = Field(
        default="en-US",
        description="BCP-47 language code for the voice; must match the voice id.",
    )
    azure_tts_rate: str = Field(
        default="1.05",
        description="Speech rate. '1.0' = natural; '1.1' faster; '0.9' slower.",
    )
    azure_tts_pitch: str = Field(
        default="",
        description="Optional pitch shift, e.g. '+10%', '-5Hz'. Blank = voice default.",
    )
    azure_tts_style: str = Field(
        default="",
        description="Optional speaking style ('cheerful', 'chat', 'newscast-casual'…). Blank = neutral.",
    )
    azure_tts_style_degree: str = Field(
        default="",
        description="Style intensity '0.01'..'2.0'. Blank = use style default.",
    )

    # --- ElevenLabs TTS ---
    elevenlabs_api_key: str = Field(default="")
    elevenlabs_voice_id: str = Field(default="EXAVITQu4vr4xnSDxMaL")
    elevenlabs_model: str = Field(
        default="eleven_flash_v2_5",
        description=(
            "TTS model. eleven_flash_v2_5 (~75ms first-byte) is the latency "
            "default — production voice agents (Twilio, LiveKit voice samples) "
            "use Flash. eleven_turbo_v2_5 (~300ms) is slightly more expressive "
            "but the 225ms penalty per turn makes the bot feel laggy compared "
            "to native Realtime APIs. Override via ELEVENLABS_MODEL for higher "
            "fidelity at cost of perceived responsiveness."
        ),
    )
    # Optional voice-tuning overrides. None = use ElevenLabs's per-voice defaults
    # (which are tuned per voice id and usually right).
    elevenlabs_stability: float | None = Field(default=None, ge=0.0, le=1.0)
    elevenlabs_similarity_boost: float | None = Field(default=None, ge=0.0, le=1.0)
    elevenlabs_style: float | None = Field(default=None, ge=0.0, le=1.0)
    elevenlabs_use_speaker_boost: bool | None = Field(default=True)

    # --- Sarvam AI TTS (streaming WebSocket; Indian-language `bulbul` models) ---
    # Set VOICE_TTS_PROVIDER=sarvam to use it. Output is linear16 PCM at the
    # pinned pipeline sample rate, so it drops into the voice pipeline with no
    # resampling — latency on par with the ElevenLabs/Azure streaming paths.
    sarvam_api_key: str = Field(
        default="", description="Sarvam API subscription key (api-subscription-key)."
    )
    sarvam_tts_model: str = Field(
        default="bulbul:v2",
        description="Sarvam TTS model: 'bulbul:v2' or 'bulbul:v3'.",
    )
    sarvam_tts_speaker: str = Field(
        default="anushka",
        description="Speaker/voice id (e.g. anushka, abhilash, manisha for v2; shubh for v3).",
    )
    sarvam_tts_language: str = Field(
        default="en-IN",
        description="BCP-47 target language (en-IN, hi-IN, ta-IN, te-IN, kn-IN, ml-IN, mr-IN, …).",
    )
    sarvam_tts_pace: float = Field(
        default=1.0, ge=0.3, le=3.0,
        description="Speaking pace. 1.0 = natural; v3 range 0.5–2.0, v2 range 0.3–3.0.",
    )
    sarvam_tts_pitch: float = Field(
        default=0.0, ge=-0.75, le=0.75,
        description="Pitch shift (bulbul:v2 only; ignored by v3). 0.0 = voice default.",
    )
    sarvam_tts_loudness: float = Field(
        default=1.0, ge=0.3, le=3.0,
        description="Loudness (bulbul:v2 only; ignored by v3). 1.0 = voice default.",
    )

    # --- Canonical pipeline audio rate ---
    # Shared by every spoken channel (voice, avatar_video) and the STT/TTS
    # service factories. Pinned 16 kHz so nothing has to resample on the hot
    # path (avatar-video OUTPUT overrides to 48 kHz via avatar_audio_out_sample_rate).
    pipecat_audio_sample_rate: int = Field(default=16000)
    # Avatar-video end-of-turn silence (Silero stop_secs). The shared
    # VOICE_VAD_STOP_SECS is 0.80 — conservative so the bot doesn't cut in
    # on a pause. But avatar_video is a snappy 1-on-1
    # call, so it gets its own, tighter value (0.5s) — the biggest "feels slow"
    # lever. Industry sweet spot is 0.5-0.6 (LiveKit/Vapi); below 0.5 risks
    # chopping natural mid-sentence pauses.
    avatar_vad_stop_secs: float = Field(default=0.5, ge=0.2, le=2.0)

    # Minimum words the user must say before they can interrupt (barge-in) the
    # speaking avatar. Guards against the avatar cutting itself off when its own
    # voice echoes into the mic and trips VAD. 3 is the production sweet spot:
    # a stray echo blip (0-2 words of garbage) can't interrupt, but a real
    # "stop, I have a question" does. Set 0 to allow instant interruption.
    avatar_interrupt_min_words: int = Field(default=3, ge=0, le=20)

    # Avatar-video (SmallWebRTC) output sample rate. WebRTC is natively 48 kHz
    # and Simli emits 48 kHz avatar audio. Pinning the OUTPUT to 16 kHz forces a
    # lossy 48k→16k→48k round-trip → choppy/metallic audio. Setting the output
    # to 48 kHz removes that round-trip. INPUT stays 16 kHz (Azure STT + Silero
    # are tuned for it and SmallWebRTC down-samples mic input for us). This knob
    # is avatar-video ONLY — every other spoken path stays pinned 16k e2e.
    avatar_audio_out_sample_rate: int = Field(default=48000)

    # --- Avatar-video human-simulation features (DEFAULT ON) ---
    # These are the avatar channel's OWN flags — fully independent of the voice
    # channel's ENABLE_* flags. Each gates one processor (or background task) in
    # the avatar pipeline; they default ON so the avatar gets full human
    # simulation out of the box, and can be flipped off per-flag if a behavior
    # misbehaves in production. With ALL of them OFF the avatar graph is
    # bit-identical to the previous baseline (input→STT→sink→bridge→TTS→Simli→out).
    avatar_enable_streaming_optimizations: bool = Field(
        default=False,
        description=(
            "SentenceStreamer: coalesce brain tokens into sentence TextFrames. "
            "DEFAULT OFF to match the voice channel (enable_streaming_optimizations=False) "
            "and minimise first-audio latency. When ON, the streamer buffers the WHOLE "
            "first sentence before TTS/Simli gets any audio — the brain streams tokens "
            "fast but the avatar stays silent until the sentence completes, which is the "
            "'LLM is generating but nothing is spoken yet' mid-layer delay. OFF streams "
            "tokens straight to TTS exactly like voice (fastest start). Turn ON only if "
            "you'd trade ~one-sentence of start latency for slightly smoother prosody."
        ),
    )
    avatar_enable_turn_detector: bool = Field(
        default=True,
        description="Multi-signal turn-state FSM + watchdog feeding the avatar event bus.",
    )
    avatar_enable_barge_in_manager: bool = Field(
        default=True,
        description="5-intent barge classifier + stop-fast/answer-final state machine. Owns interruption (replaces MinWordsInterruptionStrategy).",
    )
    avatar_enable_silence_manager: bool = Field(
        default=True,
        description="T2/T3/T4 silence re-engagement monitor (background task).",
    )
    avatar_enable_silence_responder: bool = Field(
        default=True,
        description="On T2/T3/T4 silence events, the brain emits a human-feel re-engagement utterance.",
    )
    avatar_enable_metrics: bool = Field(
        default=False,
        description="Per-stage latency stamper + Prometheus sink. OFF in prod (per-frame overhead); flip on to profile.",
    )
    avatar_speak_opener: bool = Field(
        default=True,
        description="Bot-speaks-first: the avatar greets on WebRTC connect, before the user speaks.",
    )
    # Warm-up delay before the opening greeting is spoken. On StartFrame the
    # WebRTC/ICE handshake + Simli's send buffer + the output transport are
    # still priming; speaking immediately clips the first words of the greeting.
    # ~1.2s lets the whole path go live first (also feels natural — a beat before
    # speaking). Bump if the greeting still clips; lower toward 0 to test.
    avatar_opener_warmup_s: float = Field(default=1.2, ge=0.0, le=5.0)

    # --- Avatar silence/presence thresholds (only used if silence manager on) ---
    # Tuned for human thinking time on a 1-on-1 video call. Adaptive bonus
    # (silence_monitor.py) ADDS to these when the avatar just gave a long reply
    # or asked a question. Independent of the voice channel's silence_t*_s.
    # Cadence: 3 escalating check-ins ~6s apart, then a graceful goodbye+hangup.
    # Times are measured from when the AVATAR stopped speaking (Simli SILENT) and
    # the user stayed quiet. The adaptive bonus is applied ONCE (to T2, shifting
    # all later steps by the same amount) so the SPACING between check-ins stays
    # a steady ~6s even after a long reply / question — see _compute_adaptive_bonus.
    # Cadence is RELAXED for the director-briefing presenter: a briefing is not a
    # sales call — the director needs time to read the chart and think before
    # Aisha checks in. First nudge ~9s, then ~12s gaps, graceful close ~45s.
    # (Times measured from when the avatar stopped speaking AND the user stayed
    # quiet; the per-nudge clock restart in silence_monitor guarantees the gap.)
    avatar_silence_t2_s: float = Field(default=9.0,  description="check-in #1 — first unhurried nudge after ~9s of silence (effective = 9 + adaptive bonus).")
    avatar_silence_t3_s: float = Field(default=21.0, description="check-in #2 — ~12s after #1 (effective = 21 + adaptive bonus).")
    avatar_silence_t4_s: float = Field(default=33.0, description="check-in #3 — ~12s after #2, softer (effective = 33 + adaptive bonus).")
    avatar_silence_t5_s: float = Field(default=45.0, description="graceful close + end_call ~12s after #3 (effective = 45 + adaptive bonus). ~45s total silence ends the briefing politely.")

    # --- Avatar turn-detector tunables ---
    avatar_turn_brief_pause_ms:    int   = Field(default=200,  description="VAD silence under this = mid-thought")
    avatar_turn_complete_ms:       int   = Field(default=700,  description="VAD silence over this = turn done")
    avatar_turn_abandoned_s:       float = Field(default=4.0,  description="silence after partial → abandoned")
    avatar_turn_confidence_floor:  float = Field(default=0.6,  description="min confidence to publish state change")

    # --- Addressee gate (voice channel) ---
    # When True, the bot only responds when it hears its name (fuzzy
    # match against persona.name + persona.aliases) OR an imperative
    # command verb at the start of the turn. Stops the bot from
    # cutting in on side-conversations between humans in the meeting.
    # Set to False for 1:1 demo mode where every transcript should
    # reach the brain.
    voice_require_address: bool = Field(
        default=True,
        description="If True, voice channel ignores transcripts that don't address the bot.",
    )

    # --- Silero VAD (latency-tuned for snappy turn-taking) ---
    # Tuned to balance snappy turn-taking against false turn-ends:
    #
    #   start_secs=0.10  — react to speech start immediately
    #   stop_secs=0.60   — was 0.80, dropped to match LiveKit voice-agent
    #                       defaults. Pairs with the `min_volume=0.60`
    #                       floor below which already filters noise that
    #                       used to false-trigger turn-end on the
    #                       previous 0.6 setting. Saves ~200ms per turn.
    #   confidence=0.65  — Silero's posterior threshold
    #   min_volume=0.60  — RMS floor; below this we ignore VAD even if
    #                       the posterior is high (cuts noise-triggered
    #                       phantom transcripts)
    voice_vad_start_secs: float = Field(default=0.10, ge=0.05)
    voice_vad_stop_secs: float = Field(default=0.60, ge=0.1)
    voice_vad_confidence: float = Field(default=0.65, ge=0.0, le=1.0)
    voice_vad_min_volume: float = Field(default=0.60, ge=0.0, le=1.0)

    # --- Identity / persona ---
    # The active persona's JSON file lives at
    # `agent_backend/llm_agent/identity/<identity_name>.json`. On boot
    # the FastAPI lifespan hydrates this file from Azure Blob Storage
    # using `connection_string` + `container_name` + `folder_path`,
    # mirroring how Node's avatar-fetcher.ts and LLmLayer's identity
    # fetcher work. After hydration the loader reads the JSON with an
    # lru_cache and feeds it into every system prompt as the WHO YOU
    # ARE block.
    identity_name: str = Field(
        default="aisha-counselor",
        description=(
            "Stem (without .json) of the active persona file. ONE persona drives "
            "the single brain across every channel — the use case (counsellor, "
            "support, etc.) is tuned here, not in code."
        ),
    )
    identity_force_refresh: bool = Field(
        default=False,
        description="True → re-download from blob on every boot even if local file exists.",
    )
    # The avatar_video channel runs a DIFFERENT persona from the voice/whatsapp
    # counsellor: it joins a meeting to brief the director on outreach analytics.
    # Persona is resolved per-channel (see agent._render_persona) — avatar_video
    # uses this name, every other channel uses `identity_name` above.
    avatar_identity_name: str = Field(
        default="director-briefing",
        alias="AVATAR_IDENTITY_NAME",
        description=(
            "Stem (without .json) of the persona for the avatar_video channel — "
            "the director-briefing analytics presenter, distinct from the "
            "counsellor persona used on voice/whatsapp."
        ),
    )

    # --- RAG backend switch (counsellor knowledge base) ---
    # "legacy" → original float32 dense + BM25 + RRF retriever.
    # "turbo"  → TurboQuant-quantized backend (agent_backend.rag_turbo).
    # Honoured by rag_router. Defining it as a real setting is what makes the
    # .env value take effect (rag_router reading os.environ alone misses
    # pydantic-loaded .env values).
    rag_backend: Literal["legacy", "turbo"] = Field(
        default="legacy", alias="RAG_BACKEND"
    )

    # --- Azure Blob Storage (shared with Node side; lowercase by
    # operator convention) ---
    # Used by the identity fetcher to download <identity_name>.json
    # from <container_name>/<folder_path>/. Leave blank to skip
    # hydration and rely on a local file already in the identity
    # bundle directory.
    connection_string: str = Field(default="")
    container_name: str = Field(default="")
    folder_path: str = Field(default="")

    # --- Episodic memory (Phase 1.6a) ---
    # Append-only timeline of conversation turns — lives in RAM, NOT in the
    # system prompt. Phase 1.6c's BM25 retriever queries this store
    # on demand and surfaces hits in a RECALL prompt slot. Bound the
    # size to cap memory footprint: 2000 records ≈ 60-90 minutes of
    # a moderately active conversation (~10 voice turns/min). Above this
    # the oldest records drop out of the ring buffer.
    episodic_max_records: int = Field(default=2000, ge=100, le=50000)

    # =====================================================================
    # CONVERSATIONAL PRODUCT DATA (channels: voice / whatsapp / chat / ...)
    # ---------------------------------------------------------------------
    # One brain, one model (`llm_model` / `llm_max_tokens` above), one persona
    # (`identity_name` above). Everything below is data the persona-driven brain
    # reads — leads, knowledge, telephony — not a second brain.
    # =====================================================================

    # --- Test data (JSON-backed for now; Postgres later) ---
    leads_file: str = Field(
        default=str(_PROJECT_ROOT / "test-data" / "leads.json"),
        description="Path to leads.json — used by data.LeadRepo.",
    )

    # --- Document catalog (the "resource manager") ---
    # doc_key → {title, url (public Blob/SharePoint), description, keywords,
    # template}. Used by the WhatsApp send endpoint + the live send_document tool
    # to resolve a requested document to a URL (and its approved template for
    # out-of-window sends). Hot-reloaded by data.documents on change.
    documents_file: str = Field(
        default=str(_PROJECT_ROOT / "test-data" / "documents.json"),
        description="Path to documents.json — the sendable-document catalog.",
    )

    # --- Approved WhatsApp template registry ---
    # template_key → {name (EXACT approved Meta name), languages (lead-pref →
    # BCP-47 code), params (ordered body {{1}}..{{n}} field names), header}.
    # Decoupled from the document catalog so a template can be reused across
    # documents OR sent standalone (e.g. a missed-call / outreach template that
    # has no document). Hot-reloaded by data.templates on change.
    templates_file: str = Field(
        default=str(_PROJECT_ROOT / "test-data" / "templates.json"),
        description="Path to templates.json — the approved WhatsApp template registry.",
    )

    # --- Missed-call WhatsApp outreach ---
    # When an OUTBOUND call ends unanswered (busy / rejected / no-answer), the
    # Plivo hangup callback fires the registered `outreach` template at the
    # candidate so they still hear from us. Approved templates deliver in OR out
    # of the 24h window, so this works for a cold number. Consent
    # (consent_whatsapp) is checked against the BusinessLayer; unknown numbers
    # are skipped. The cooldown stops a redial burst from spamming WhatsApp.
    missed_call_outreach_enabled: bool = Field(
        default=True,
        description="Send a WhatsApp template when an outbound call goes unanswered.",
    )
    missed_call_outreach_template_key: str = Field(
        default="outreach",
        description="templates.json key of the approved template used for missed-call outreach.",
    )
    missed_call_outreach_cooldown_minutes: int = Field(
        default=360,
        ge=1,
        description="Minimum minutes between missed-call outreach messages to the same number.",
    )
    missed_call_retry_minutes: int = Field(
        default=1440,
        ge=1,
        description="After a missed outbound call, schedule the lead's next dial attempt this many minutes out (via the BusinessLayer).",
    )

    # --- University knowledge ---
    # University facts now come SOLELY from the RAG knowledge base
    # (knowledge-base/, see agent_backend.rag) — there is no university.json.
    # This is just the spoken name used in the bot-speaks-first opener template;
    # everything else is retrieved from the KB.
    university_short_name: str = Field(
        default="Sreenidhi University",
        description="University name spoken in the call opener (TTS-friendly, no parens).",
    )

    # --- Campus visit (confirmation email / WhatsApp) ---
    # Defaults rendered into the campus-visit confirmation email + WhatsApp.
    # Override per-deployment in .env.
    campus_address: str = Field(
        default="Sreenidhi University, Yamnampet, Ghatkesar, Hyderabad, Telangana 501301",
        alias="CAMPUS_ADDRESS",
        description="Postal address shown on the campus-visit confirmation.",
    )
    campus_visit_contact: str = Field(
        default="+91 77999 56801",
        alias="CAMPUS_VISIT_CONTACT",
        description="Phone number the candidate can call about their visit.",
    )
    campus_map_url: str = Field(
        default="https://www.google.com/maps/search/?api=1&query=Sreenidhi+University+Hyderabad",
        alias="CAMPUS_MAP_URL",
        description="Maps link for the 'Get directions' button on the visit email.",
    )
    campus_visit_qr_pass: bool = Field(
        default=True,
        alias="CAMPUS_VISIT_QR_PASS",
        description="Embed a per-visitor QR 'campus entry pass' in the visit confirmation email (shown to security at the gate). Set false to disable.",
    )

    # --- Voice channel (real PSTN phone call) ---
    voice_provider: str = Field(
        default="acs",
        description="Telephony provider: 'acs' | 'twilio' | 'plivo' | 'exotel'.",
    )
    public_base_url: str = Field(
        default="",
        description="Public HTTPS base (e.g. ngrok tunnel) ACS can reach us on.",
    )
    # ACS Call Automation (provider 'acs').
    acs_connection_string: str = Field(default="", alias="ACS_CONNECTION_STRING")
    acs_from_number: str = Field(default="", alias="ACS_FROM_NUMBER")

    # Plivo Voice API (provider 'plivo'). Answer-URL driven; media streams
    # μ-law 8 kHz over a <Stream> WebSocket. Additive to ACS — only used when
    # VOICE_PROVIDER=plivo. PUBLIC_BASE_URL (above) is reused for the answer +
    # media-stream URLs Plivo calls back on.
    plivo_auth_id: str = Field(default="", alias="PLIVO_AUTH_ID")
    plivo_auth_token: str = Field(default="", alias="PLIVO_AUTH_TOKEN")
    plivo_from_number: str = Field(default="", alias="PLIVO_FROM_NUMBER")

    # --- BusinessLayer integration (additive; OFF unless URL is set) ---
    # When BUSINESS_LAYER_URL is set, the conversation engine (a) fetches a
    # lead's memory bundle (facts/summary/open-concerns) at session start and
    # overlays it onto the prompt's LEAD PROFILE slot, and (b) pushes session
    # lifecycle + transcript to the BusinessLayer so it can run post-call
    # analysis. EVERY such call is best-effort with a short timeout — if the
    # BusinessLayer is down or the URL is empty, the call/chat behaves exactly
    # as it does today (no behavioural change, no added latency on failure).
    business_layer_url: str = Field(default="", alias="BUSINESS_LAYER_URL")
    business_timeout_s: float = Field(
        default=5.0,
        ge=0.5,
        le=30.0,
        description="Per-request timeout for BusinessLayer calls; kept short so a slow/down service never stalls a live call.",
    )

    # --- WhatsApp channel (Plivo WhatsApp Business API) ---
    # Reuses PLIVO_AUTH_ID / PLIVO_AUTH_TOKEN above (same Plivo account as voice).
    # PLIVO_WHATSAPP_FROM is your Plivo WhatsApp-enabled sender number (the WABA
    # number). The channel mounts WITHOUT this set — inbound still 200s and
    # outbound sends are disabled with a clear warning (same graceful-degrade
    # pattern as the voice channel booting without a from-number).
    # Inbound: set this URL as the number's Message URL in the Plivo console →
    #   <PUBLIC_BASE_URL>/channels/whatsapp/inbound   (HTTP POST)
    plivo_whatsapp_from: str = Field(
        default="",
        alias="PLIVO_WHATSAPP_FROM",
        description="Plivo WhatsApp-enabled sender number (WABA number), e.g. +91XXXXXXXXXX.",
    )

    # --- Production-grade voice features (all default OFF — additive only) ---
    # Each flag gates an independent processor in the voice pipeline; with
    # every flag false the pipeline is bit-identical to baseline behaviour.
    enable_streaming_optimizations: bool = Field(default=False)
    enable_turn_detector:           bool = Field(default=False)
    enable_barge_in_manager:        bool = Field(default=False)
    enable_silence_manager:         bool = Field(default=False)
    enable_silence_responder:       bool = Field(
        default=False,
        description="On T2/T3/T4 silence events, brain emits a human-feel re-engagement utterance.",
    )
    # Backchannel filter + bot-acknowledgement flags REMOVED in the
    # barge-classifier refactor. The new BargeInManager classifies user
    # input as ACK / ANSWER / INTERRUPT / CONFUSED / AMBIGUOUS using a
    # heuristic + (for AMBIGUOUS only) an async LLM call running in
    # parallel with bot speech. No more vocab-list patching.
    enable_metrics:                 bool = Field(default=False)

    # --- Silence/presence thresholds (only used if silence manager on) ---
    # Baseline thresholds — tuned for human thinking time, not robot
    # impatience. Bumped from 5/10/20 → 8/15/25 after observing the 5 s T2
    # firing "still there?" mid-thought on a 67-word explanation with a yes/no
    # ask. Production voice agents (Retell, Vapi) use 6–8 s as the floor for
    # the first re-engagement. Adaptive bonus (silence_monitor.py) ADDS to
    # these when the bot just gave a long reply or asked a question — long
    # explanations get even more thinking time.
    silence_t1_s: float = Field(default=2.0,  description="<= no action")
    silence_t2_s: float = Field(
        default=8.0,
        description=(
            "First probe (effective = 8 + adaptive bonus). "
            "SKIPPED ENTIRELY when engagement_pending — the user signalled "
            "they're tracking, so the bot stays quiet through T2."
        ),
    )
    silence_t3_s: float = Field(
        default=18.0,
        description=(
            "Re-engagement nudge (effective = 18 + adaptive bonus). With "
            "engagement_bonus +15, this becomes the FIRST check-in at "
            "~36 s for a typical bot question. Without engagement, fires "
            "as a stronger follow-up after T2 already fired."
        ),
    )
    silence_t4_s: float = Field(default=25.0, description="graceful termination (effective = 25 + adaptive bonus)")
    silence_termination_grace_s: float = Field(
        default=6.0,
        description="seconds between T4 close-line and actual hangup",
    )

    # --- Turn-detector tunables ---
    turn_brief_pause_ms:    int   = Field(default=200,  description="VAD silence under this = mid-thought")
    turn_complete_ms:       int   = Field(default=700,  description="VAD silence over this = turn done")
    turn_abandoned_s:       float = Field(default=4.0,  description="silence after partial → abandoned")
    turn_confidence_floor:  float = Field(default=0.6,  description="min confidence to publish state change")

    # --- Barge-in tunables ---
    # The old vocab-based tunables (barge_confirmation_ms,
    # barge_quiet_period_ms, barge_backchannel_wait_ms) are GONE — the new
    # BargeClassifier-based manager doesn't have a fixed confirmation
    # window; it decides per-intent the moment the transcript arrives, and
    # uses an async LLM (capped at 700 ms inside the manager) for the
    # AMBIGUOUS bucket only. See processors/barge_classifier.py.
    #
    # The one knob that remains is the echo correlation threshold used by
    # the acoustic gate — kept as a config to allow per-deployment tuning
    # on noisy lines.
    barge_echo_corr_threshold: float = Field(
        default=0.35,
        description=(
            "Acoustic gate — if user RMS / TTS-peak RMS < this, treat the "
            "VAD trip as echo / noise (suppress, bot keeps speaking). "
            "Lowered from 0.7 → 0.35 because the prior value rejected most "
            "real interruptions on hands-free phones."
        ),
    )

    # --- Director analytics viz model ---
    # A small/fast model that turns the director's question + the aggregated
    # stats slice into a constrained UiDirective (chart/report spec) via OpenAI
    # structured outputs. Separate from the conversational brain so the spec call
    # stays cheap and fast. gpt-4o-mini supports structured outputs reliably.
    analytics_viz_model: str = Field(
        default="gpt-4o-mini",
        alias="ANALYTICS_VIZ_MODEL",
        description="Model for the present_analytics chart-spec step (structured output).",
    )

    # --- Knowledge capture (director states a new fact in a video call) ---
    # The avatar_video channel can capture a fact the director states, run a
    # contradiction check against the KB, surface it for approval, and on approval
    # ingest it (and use it live). All additive + gated.
    knowledge_capture_enabled: bool = Field(
        default=True, alias="KNOWLEDGE_CAPTURE_ENABLED",
        description="Master switch for in-call knowledge capture (explicit trigger).",
    )
    knowledge_autodetect: bool = Field(
        default=False, alias="KNOWLEDGE_AUTODETECT",
        description=(
            "Auto-detect new facts on EVERY director utterance via an async LLM "
            "classifier. OFF by default (explicit Capture button only) — flip on to "
            "enable per-utterance detection (1 cheap LLM call/utterance, off the hot path)."
        ),
    )
    knowledge_detect_model: str = Field(
        default="gpt-4o-mini", alias="KNOWLEDGE_DETECT_MODEL",
        description="Cheap model for the detect/extract step (structured output).",
    )
    knowledge_judge_model: str = Field(
        default="gpt-4o-mini", alias="KNOWLEDGE_JUDGE_MODEL",
        description="Cheap model for the contradiction LLM-as-judge step (structured output).",
    )
    knowledge_confidence_min: float = Field(
        default=0.6, ge=0.0, le=1.0, alias="KNOWLEDGE_CONFIDENCE_MIN",
        description="Drop extracted candidates below this confidence.",
    )
    knowledge_conflict_block_threshold: int = Field(
        default=20, ge=0, le=100, alias="KNOWLEDGE_CONFLICT_BLOCK_THRESHOLD",
        description="conflict_score above this shows the UI warning chip (matches FE >20).",
    )
    knowledge_neighbors_k: int = Field(
        default=8, ge=1, le=32, alias="KNOWLEDGE_NEIGHBORS_K",
        description="How many KB neighbors to retrieve for the contradiction check.",
    )
    knowledge_wake_phrases: str = Field(
        default="remember that,note that,capture that,add to knowledge",
        alias="KNOWLEDGE_WAKE_PHRASES",
        description="Comma-separated phrases that trigger an explicit capture from director speech.",
    )

    # --- Outreach analytics source (the data behind present_analytics) ---
    # "json"  → read aggregated per-month figures from ANALYTICS_STATS_FILE (default;
    #           the JSON is re-read per call, so editing it needs no restart).
    # "dummy" → the hardcoded DummyStatsProvider (legacy demo numbers).
    analytics_provider: Literal["json", "dummy"] = Field(
        default="json",
        alias="ANALYTICS_PROVIDER",
        description="Where the director's outreach stats come from: json file or hardcoded dummy.",
    )
    analytics_stats_file: str = Field(
        default=str(_PROJECT_ROOT / "test-data" / "outreach_stats.json"),
        alias="ANALYTICS_STATS_FILE",
        description="Path to the per-month outreach stats JSON (used when ANALYTICS_PROVIDER=json).",
    )

    # --- Avatar video channel (Tavus + Daily.co) ---
    # --- Avatar video channel (Simli avatar + SmallWebRTC transport) ---
    # The browser connects to THIS backend via WebRTC (aiortc, pure-Python —
    # works on Windows/Linux/macOS). Signalling is a single HTTP endpoint
    # (POST /api/avatar_video/offer), so there is NO per-session TCP port to
    # bind and therefore no port-collision race.
    #
    # Pipeline:
    #   SmallWebRTC.input() → STT → AgentBridge(llm_agent) → TTS
    #     → SimliVideoService → SmallWebRTC.output()
    # Simli generates the lip-synced avatar video; those frames flow THROUGH
    # the pipeline and are streamed to the browser over the same WebRTC peer.
    simli_api_key: str = Field(default="", alias="SIMLI_API_KEY")
    simli_face_id: str = Field(
        default="",
        alias="SIMLI_FACE_ID",
        description="Simli face/avatar ID. Trinity avatars: use 'faceId/emotionId'.",
    )
    simli_max_session_length: int = Field(
        default=1800,
        description="Absolute max session length in seconds; Simli auto-disconnects after this.",
    )
    simli_max_idle_time: int = Field(
        default=300,
        description="Max idle (avatar not speaking) time in seconds before Simli disconnects.",
    )
    simli_is_trinity_avatar: bool = Field(
        default=False,
        description="Set True for Trinity avatars to enable low-latency playImmediate() path.",
    )
    # Avatar video output resolution streamed to the browser. Simli's standard
    # faces render at 512×512; Trinity avatars may differ. The WebRTC live
    # video track adapts to the actual frame size, so these are nominal.
    avatar_video_width: int = Field(default=512)
    avatar_video_height: int = Field(default=512)
    # Simli's REAL output framerate. The WebRTC output video track must be paced
    # at this exact rate or the avatar's lips run ahead of the audio (drift grows
    # over the utterance). aiortc defaults the track to 30fps; Simli commonly
    # delivers ~25fps. Set this to the value the backend logs as
    # "MEASURED Simli video FPS ≈ N" on the first call. Both Pipecat's live pacer
    # (video_out_framerate) and the aiortc track timestamp are driven from this.
    avatar_video_fps: int = Field(default=25, ge=10, le=60)
    # Output audio buffer depth in 10ms units (pipecat default 4 = 40ms). Higher
    # = more slack against underruns (the WebRTC audio track emits silence if its
    # queue drains between fills). 15 = 150ms — chosen over 100ms because Windows
    # scheduler jitter is coarser; the extra 50ms is cheap insurance against rare
    # residual dropouts and pairs with the browser-side jitterBufferTarget=200ms.
    # The primary jitter remedy is the RECEIVER buffer (browser), not this.
    avatar_audio_out_10ms_chunks: int = Field(default=15, ge=2, le=50)
    # Master switch for the custom video-FPS pacing patch (av_sync.py). When True
    # it overrides aiortc's stock 30fps timestamp clock to pace video at Simli's
    # REAL fps (avatar_video_fps) — fixes the "lips run ahead, audio lags" drift.
    # Now that the blank-video issue is traced to the FE remount (fixed), this is
    # safe to enable. Confirm the measured FPS from the backend's
    # "MEASURED Simli video FPS ≈ N" log and set AVATAR_VIDEO_FPS to match.
    avatar_pace_video: bool = Field(default=True)
    # Comma-separated STUN/TURN server URLs for the browser↔backend WebRTC.
    # Default is Google's public STUN. Behind strict NAT, add a TURN server.
    webrtc_ice_servers: str = Field(
        default="stun:stun.l.google.com:19302",
        alias="WEBRTC_ICE_SERVERS",
        description="Comma-separated STUN/TURN URLs for SmallWebRTC connections.",
    )

    # --- Meeting channel (LiveKit room: counsellor + candidate + listening agent) ---
    # A virtual counselling meeting hosted on LiveKit. Two HUMANS join from the
    # browser (counsellor + candidate); the AgentBackend agent joins SERVER-SIDE
    # as a third participant via Pipecat's LiveKitTransport. It listens to the
    # whole conversation, answers/suggests ONLY when addressed by name (the
    # addressee gate, same as the Teams meeting bridge), and on meeting end flushes
    # the speaker-tagged transcript to the BusinessLayer for DUAL analysis
    # (candidate + counsellor).
    #
    # LiveKit Cloud: create a project at cloud.livekit.io → copy the wss:// URL +
    # API key/secret. Self-host later by pointing LIVEKIT_URL at your own server;
    # nothing else changes. With LIVEKIT_URL unset the channel mounts but
    # /schedule returns 503 (same graceful-degrade as the other outbound channels).
    livekit_url: str = Field(
        default="",
        alias="LIVEKIT_URL",
        description="LiveKit server URL, e.g. wss://<project>.livekit.cloud (Cloud) or ws://host:7880 (self-host).",
    )
    livekit_api_key: str = Field(default="", alias="LIVEKIT_API_KEY")
    livekit_api_secret: str = Field(default="", alias="LIVEKIT_API_SECRET")
    # Identity the agent uses when it joins the room. Kept distinct from the two
    # human identities so per-speaker attribution never confuses the agent's own
    # (published) audio with a participant. The agent's mic track is its TTS.
    livekit_agent_identity: str = Field(
        default="aegis-agent",
        description="Participant identity the listening agent joins the room under.",
    )
    # Seconds LiveKit keeps an empty room alive before reaping it. Should be ≥
    # MEETING_AGENT_WAIT_S so the SFU doesn't reap a room while the agent is still
    # holding it open for a not-yet-arrived candidate (Flow B).
    livekit_room_empty_timeout_s: int = Field(default=600, ge=30, le=3600)
    # Hard cap on participants per meeting room. The agent is ONE entity; the
    # humans are unbounded for our use (a group counselling call can have several
    # participants), so this is generous. LiveKit handles dozens fine.
    livekit_room_max_participants: int = Field(default=50, ge=2, le=200)
    # JWT lifetime for the join tokens handed to the browser. Long enough to
    # cover a scheduled-but-not-yet-started meeting plus its duration.
    livekit_token_ttl_s: int = Field(default=14400, ge=300, le=86400)
    # Public base URL of the web-app the join links point at. The /schedule
    # endpoint builds <MEETING_JOIN_BASE_URL>/meeting/<room>?token=<jwt>&role=...
    # Falls back to the first FRONTEND_URLS origin when unset.
    meeting_join_base_url: str = Field(
        default="",
        alias="MEETING_JOIN_BASE_URL",
        description="Public web-app base for meeting join links; defaults to the first FRONTEND_URLS origin.",
    )
    # Whether the meeting agent must be addressed by name before it answers.
    # DEFAULT FALSE: the meeting agent is a warm, ACTIVE career counsellor who
    # answers EVERY turn (solo or panel) — it should never make the student say
    # its name before each question, and it should never sit silent. Set True
    # only for the legacy "quiet co-pilot that waits to be named in a multi-party
    # room" behaviour (the panel gate then applies when 2+ humans are present).
    meeting_require_address: bool = Field(
        default=False,
        alias="MEETING_REQUIRE_ADDRESS",
        description="If True, the meeting agent only answers turns that address it by name (legacy quiet co-pilot). Default False = active counsellor that answers everything.",
    )
    # Optional one-line consent notice the agent speaks once when the first human
    # joins (recording / AI-presence disclosure). Blank = say nothing on join.
    meeting_consent_line: str = Field(
        default="",
        description="Spoken once on first-participant-join, e.g. an AI-presence / recording disclosure. Blank = silent join.",
    )
    # End-of-turn silence (Silero stop_secs) for the meeting agent. Kept ~= the
    # Azure segmentation window (meeting_stt_segmentation_silence_ms) so the VAD
    # and Azure declare end-of-turn together. LATENCY-TUNED to 0.6s: this is the
    # dead air after you stop talking before the turn finalises and the LLM
    # starts — the single biggest "feels slow" lever. 0.6s feels human on a
    # headset/laptop mic; if a speaker pauses >0.6s mid-sentence the turn may cut
    # early, but the bridge's supersede logic answers the newer fuller transcript,
    # so it self-corrects. Raise toward 0.8-1.2 for far-field/noisy rooms.
    meeting_vad_stop_secs: float = Field(default=0.6, ge=0.2, le=2.0)
    # Meeting-specific VAD sensitivity. The phone (voice) VAD is tuned for a
    # close handset mic (min_volume 0.6); a meeting is a laptop/room mic farther
    # from the speaker, so the same gate clips quieter speech and feeds fragments
    # to STT (an accuracy hit on top of the per-speaker routing fix). These default
    # MORE PERMISSIVE so genuine speech isn't dropped; raise them if a noisy room
    # triggers the agent on background sound. Separate from voice_* so the phone
    # path is untouched.
    meeting_vad_confidence: float = Field(
        default=0.55, ge=0.0, le=1.0, alias="MEETING_VAD_CONFIDENCE"
    )
    meeting_vad_min_volume: float = Field(
        default=0.35, ge=0.0, le=1.0, alias="MEETING_VAD_MIN_VOLUME"
    )
    # Azure end-of-phrase silence for the MEETING STT (ms). LATENCY-TUNED to 600:
    # this is how long Azure waits for silence before finalising your transcript,
    # so it directly adds to turn latency. The phone default (300) chops far-field
    # speech into homophone fragments; 1200 was accurate but added 1.2s of dead
    # air per turn. 600 is the balance — accurate enough on a headset/laptop mic
    # while halving the wait. Raise toward 800-1200 if speech is being chopped in
    # a noisy/far-field room; lower toward 400 for the snappiest 1:1 turns.
    meeting_stt_segmentation_silence_ms: int = Field(
        default=600, ge=200, le=2000, alias="MEETING_STT_SEGMENTATION_SILENCE_MS"
    )
    # Active-speaker gating (multi-party only). In a room, each participant's mic
    # ALSO picks up the OTHER people (acoustic bleed / shared room / speakers), so
    # every per-speaker recognizer hears a faint copy of the others and mis-
    # transcribes ("Aisha"→"Sir"). When ON, the router feeds a speaker's audio to
    # their recognizer ONLY while they are the DOMINANT (loudest) active track,
    # dropping the quieter bleed. No effect with a single speaker. Ratio = how
    # much louder the dominant track must be than a contender to win (0.55 = the
    # loser must be below 55% of the leader's energy to be dropped).
    # ON by default. ROOT CAUSE of N-humans→N-transcripts: each browser also
    # captures the played-back audio of the active speaker (imperfect AEC + remote
    # audio on the default speaker), so EVERY mic carries a ~full-energy copy and
    # all N recognizers transcribe the one utterance. This gate feeds STT only for
    # the SINGLE clearly-dominant track (>= factor × loudest other, AND above the
    # absolute floor), so the near-equal re-captured copies are dropped. No-op for
    # a single speaker (gate only engages with 2+ recognizers).
    # OFF: energy-dominance can't separate a re-captured copy from the original —
    # they're near-equal energy, so any margin either drops the real speaker or
    # lets the copy through (no safe value). The reliable fix is content-level
    # transcript dedup (meeting_dedup_*) below, which catches the duplicate by its
    # near-identical TEXT regardless of energy. Left as an opt-in experiment.
    meeting_active_speaker_gate: bool = Field(
        default=False, alias="MEETING_ACTIVE_SPEAKER_GATE"
    )
    # Dominance margin: a track must be at least this many× the loudest OTHER
    # track to be fed. MUST be > 1.0 — at 1.0 two equal-energy copies both pass
    # (the original bug). Raise toward 1.5 if duplicates still slip; lower toward
    # 1.1 if a slightly-quieter real speaker is being dropped in fast exchanges.
    meeting_active_speaker_factor: float = Field(
        default=1.25, ge=1.0, le=4.0, alias="MEETING_ACTIVE_SPEAKER_FACTOR"
    )
    # Absolute speech floor (PCM16 RMS): tracks below this are treated as silence/
    # comfort-noise and never fed. Tune against the "[meeting-stt] inbound audio
    # format" + RMS values in the logs if speech is wrongly dropped or noise leaks.
    meeting_active_speaker_floor: float = Field(
        default=30.0, ge=0.0, le=5000.0, alias="MEETING_ACTIVE_SPEAKER_FLOOR"
    )
    # Cross-speaker transcript DEDUP (the real multi-party fix). Each participant's
    # mic re-captures the active speaker, so N recognizers emit N near-identical
    # transcripts of one utterance, each stamped a different speaker. When a final
    # transcript is >= this similarity to one another speaker produced within the
    # window, it's a duplicate → drop it (keep the FIRST = the true speaker's mic,
    # which fires earliest as the closest/loudest). Set the window in seconds.
    meeting_dedup_enabled: bool = Field(default=True, alias="MEETING_DEDUP_ENABLED")
    meeting_dedup_similarity: float = Field(
        default=0.72, ge=0.0, le=1.0, alias="MEETING_DEDUP_SIMILARITY"
    )
    meeting_dedup_window_s: float = Field(
        default=4.0, ge=0.5, le=15.0, alias="MEETING_DEDUP_WINDOW_S"
    )

    # ── SFU server-side active-speaker STT gate (PRODUCTION multi-party fix) ──
    # The RMS gate above FAILED: a re-captured copy has ~= the original's energy,
    # so no margin separates them. The real fix gates STT on LiveKit's server-side
    # active_speakers_changed signal (computed at INGEST from each publisher's
    # RFC6464 audio-level header, before any playback mix) — immune to the browser
    # playback-recapture loop. Only participants the SFU reports active are
    # transcribed. Ships default FALSE; flip TRUE after the runtime checklist
    # confirms the SFU excludes the silent re-capturing participants. Dedup stays
    # ON as the backstop behind it.
    meeting_active_speaker_sfu_gate: bool = Field(
        default=False, alias="MEETING_ACTIVE_SPEAKER_SFU_GATE"
    )
    # Hangover: keep a speaker's STT gate open this long AFTER they leave the SFU
    # active set, so trailing syllables + mid-utterance micro-pauses aren't
    # chopped. CLAMPED at runtime to >= meeting_stt_segmentation_silence_ms so an
    # in-phrase pause never opens an Azure silence hole the recognizer wouldn't
    # itself close. Default 1200 = the segmentation window (the load-bearing floor).
    meeting_active_speaker_hangover_ms: int = Field(
        default=1200, ge=200, le=3000, alias="MEETING_ACTIVE_SPEAKER_HANGOVER_MS"
    )
    # Staleness fail-open: if no active_speakers_changed within this window, the
    # gate goes no-op (degrade to legacy). Small so an event-death blackout is
    # sub-second, not multi-second.
    meeting_active_speaker_stale_s: float = Field(
        default=1.2, ge=0.3, le=10.0, alias="MEETING_ACTIVE_SPEAKER_STALE_S"
    )
    # Leading-edge lookback ring buffer (ms of already-downmixed audio held per
    # speaker while their gate is closed). On first entry into the active set the
    # buffer is flushed to their recognizer, recovering the ~100-300ms onset the
    # SFU lags speech start — so the first syllable isn't lost every turn.
    meeting_active_speaker_lookback_ms: int = Field(
        default=250, ge=0, le=1000, alias="MEETING_ACTIVE_SPEAKER_LOOKBACK_MS"
    )
    # Watchdog: if the gate is on but NO active_speakers_changed is seen within
    # this many seconds of the FIRST human audio frame, auto-disable the gate and
    # log loudly (a silent permission/registration failure becomes observable +
    # self-healing instead of a quiet blackout). 0 = off.
    meeting_active_speaker_watchdog_s: float = Field(
        default=10.0, ge=0.0, le=60.0, alias="MEETING_ACTIVE_SPEAKER_WATCHDOG_S"
    )
    # Per-speaker STT routing. ON → one Azure recognizer PER participant (correct
    # for a PANEL where 2+ humans talk: stops two voices being byte-interleaved
    # into one recognizer). OFF → a SINGLE shared recognizer wired exactly like
    # the (accurate) voice channel's `transport.input() → stt` chain — best for
    # SOLO 1:1 meetings and the cleanest apples-to-apples with voice. Flip OFF if
    # solo transcription is worse than the phone channel's.
    meeting_stt_per_speaker: bool = Field(
        default=True, alias="MEETING_STT_PER_SPEAKER"
    )
    # Coalesce brain tokens into whole sentences before TTS (the SentenceStreamer,
    # ported from avatar_video). ON → the bridge's per-token TextFrames are
    # buffered to sentence boundaries, so TTS synthesises whole phrases (cleaner
    # prosody, fewer round-trips) and the SoulX/Simli avatar lip-syncs per
    # sentence instead of per micro-chunk — the single biggest "voice breaks /
    # bad lip-sync" fix for the avatar meeting. First sentence still ships
    # immediately, so first-audio latency is unchanged. OFF → legacy per-token.
    meeting_sentence_streaming: bool = Field(
        default=True, alias="MEETING_SENTENCE_STREAMING"
    )
    # Drop backlogged avatar video frames so the animation can't tail seconds
    # behind the audio after a SoulX burst / slow start / barge-in. When more than
    # this many frames are queued in the LiveKit video pacer, the oldest are
    # dropped down to the freshest — audio is the master clock, a brief visual
    # catch-up beats a multi-second lag. Mirrors avatar_video's max_backlog=2.
    # 0 = disable dropping (let every frame play, accepting possible tail).
    meeting_video_max_backlog: int = Field(
        default=2, alias="MEETING_VIDEO_MAX_BACKLOG", ge=0, le=30
    )
    # Meeting mode. "solo" = 1:1 career-counsellor call (agent + candidate): the
    # agent greets on candidate-join and answers EVERY turn (addressee gate off,
    # regardless of MEETING_REQUIRE_ADDRESS). "panel" = the original multi-party
    # co-pilot (agent + candidate + human counsellor): gate on, no opener, silent
    # until addressed. Per-meeting overridable via the schedule/start request.
    meeting_mode: str = Field(
        default="solo",
        alias="MEETING_MODE",
        description="Default meeting mode: 'solo' (1:1 agent+candidate, greets + answers all) or 'panel' (multi-party co-pilot, gated).",
    )
    # Opener the agent speaks once when the candidate joins a SOLO meeting. Blank
    # falls back to a persona-aware default built in the runner. Panel meetings
    # never use an opener (the humans run the meeting).
    meeting_solo_opener: str = Field(
        default="",
        alias="MEETING_SOLO_OPENER",
        description="Spoken once on candidate-join in solo mode. Blank = persona-aware default.",
    )
    # How long the agent holds an empty room (Flow B: API trigger → agent waits →
    # candidate joins later) before giving up and tearing down. Should be ≤
    # LIVEKIT_ROOM_EMPTY_TIMEOUT_S so the SFU doesn't reap the room first.
    meeting_agent_wait_s: int = Field(
        default=600,
        alias="MEETING_AGENT_WAIT_S",
        ge=30,
        le=3600,
        description="Seconds the agent waits alone in a room for the candidate before tearing down (Flow B).",
    )
    # Grace after the LAST human leaves before the agent tears down. Absorbs a
    # transient WebRTC reconnect (candidate drops for a few seconds) so a blip
    # doesn't kill an active meeting. 0 = tear down immediately (old behaviour).
    meeting_empty_grace_s: float = Field(
        default=20.0,
        alias="MEETING_EMPTY_GRACE_S",
        ge=0.0,
        le=300.0,
        description="Seconds to wait after the last human leaves before ending the meeting (reconnect grace).",
    )
    # When True the meeting agent publishes a Simli lip-synced AVATAR video track
    # into the room (instead of joining audio-only) — the candidate sees a talking
    # face, not a blank tile. Reuses the SIMLI_API_KEY / SIMLI_FACE_ID +
    # AVATAR_VIDEO_* settings the avatar_video channel already uses. Requires those
    # to be set; falls back to audio-only (with a warning) if Simli is unconfigured.
    meeting_avatar_enabled: bool = Field(
        default=True,
        alias="MEETING_AVATAR_ENABLED",
        description="Publish a Simli avatar video track in the meeting room (vs audio-only). Needs SIMLI_API_KEY + SIMLI_FACE_ID.",
    )
    # Smart barge-in (the 5-intent ACK/ANSWER/INTERRUPT/CONFUSED classifier, same
    # as voice/avatar_video) for the meeting agent. ON: a candidate's "okay/mm-hm"
    # doesn't cut the agent off; only a real interruption does. In panel mode the
    # barge only fires on turns addressed to the agent (human↔human talk never
    # interrupts). OFF: falls back to the crude "any speech cancels" behaviour.
    meeting_enable_barge_in: bool = Field(
        default=True,
        alias="MEETING_ENABLE_BARGE_IN",
        description="Smart ACK/INTERRUPT barge-in for the meeting agent (vs crude any-speech-cancels).",
    )

    # --- Meeting human-simulation features (ported from avatar_video, DEFAULT ON) ---
    # These mirror the AVATAR_ENABLE_* flags one-to-one so the meeting agent gets
    # the same human-feel stack that makes the avatar_video channel seamless:
    # per-session event bus + turn detector + silence re-engagement + metrics.
    # Fully independent of the avatar flags — each channel owns its own stack.
    meeting_enable_turn_detector: bool = Field(
        default=True,
        alias="MEETING_ENABLE_TURN_DETECTOR",
        description="Multi-signal turn-state FSM + watchdog feeding the meeting event bus.",
    )
    meeting_enable_silence_manager: bool = Field(
        default=True,
        alias="MEETING_ENABLE_SILENCE_MANAGER",
        description="T2/T3/T4 silence re-engagement monitor (background task). Only acts in solo/1-human meetings.",
    )
    meeting_enable_silence_responder: bool = Field(
        default=True,
        alias="MEETING_ENABLE_SILENCE_RESPONDER",
        description="On T2/T3/T4 silence events (solo meetings), the brain emits a re-engagement utterance; T5 closes the meeting.",
    )
    meeting_enable_metrics: bool = Field(
        default=False,
        alias="MEETING_ENABLE_METRICS",
        description="Per-stage latency stamper + Prometheus sink + loop-lag monitor. OFF in prod (per-frame overhead).",
    )
    # Warm-up before the solo opener is spoken on candidate-join. The candidate's
    # browser is still subscribing to the agent's audio/video tracks right after
    # join; greeting instantly clips the first words ("...lo, I'm Aisha") — the
    # same failure the avatar channel fixed with AVATAR_OPENER_WARMUP_S. ~1.2s
    # lets the subscription settle (and feels natural — a beat before speaking).
    meeting_opener_warmup_s: float = Field(
        default=1.2, ge=0.0, le=5.0, alias="MEETING_OPENER_WARMUP_S"
    )

    # --- Meeting silence thresholds (used when the silence manager is on) ---
    # Same escalating cadence as the avatar channel: three shortening check-ins,
    # then a warm close. Measured from when the agent stopped speaking AND the
    # candidate stayed quiet; the adaptive bonus in silence_monitor.py shifts all
    # steps together after a long reply. Only applies when the meeting is
    # effectively 1:1 (solo) — a panel's humans are never nudged.
    meeting_silence_t2_s: float = Field(default=9.0,  alias="MEETING_SILENCE_T2_S", description="check-in #1 — first gentle nudge (effective = value + adaptive bonus).")
    meeting_silence_t3_s: float = Field(default=21.0, alias="MEETING_SILENCE_T3_S", description="check-in #2 — ~12s after #1.")
    meeting_silence_t4_s: float = Field(default=33.0, alias="MEETING_SILENCE_T4_S", description="check-in #3 — ~12s after #2, softer.")
    meeting_silence_t5_s: float = Field(default=45.0, alias="MEETING_SILENCE_T5_S", description="graceful goodbye + leave the room ~12s after #3.")

    # --- Meeting turn-detector tunables (mirror avatar_turn_*) ---
    meeting_turn_brief_pause_ms:   int   = Field(default=200,  alias="MEETING_TURN_BRIEF_PAUSE_MS",  description="VAD silence under this = mid-thought")
    meeting_turn_complete_ms:      int   = Field(default=700,  alias="MEETING_TURN_COMPLETE_MS",     description="VAD silence over this = turn done")
    meeting_turn_abandoned_s:      float = Field(default=4.0,  alias="MEETING_TURN_ABANDONED_S",     description="silence after partial → abandoned")
    meeting_turn_confidence_floor: float = Field(default=0.6,  alias="MEETING_TURN_CONFIDENCE_FLOOR", description="min confidence to publish state change")

    # --- LiveKit control-plane service (live-kit/ sibling) -------------------
    # When set, AgentBackend asks the live-kit service for rooms + tokens + the
    # SFU URL instead of using livekit-api directly. This is the clean seam that
    # makes Cloud↔self-hosted a one-place swap (flip LIVEKIT_PROVIDER in the
    # live-kit service; AgentBackend never changes). The agent still joins the
    # room directly via WebRTC (media plane can't go through HTTP), but it fetches
    # its server URL + join token from the service so ALL LiveKit coordinates flow
    # through one seam.
    #
    # BACKWARD-COMPAT: leave this BLANK to keep the original behaviour — AgentBackend
    # mints rooms/tokens itself from LIVEKIT_URL/KEY/SECRET above (direct mode). So
    # existing deployments are unaffected until they opt in by setting this URL.
    livekit_service_url: str = Field(
        default="",
        alias="LIVEKIT_SERVICE_URL",
        description="Base URL of the live-kit control-plane service, e.g. http://localhost:8003. Blank = direct mode (mint locally).",
    )
    livekit_service_timeout_s: float = Field(
        default=10.0,
        ge=1.0,
        le=60.0,
        description="Per-request timeout for calls to the live-kit service.",
    )

    # --- CORS (web-app frontend) ---
    # Comma-separated list of allowed origins for the Next.js web-app.
    # In development this is typically http://localhost:3000.
    # In production set FRONTEND_URLS to the deployed domain(s).
    frontend_urls: str = Field(
        default="http://localhost:3000",
        alias="FRONTEND_URLS",
        description=(
            "Comma-separated CORS allowed origins for the Next.js web-app.  "
            "Example: 'https://counselor.example.com,http://localhost:3000'."
        ),
    )

    # --- Email channel (SMTP — OUTBOUND only; additive) ---
    # Provider-agnostic SMTP: the same code sends via Gmail, Microsoft 365 /
    # Outlook, Amazon SES, etc. — only these values change. With EMAIL_SMTP_HOST
    # unset the channel mounts but sends are disabled (logged), exactly like the
    # WhatsApp/voice channels boot without their secrets.
    #
    #   Gmail:         host=smtp.gmail.com       port=587 security=starttls
    #                  username=<you@gmail.com>  password=<16-char App Password>
    #                  (App Password REQUIRED with 2FA; a normal password fails)
    #   Microsoft 365: host=smtp.office365.com   port=587 security=starttls
    #                  (SMTP AUTH must be ENABLED on the mailbox — Microsoft
    #                   disables basic auth by default & is deprecating it;
    #                   for production prefer Microsoft Graph or ACS Email)
    #   Outlook.com:   host=smtp-mail.outlook.com port=587 security=starttls
    #
    # PROVIDER SWITCH: set EMAIL_PROVIDER to flip the active sender without
    # rewriting the generic EMAIL_SMTP_* block each time:
    #   custom    (default) → use EMAIL_SMTP_HOST/PORT/SECURITY exactly as set
    #                          (today's behaviour — fully backward compatible).
    #   gmail     → preset smtp.gmail.com:587 starttls; credentials from
    #               EMAIL_GMAIL_USERNAME/PASSWORD (fall back to EMAIL_SMTP_*).
    #   microsoft → preset smtp.office365.com:587 starttls; credentials from
    #               EMAIL_MICROSOFT_USERNAME/PASSWORD (fall back to EMAIL_SMTP_*).
    # The From header per provider defaults to that provider's username so a
    # Gmail From is never sent through Microsoft (most relays reject that).
    # Both credential sets can live in .env at once — switch with one var.
    email_provider: Literal["custom", "gmail", "microsoft"] = Field(
        default="custom",
        alias="EMAIL_PROVIDER",
        description="Active SMTP profile: custom (EMAIL_SMTP_*) | gmail | microsoft.",
    )
    email_gmail_username: str = Field(default="", alias="EMAIL_GMAIL_USERNAME")
    email_gmail_password: str = Field(default="", alias="EMAIL_GMAIL_PASSWORD")
    email_gmail_from: str = Field(default="", alias="EMAIL_GMAIL_FROM")
    email_microsoft_username: str = Field(default="", alias="EMAIL_MICROSOFT_USERNAME")
    email_microsoft_password: str = Field(default="", alias="EMAIL_MICROSOFT_PASSWORD")
    email_microsoft_from: str = Field(default="", alias="EMAIL_MICROSOFT_FROM")
    email_smtp_host: str = Field(default="", alias="EMAIL_SMTP_HOST")
    email_smtp_port: int = Field(default=587, ge=1, le=65535, alias="EMAIL_SMTP_PORT")
    email_smtp_security: Literal["starttls", "ssl", "none"] = Field(
        default="starttls",
        alias="EMAIL_SMTP_SECURITY",
        description="starttls (587) | ssl (465) | none (unencrypted — dev only).",
    )
    email_smtp_username: str = Field(default="", alias="EMAIL_SMTP_USERNAME")
    email_smtp_password: str = Field(default="", alias="EMAIL_SMTP_PASSWORD")
    email_from: str = Field(
        default="",
        alias="EMAIL_FROM",
        description="From header, e.g. 'Aisha — Sreenidhi <admissions@suh.edu.in>'. Falls back to EMAIL_SMTP_USERNAME if blank.",
    )
    email_reply_to: str = Field(default="", alias="EMAIL_REPLY_TO")
    email_timeout_s: float = Field(default=20.0, ge=1.0, le=120.0)
    # Default recipients for POST /api/email/report-lead when no `to` is passed —
    # the human counsellor(s) who receive good/best-lead reports. Comma-separated.
    email_counsellor_recipients: str = Field(
        default="",
        alias="EMAIL_COUNSELLOR_RECIPIENTS",
        description="Comma-separated counsellor emails that receive good/best-lead reports.",
    )

    # --- Metrics ---
    metrics_prometheus_port: int = Field(
        default=0,
        description="0 = don't expose; >0 = serve /metrics on this port",
    )

    # --- SoulX / Ditto real-time talking head (deepfake avatar) ---
    ditto_service_url: str = Field(
        default="",
        description="WebSocket URL of the SoulX inference service, e.g. ws://localhost:8011/ws",
    )
    ditto_reference_image_path: str = Field(
        default="",
        description="Optional override of the avatar photo (absolute path on the SoulX "
        "server's filesystem). Leave empty to use the SoulX server's own "
        "SOULX_REFERENCE_IMAGE — the single source of truth for the avatar image.",
    )
    avatar_renderer: Literal["soulx", "simli"] = Field(
        default="soulx",
        alias="AVATAR_RENDERER",
        description="Which renderer the avatar_video channel drives: soulx (GPU talking "
        "head via DITTO_SERVICE_URL) or simli (Simli cloud API). Both run the same "
        "backend wiring; only the video service at the end of the pipeline differs.",
    )

    # --- Logging ---
    log_level: str = Field(default="INFO")
    log_pretty: bool = Field(default=True)

    # --- Langfuse tracing (LLM observability) ---
    # Traces every LangGraph agent turn end-to-end: LLM calls, tool calls,
    # token usage, latency and cost — grouped per conversation. Attached via a
    # LangChain callback handler at the agent invoke sites, so it adds NOTHING
    # to the realtime hot path (events batch + flush on a background thread).
    # Disabled unless both keys are set, so leaving them blank is a safe no-op.
    langfuse_enabled: bool = Field(
        default=True,
        description="Master switch. Even when True, tracing stays OFF until both keys are set.",
        alias="LANGFUSE_ENABLED",
    )
    langfuse_public_key: str = Field(default="", alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(default="", alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        description="Langfuse Cloud, or your self-hosted URL.",
        alias="LANGFUSE_HOST",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """One Settings instance per process. Tests can clear the cache
    via `get_settings.cache_clear()`."""
    return Settings()
