from __future__ import annotations
import asyncio, contextlib, json
from collections.abc import AsyncGenerator
from pathlib import Path
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from starlette.websockets import WebSocketState
from agent_backend.config import get_settings
from agent_backend.infra import get_logger
from agent_backend.services.deepfake import audio_tap
from agent_backend.services.deepfake.ditto_client import DittoClient

log = get_logger(__name__)
router = APIRouter(tags=["avatar"])
_WEBAPP_PATH = Path(__file__).resolve().parent.parent.parent.parent.parent / "webapp" / "index.html"

# Queue holds bytes (JPEG) or str (JSON state message).
_frame_queue: asyncio.Queue | None = None
_audio_ws: WebSocket | None = None
# Ditto pre-generated animation clips — cached for the server lifetime.
_idle_frames: list[bytes] = []
_thinking_frames: list[bytes] = []
# Prevents concurrent Ditto calls (DittoPipeline singleton is not re-entrant).
_ditto_lock = asyncio.Lock()


@router.get("/")
async def serve_page() -> HTMLResponse:
    if _WEBAPP_PATH.exists():
        return HTMLResponse(content=_WEBAPP_PATH.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>webapp/index.html not found</h1>", status_code=404)


@router.get("/health")
async def health() -> dict:
    s = get_settings()
    reachable = False
    if s.ditto_service_url:
        try:
            import httpx
            url = s.ditto_service_url.replace("ws://", "http://").replace("wss://", "https://").replace("/ws", "/health")
            async with httpx.AsyncClient(timeout=2) as c:
                r = await c.get(url)
                reachable = r.status_code == 200
        except Exception:
            pass
    return {"ditto_service": "ok" if reachable else "unreachable", "configured": bool(s.ditto_service_url)}


@router.websocket("/stream")
async def avatar_stream(ws: WebSocket) -> None:
    global _frame_queue
    await ws.accept()
    log.info("[avatar] browser connected to /stream")
    s = get_settings()
    pcm_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=256)
    audio_tap.register(pcm_q)
    _frame_queue = asyncio.Queue(maxsize=1500)
    asyncio.create_task(_init_animation_frames(s), name="anim-init")
    try:
        while True:
            fq = _frame_queue
            if fq is None:
                await asyncio.sleep(0.1)
                continue
            try:
                item = await asyncio.wait_for(fq.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if ws.client_state != WebSocketState.CONNECTED:
                    break
                continue
            try:
                if isinstance(item, str):
                    await ws.send_text(item)
                else:
                    await ws.send_bytes(item)
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        audio_tap.unregister()
        _frame_queue = None
        log.info("[avatar] browser disconnected from /stream")


async def _init_animation_frames(s) -> None:
    """Generate idle and thinking clips (once) and stream them to the browser."""
    idle = await _ensure_idle_frames(s)
    if idle:
        fq = _frame_queue
        if fq is not None:
            with contextlib.suppress(Exception):
                fq.put_nowait(json.dumps({"state": "idle_frames", "count": len(idle)}))
            for jpeg in idle:
                fq2 = _frame_queue
                if fq2 is None:
                    return
                with contextlib.suppress(Exception):
                    fq2.put_nowait(jpeg)

    thinking = await _ensure_thinking_frames(s)
    if thinking:
        fq = _frame_queue
        if fq is not None:
            with contextlib.suppress(Exception):
                fq.put_nowait(json.dumps({"state": "thinking_frames", "count": len(thinking)}))
            for jpeg in thinking:
                fq2 = _frame_queue
                if fq2 is None:
                    return
                with contextlib.suppress(Exception):
                    fq2.put_nowait(jpeg)


async def _ensure_idle_frames(s) -> list[bytes]:
    """Load pre-generated offline idle frames, or generate via streaming as fallback.

    Offline frames (idle_frames.pkl) are generated once using advancing audio indices
    (like generate_video.py) which avoids the motion latent drift that streaming mode
    produces. Generate them with the gen_idle_frames.py script on the VM.
    """
    global _idle_frames
    if _idle_frames:
        return _idle_frames
    import pickle, pathlib
    if s.ditto_reference_image_path:
        cache_path = pathlib.Path(s.ditto_reference_image_path).parent / "idle_frames.pkl"
        if cache_path.exists():
            try:
                with open(cache_path, "rb") as f:
                    _idle_frames = pickle.load(f)
                log.info("[avatar] idle frames loaded from cache: %d frames", len(_idle_frames))
                return _idle_frames
            except Exception as exc:
                log.warning("[avatar] idle cache load failed: %s — falling back to streaming", exc)
    if not s.ditto_service_url or not s.ditto_reference_image_path:
        return []
    silence = bytes(6 * 16000 * 2)
    ditto = DittoClient(ws_url=s.ditto_service_url)
    async with _ditto_lock:
        try:
            frames = await ditto.speak_once(image_path=s.ditto_reference_image_path, pcm_int16=silence)
        except Exception as exc:
            log.warning("[avatar] idle frame generation failed: %s", exc)
            return []
    if frames:
        _idle_frames = frames
        log.info("[avatar] idle frames cached: %d", len(frames))
    return _idle_frames


async def _ensure_thinking_frames(s) -> list[bytes]:
    """Load pre-generated offline thinking frames, or generate via streaming as fallback."""
    global _thinking_frames
    if _thinking_frames:
        return _thinking_frames
    import pickle, pathlib
    if s.ditto_reference_image_path:
        cache_path = pathlib.Path(s.ditto_reference_image_path).parent / "thinking_frames.pkl"
        if cache_path.exists():
            try:
                with open(cache_path, "rb") as f:
                    _thinking_frames = pickle.load(f)
                log.info("[avatar] thinking frames loaded from cache: %d frames", len(_thinking_frames))
                return _thinking_frames
            except Exception as exc:
                log.warning("[avatar] thinking cache load failed: %s — falling back to streaming", exc)
    if not s.ditto_service_url or not s.ditto_reference_image_path:
        return []
    silence = bytes(6 * 16000 * 2)
    ditto = DittoClient(ws_url=s.ditto_service_url)
    async with _ditto_lock:
        try:
            frames = await ditto.speak_once(image_path=s.ditto_reference_image_path, pcm_int16=silence)
        except Exception as exc:
            log.warning("[avatar] thinking frame generation failed: %s", exc)
            return []
    if frames:
        _thinking_frames = frames
        log.info("[avatar] thinking frames cached: %d", len(frames))
    return _thinking_frames


@router.websocket("/audio")
async def avatar_audio(ws: WebSocket) -> None:
    global _audio_ws
    await ws.accept()
    _audio_ws = ws
    log.info("[avatar] browser connected to /audio")
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        _audio_ws = None


async def relay_audio_to_browser(pcm: bytes) -> None:
    if _audio_ws and _audio_ws.client_state == WebSocketState.CONNECTED:
        with contextlib.suppress(Exception):
            await _audio_ws.send_bytes(pcm)


@router.post("/speak")
async def speak(body: dict) -> dict:
    """Test endpoint — type text to make the avatar speak without the full voice pipeline."""
    text: str = body.get("text", "").strip()
    if not text:
        return {"error": "text is required"}
    asyncio.create_task(_speak_task(text, get_settings()), name="avatar-speak")
    return {"ok": True, "text": text}


async def _tts_stream_azure(text: str, s) -> AsyncGenerator[bytes, None]:
    """Yield raw int16 PCM chunks as Azure TTS generates them (streaming)."""
    import azure.cognitiveservices.speech as speechsdk  # type: ignore

    speech_config = speechsdk.SpeechConfig(
        subscription=s.azure_speech_key, region=s.azure_speech_region
    )
    speech_config.speech_synthesis_voice_name = s.azure_tts_voice
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Raw16Khz16BitMonoPcm
    )

    chunk_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _on_synthesizing(evt: object) -> None:
        data = evt.result.audio_data  # type: ignore[attr-defined]
        if data:
            loop.call_soon_threadsafe(chunk_queue.put_nowait, data)

    def _on_done(evt: object) -> None:  # noqa: ARG001
        loop.call_soon_threadsafe(chunk_queue.put_nowait, None)

    synth = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
    synth.synthesizing.connect(_on_synthesizing)
    synth.synthesis_completed.connect(_on_done)
    synth.synthesis_canceled.connect(_on_done)
    synth.speak_text_async(text)  # non-blocking start

    while True:
        chunk = await chunk_queue.get()
        if chunk is None:
            break
        yield chunk


async def _tts_stream_elevenlabs(text: str, s) -> AsyncGenerator[bytes, None]:
    """Yield raw int16 PCM chunks from ElevenLabs streaming TTS."""
    from elevenlabs.client import ElevenLabs  # type: ignore

    try:
        audio_stream = ElevenLabs(api_key=s.elevenlabs_api_key).text_to_speech.convert(
            voice_id=s.elevenlabs_voice_id,
            model=s.elevenlabs_model,
            text=text,
            output_format="pcm_16000",
        )
    except Exception as exc:
        log.warning("[avatar] ElevenLabs TTS failed: %s", exc)
        return

    for piece in audio_stream:
        if piece:
            yield piece


async def _speak_task(text: str, s) -> None:
    import time as _time
    _t_click = _time.monotonic()
    await _send_state("thinking")

    if not s.ditto_service_url or not s.ditto_reference_image_path:
        await _send_state("idle")
        return

    # Choose TTS stream based on provider
    if s.voice_tts_provider == "azure":
        tts_gen = _tts_stream_azure(text, s)
    else:
        tts_gen = _tts_stream_elevenlabs(text, s)

    # Accumulate all PCM as TTS streams — needed for A/V sync in on_frame_live.
    # The server buffers all PCM before starting LMDM, so all_pcm is fully
    # populated before the first frame arrives.
    all_pcm: bytearray = bytearray()

    _DITTO_CHUNK_BYTES = 6480 * 2  # 6480 int16 samples

    async def pcm_generator() -> AsyncGenerator[bytes, None]:
        buf = bytearray()
        async for raw in tts_gen:
            all_pcm.extend(raw)
            buf.extend(raw)
            while len(buf) >= _DITTO_CHUNK_BYTES:
                yield bytes(buf[:_DITTO_CHUNK_BYTES])
                del buf[:_DITTO_CHUNK_BYTES]
        if buf:
            yield bytes(buf)

    # Deliver each frame + its matching audio immediately as Ditto produces it.
    frames_played = 0
    speak_started = False
    AUDIO_PER_FRAME = 1280  # 40ms × 16kHz × 2 bytes/sample

    async def on_frame_live(jpeg: bytes) -> None:
        nonlocal frames_played, speak_started
        if not speak_started:
            speak_started = True
            await _send_state("speaking")
            log.info("[avatar] first frame latency: %.1fs from click", _time.monotonic() - _t_click)
        fq = _frame_queue
        if fq is not None:
            with contextlib.suppress(Exception):
                fq.put_nowait(jpeg)
        a_start = frames_played * AUDIO_PER_FRAME
        a_end = min(a_start + AUDIO_PER_FRAME, len(all_pcm))
        if a_start < len(all_pcm):
            await relay_audio_to_browser(bytes(all_pcm[a_start:a_end]))
        frames_played += 1
        await asyncio.sleep(1 / 25)  # pace recv at display rate so final burst doesn't return early

    ditto = DittoClient(ws_url=s.ditto_service_url)
    async with _ditto_lock:
        try:
            await ditto.speak_from_stream(
                image_path=s.ditto_reference_image_path,
                pcm_gen=pcm_generator(),
                on_frame=on_frame_live,
            )
        except Exception as exc:
            log.warning("[avatar] Ditto streaming failed: %s", exc)
            await _send_state("idle")
            return

    if not speak_started:
        log.warning("[avatar] Ditto returned no frames")
        await _send_state("idle")
        return

    audio_s = len(all_pcm) / 2 / 16000
    log.info("[avatar] speaking: %d frames, %.1fs audio — total %.1fs from click",
             frames_played, audio_s, _time.monotonic() - _t_click)
    await _send_state("idle")
    log.info("[avatar] speak complete")


async def _send_state(state: str) -> None:
    fq = _frame_queue
    if fq is not None:
        with contextlib.suppress(Exception):
            fq.put_nowait(json.dumps({"state": state}))


async def _play_synced(all_pcm: bytearray, frames: list[bytes]) -> None:
    """Send audio and video in lock-step at 25 FPS — guarantees A/V sync duration.

    Each iteration: one video frame + one 40ms audio chunk sent together.
    Total iterations = max(len(frames), audio_frame_count), so audio and video
    always last exactly the same duration regardless of Ditto frame count.
    If frames < audio duration, last frame is held (not idle animation).
    """
    AUDIO_PER_FRAME = 1280  # 40ms × 16000 Hz × 2 bytes/sample
    audio_frame_count = (len(all_pcm) + AUDIO_PER_FRAME - 1) // AUDIO_PER_FRAME
    total = max(len(frames), audio_frame_count)

    for i in range(total):
        # Video: send frame i, or hold last frame if Ditto returned fewer frames
        jpeg = frames[min(i, len(frames) - 1)] if frames else None
        if jpeg is not None:
            fq = _frame_queue
            if fq is not None:
                with contextlib.suppress(Exception):
                    fq.put_nowait(jpeg)

        # Audio: send the 40ms audio chunk corresponding to frame i
        a_start = i * AUDIO_PER_FRAME
        a_end = min(a_start + AUDIO_PER_FRAME, len(all_pcm))
        if a_start < len(all_pcm):
            await relay_audio_to_browser(bytes(all_pcm[a_start:a_end]))

        await asyncio.sleep(1 / 25)
