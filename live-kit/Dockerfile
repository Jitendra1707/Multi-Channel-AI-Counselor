# Meeting service (the standalone, agent-agnostic meeting backend).
# Tiny FastAPI app on :8003: create rooms + mint join tokens + verify webhooks.
# No LLM, no ML — just the LiveKit control plane. Build + run:
#   docker build -t aegis-meeting-svc .
#   docker run --rm -p 8003:8003 --env-file .env aegis-meeting-svc
# The LiveKit OSS server runs separately (public livekit/livekit-server image).
FROM python:3.11-slim

WORKDIR /app

# Install deps first (layer cache: code changes don't reinstall packages).
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# App code.
COPY livekit_svc ./livekit_svc

# Transcript output dir. Mount a host volume here to keep files across restarts:
#   docker run ... -v "$PWD/transcripts:/app/transcripts" ...
ENV TRANSCRIPT_OUTPUT_DIR=/app/transcripts
RUN mkdir -p /app/transcripts

# Run as a non-root user (avoids running the container as root).
RUN addgroup --system app && adduser --system --ingroup app app \
    && chown -R app:app /app
USER app

EXPOSE 8003

# Reads ALL config from env (pass via --env-file .env): LIVEKIT_PROVIDER,
# LIVEKIT_URL/KEY/SECRET, MEETING_JOIN_BASE_URL, AZURE_SPEECH_*, TRANSCRIBE_*.
CMD ["python", "-m", "uvicorn", "livekit_svc.main:app", "--host", "0.0.0.0", "--port", "8003"]
