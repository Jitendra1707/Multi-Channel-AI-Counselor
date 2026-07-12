# live-kit — LiveKit control-plane service

A small FastAPI service that owns the LiveKit **control plane** (room creation,
JWT minting, webhook verification) for the AegisAvatar meeting channel — behind a
**provider switch** so LiveKit **Cloud** and a **self-hosted open-source** server
are swappable by one env var. AegisBackend calls this service's HTTP API instead
of using `livekit-api` directly, so the Cloud↔OSS choice lives in **one place** and
AegisBackend's code stays clean and unchanged when you swap backends.

```
                 LIVEKIT_PROVIDER=cloud | selfhosted
                              │
 AegisBackend ──HTTP──▶  live-kit service  ──livekit-api──▶  LiveKit (Cloud or your server)
 (meeting channel)        /rooms /token                       creates rooms, signs JWTs
                          /webhook /config
 web-app ──HTTP(/config)──▶  (one source of truth for the wss:// SFU URL)

 The agent's MEDIA (WebRTC audio) still flows AegisBackend⇄SFU directly — only
 the *coordinates* (room, token, url) pass through this service.
```

## Why a separate service (not just config in AegisBackend)

- **One seam for pluggability.** Switching Cloud→self-hosted is `LIVEKIT_PROVIDER`
  + creds *here* — AegisBackend and the web-app don't change.
- **Clean readability.** AegisBackend's meeting code calls `create_room` /
  `mint_token` over HTTP (like it already calls BusinessLayer), instead of
  embedding LiveKit SDK + key handling in the channel.
- **Single source of truth for the SFU URL.** The web-app and the agent both get
  the `wss://` URL from `GET /config`, so it's never duplicated/out-of-sync.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/health` | liveness + provider + configured status |
| GET  | `/config` | `{ provider, url, configured }` — web-app/agent fetch the SFU URL here |
| POST | `/rooms` | create (or reuse) a room → `{ room }` |
| POST | `/token` | mint a join JWT → `{ room, identity, token, url }` |
| POST | `/rooms/{room}/delete` | delete a room (best-effort) |
| POST | `/webhook` | LiveKit → here: verify signature, forward to AegisBackend |

Degradation: provider not configured → **503**; bad input → **4xx**; upstream
LiveKit failure → **502**; bad webhook signature → **401**. Never a bare 500.

## Layout

```
live-kit/
  livekit_svc/
    main.py            # FastAPI app + CORS + startup log
    config.py          # LIVEKIT_PROVIDER + creds + room policy + webhook forward
    logging.py         # structlog (matches AegisBackend / BusinessLayer)
    routes.py          # the endpoints above
    providers/
      base.py          # LiveKitProvider Protocol + ProviderInfo + MeetingConfigError
      _common.py       # shared impl (livekit-api): create_room/mint_token/delete/verify_webhook
      cloud.py         # CloudProvider (name="cloud")
      selfhosted.py    # SelfHostedProvider (name="selfhosted")
      __init__.py      # get_provider() — picks by LIVEKIT_PROVIDER
  requirements.txt
  .env.example
```

## Run

```bash
cd live-kit
python -m venv venv && ./venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# (or: python3 -m venv venv && venv/bin/pip install -r requirements.txt)

cp .env.example .env       # edit: provider + creds
python -m uvicorn livekit_svc.main:app --host 0.0.0.0 --port 8003 --reload
```

Verify:
```bash
curl http://localhost:8003/health
curl -X POST http://localhost:8003/token \
  -H 'Content-Type: application/json' \
  -d '{"room":"meet-x","identity":"u1","display_name":"User","role":"candidate"}'
```

## Switching Cloud ↔ self-hosted

Edit **this service's** `.env` only:

```
# Cloud
LIVEKIT_PROVIDER=cloud
LIVEKIT_URL=wss://<project>.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...

# Self-hosted (open-source server — see the live-kit-opensource skill)
LIVEKIT_PROVIDER=selfhosted
LIVEKIT_URL=ws://localhost:7880        # wss://lk.yourdomain.com behind TLS in prod
LIVEKIT_API_KEY=devkey
LIVEKIT_API_SECRET=secret
```

Restart this service. AegisBackend and the web-app are untouched.

## Connecting AegisBackend to this service

In `AegisBackend/.env` set:
```
LIVEKIT_SERVICE_URL=http://localhost:8003
```
…and restart AegisBackend. Now AegisBackend asks this service for rooms/tokens
(SERVICE mode). Leave it blank to keep AegisBackend minting locally (DIRECT mode)
— both work; the service is opt-in and non-breaking.
```
