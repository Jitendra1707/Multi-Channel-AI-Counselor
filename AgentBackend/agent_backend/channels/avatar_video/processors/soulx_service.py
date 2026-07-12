"""SoulX FrameProcessor — persistent session, one WebSocket per browser peer.

Architecture mirrors simli_service.py:
  - Connect once on StartFrame, keep connection alive for the entire session.
  - Server streams idle JPEG frames at 25fps when no speech is active.
  - On first TTSAudioRawFrame: send speak_start → server cancels idle, enters GPU inference.
  - Server confirms with {"type": "speaking"} → client starts pairing audio with frames.
  - On TTSStoppedFrame: send eof (debounced 150ms) → server flushes, returns to idle.
  - Server confirms with {"type": "idle"} → client drains remaining audio, pushes BotStopped.
  - On InterruptionFrame: send interrupt → server discards buffer, returns to idle.

Two-flag design for clean audio pairing:
  _speak_start_sent — set when speak_start is sent; governs EOF/interrupt logic
  _speaking         — set only when server confirms "speaking"; governs frame audio pairing

This prevents idle frames (received after speak_start but before server confirmation)
from draining real TTS audio from _pcm_buffer, eliminating A/V desync.

Server protocol (soulx_server.py):
  idle  ─── speak_start ──► [{"type":"speaking"}] ──► speaking ─── eof ──► [{"type":"idle"}] ──► idle
                                                                  ─── interrupt ──► [{"type":"idle"}] ──► idle
  idle  ─── close ──► closed
"""
from __future__ import annotations

import asyncio
import io
import json
import time
from collections import deque

import aiohttp
import numpy as np
from loguru import logger
from PIL import Image

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    OutputImageRawFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor, StartFrame

from agent_backend.channels.avatar_video.events import BotSpeakingEvent, EventBus
from agent_backend.config import get_settings

# 40ms of audio per video frame at 16kHz int16 mono = 640 samples × 2 bytes
FRAME_AUDIO_BYTES = 1280


def _decode_jpeg_to_rgb(payload: bytes) -> tuple[bytes, int, int]:
    """JPEG bytes → packed RGB24 bytes + (width, height).

    Runs in a worker thread (via asyncio.to_thread) so this CPU-bound, GIL-holding
    decode never blocks the event loop's realtime audio/RTP delivery. Mirrors
    simli_service._frame_to_rgb_bytes — without the offload the 25fps idle decode
    starves the loop and delays run_stream / TTS for the whole session.
    """
    img = Image.open(io.BytesIO(payload)).convert("RGB")
    w, h = img.size
    return img.tobytes(), w, h


class AgentSoulXVideoService(FrameProcessor):
    """Pipecat FrameProcessor driving SoulX with a persistent WebSocket session.

    Usage:
        service = AgentSoulXVideoService(
            service_url="ws://localhost:8011/ws",
            image_path="/home/aegisuser/ditto_service/face.jpg",
            out_sample_rate=48000,
        )
    """

    def __init__(
        self,
        *,
        service_url: str,
        image_path: str = "",
        out_sample_rate: int = 48000,
    ) -> None:
        super().__init__()
        self._service_url     = service_url
        self._image_path      = image_path
        self._out_sample_rate = out_sample_rate

        # Event bus — set by runner.py (render_service._bus = bus). Mirrors
        # AgentSimliVideoService: the video service publishes the AUTHORITATIVE
        # BotSpeakingEvent so the SilenceMonitor ARMS and the T5 auto-hangup /
        # end_call timers fire. Without it, silence re-engagement never starts.
        self._bus: EventBus | None = None

        # Persistent WebSocket (one per browser peer)
        self._ws:           aiohttp.ClientWebSocketResponse | None = None
        self._http_session: aiohttp.ClientSession | None = None
        self._video_task:   asyncio.Task | None = None

        # Two-flag design for correct audio pairing (see module docstring)
        # _speak_start_sent: we sent speak_start; server is cancelling idle / starting inference
        # _speaking: server confirmed speaking mode via {"type":"speaking"} message
        self._speak_start_sent: bool = False
        self._speaking:         bool = False

        # EOF gate — True after LLMFullResponseEndFrame (LLM fully done, no more TTS batches)
        self._llm_response_done: bool = False

        # True while the server is showing thinking frames (LLM processing → speak_start).
        self._thinking_started: bool = False

        # Safety-net debounce task — fallback when LLMFullResponseEndFrame is delayed
        self._eof_task: asyncio.Task | None = None

        # After barge-in: discard in-transit speaking frames from the old utterance
        # until the server confirms it is back in idle mode ({"type":"idle"} received).
        self._ignore_binary: bool = False

        # Set by runner.py via signal_client_connected() when the WebRTC browser peer
        # connects. The first speak_start is held until this event fires so that no
        # speaking audio is pushed to an unready transport (which drops the first words).
        self._client_connected: asyncio.Event = asyncio.Event()


        # Audio buffer: raw int16 bytes from TTS, drained 1280 bytes per speaking JPEG frame
        self._pcm_buffer = bytearray()

        # Echoed audio (0x01) for the current speaking frame, held until its paired
        # video frame (0x00) is decoded so the two are pushed together. Pushing audio
        # the instant it arrives — while the now-threaded JPEG decode yields the loop —
        # lets the audio track play ahead of the frame, so audio leads video. Buffering
        # here restores the per-frame audio↔video lockstep without re-blocking the loop.
        self._pending_audio: bytes | None = None

        # Integer upsampling ratio: 16kHz TTS → out_sample_rate (48kHz = 3×).
        # np.repeat(arr, ratio) produces exactly len(arr)×ratio samples per call —
        # stateless, no filter delay, no frame-splitting — guaranteeing one audio
        # frame per video frame for perfect A/V alignment.
        self._upsample_ratio: int = out_sample_rate // 16000

        # 40ms silence at out_sample_rate — pushed with each idle video frame to
        # keep the WebRTC audio clock running continuously during idle periods.
        self._silence_audio: bytes = (
            np.zeros(int(out_sample_rate / 25), dtype=np.int16).tobytes()
        )

        # ── Startup-latency profiling (observe-only; gated by AVATAR_ENABLE_METRICS) ──
        # Decomposes the per-utterance renderer spin-up that Simli doesn't pay:
        #   b0 = first TTS audio → speak_start sent (incl. one-time _client_connected gate)
        #   b1 = speak_start → server "speaking" confirmation (idle cancel + inference start)
        #   b2 = speak_start → first speaking frame pushed downstream (full GPU spin-up)
        self._metrics_on: bool = get_settings().avatar_enable_metrics
        self._t_speak_start: float | None = None
        self._first_speaking_frame_logged: bool = False

        # Pass-2 speech-pacing accounting (observe-only): is SoulX feeding audio
        # below real-time (GPU can't sustain 25fps)? Reset per utterance.
        #   ratio = audio_delivered_s / speaking_wall_s  (≈1.0 = real-time)
        self._pace_frames: int = 0
        self._pace_audio_bytes: int = 0
        self._pace_first_frame_t: float | None = None
        self._pace_last_frame_t: float | None = None

        # A/V-drift probe (observe-only): inter-frame arrival jitter + first-audio
        # vs first-frame offset. Aggregates (fps/ratio) hide a constant A/V offset
        # and bursty frame delivery — these two catch exactly that. Reset per utterance.
        self._pace_prev_frame_t: float | None = None   # arrival wall-clock of previous frame
        self._pace_max_gap_s: float = 0.0              # worst inter-frame arrival gap this utterance
        self._pace_gap_count: int = 0                  # frames arriving >80ms after the previous (>2× interval)
        self._pace_first_audio_t: float | None = None  # wall-clock of first REAL echoed-audio push

        # Audio-push BURST probe (Stage 0.5b, observe-only). The browser audio jitter
        # buffer balloons when audio is pushed faster than real-time (a video burst →
        # many 40ms chunks shoved in at once). Track, over a sliding 500ms wall window,
        # the peak (audio-ms-pushed / wall-ms) ratio and the largest 200ms-window burst.
        #   peak_audio_push_rate >> 1.0 ⇒ the bridge IS the burst source. ≈1.0 ⇒ it isn't.
        self._push_window: "deque[tuple[float, float]]" = deque()  # (wall_t, audio_ms)
        self._peak_push_rate: float = 0.0
        self._max_push_burst_ms: float = 0.0

    # ── Bot-speaking → bus ──────────────────────────────────────────────────

    def _publish_speaking(self, speaking: bool) -> None:
        """Publish the authoritative bot-speaking signal to the event bus.

        Called alongside every BotStarted/StoppedSpeakingFrame push so the
        SilenceMonitor (which gates on BotSpeakingEvent) arms correctly — this
        is what mirrors AgentSimliVideoService's _bus publishes.
        """
        if self._bus is not None:
            self._bus.publish(BotSpeakingEvent(speaking=speaking))

    # ── Resampler ─────────────────────────────────────────────────────────────

    def _flush_resampler(self) -> None:
        """No-op: resampler replaced by stateless numpy upsampling."""
        pass

    async def _push_audio_chunk(self, chunk: bytes) -> None:
        """Upsample int16 16kHz chunk → out_sample_rate and push ONE audio frame.

        Uses np.repeat (zero-order hold) instead of PyAV AudioResampler because:
          - AudioResampler has a polyphase FIR filter that produces 0, 1, or 2 output
            frames per call (filter ramp-up delay + internal frame-size splitting).
          - This causes random misalignment: some video frames get no audio, others
            get 2× audio, creating progressive A/V drift mid-response.
          - np.repeat produces exactly len(arr)×ratio samples every call — no state,
            no delay, guaranteed one TTSAudioRawFrame per video frame.
        """
        if not chunk:
            return
        arr = np.frombuffer(chunk, dtype=np.int16)
        if not arr.size:
            return
        # Zero-order hold: each 16kHz sample is repeated _upsample_ratio times.
        # For 48kHz output: 640 input → exactly 1920 output samples (40ms). Always.
        upsampled = np.repeat(arr, self._upsample_ratio)
        await self.push_frame(
            TTSAudioRawFrame(
                audio=upsampled.astype(np.int16).tobytes(),
                sample_rate=self._out_sample_rate,
                num_channels=1,
            )
        )

    def _record_audio_push(self, chunk: bytes) -> None:
        """Stage 0.5b burst probe (observe-only): update the sliding-window peak
        audio-push rate. Each `chunk` is int16 16kHz mono, so audio-ms = samples / 16.
        Peak (audio-ms / wall-ms) over 500ms >> 1.0 means we're shoving audio in
        faster than real-time (the browser audio-buffer-inflation source)."""
        now = time.monotonic()
        audio_ms = (len(chunk) // 2) / 16.0  # int16 @16kHz → ms
        self._push_window.append((now, audio_ms))
        # Drop entries older than 500ms.
        while self._push_window and now - self._push_window[0][0] > 0.5:
            self._push_window.popleft()
        wall_ms = (now - self._push_window[0][0]) * 1000.0
        win_audio_ms = sum(ms for _, ms in self._push_window)
        if wall_ms >= 50.0:  # need a meaningful span before the ratio is trustworthy
            self._peak_push_rate = max(self._peak_push_rate, win_audio_ms / wall_ms)
        # Largest burst within any tight 200ms window (back-to-back catch-up sends).
        burst_ms = sum(ms for t, ms in self._push_window if now - t <= 0.2)
        self._max_push_burst_ms = max(self._max_push_burst_ms, burst_ms)

    # ── Deferred EOF ──────────────────────────────────────────────────────────

    async def _deferred_eof(self, delay: float) -> None:
        """Send EOF to SoulX after delay. Cancelled if LLMFullResponseEndFrame arrives first."""
        await asyncio.sleep(delay)  # CancelledError propagates naturally — Task properly cancelled
        if self._speak_start_sent and self._ws and not self._ws.closed:
            try:
                await self._ws.send_str(json.dumps({"type": "eof"}))
            except Exception:
                pass

    # ── Connection lifecycle (one per browser peer) ───────────────────────────

    def signal_client_connected(self) -> None:
        """Called by runner.py when the WebRTC browser peer has connected.

        Releases the _client_connected event so the first speak_start can be sent.
        Without this gate, ~1.7s of speaking audio is pushed to an unready transport
        and dropped — the user misses the first 4-5 words of the greeting.
        """
        self._client_connected.set()

    async def _connect(self) -> None:
        """Open the persistent SoulX WebSocket and start the video consumer."""
        self._http_session = aiohttp.ClientSession()
        self._ws = await self._http_session.ws_connect(self._service_url)
        # Omit image_path so the SoulX server uses its own SOULX_REFERENCE_IMAGE
        # (single source of truth); send it only as an explicit override.
        init: dict = {"session_id": "pipecat"}
        if self._image_path:
            init["image_path"] = self._image_path
        await self._ws.send_str(json.dumps(init))
        msg  = await self._ws.receive()
        data = json.loads(msg.data)
        if data.get("status") != "ready":
            raise RuntimeError(f"SoulX init failed: {data}")
        logger.info("[soulx] session connected — idle mode")
        self._video_task = self.create_task(self._consume_video())

    async def _disconnect(self) -> None:
        """Close the persistent WebSocket and cancel background tasks."""
        if self._eof_task and not self._eof_task.done():
            self._eof_task.cancel()
        self._eof_task = None
        if self._video_task and not self._video_task.done():
            self._video_task.cancel()
            try:
                await self._video_task
            except (asyncio.CancelledError, Exception):
                pass
        self._video_task = None
        if self._ws and not self._ws.closed:
            try:
                await self._ws.send_str(json.dumps({"type": "close"}))
                await self._ws.close()
            except Exception:
                pass
        if self._http_session:
            try:
                await self._http_session.close()
            except Exception:
                pass
        self._ws           = None
        self._http_session = None

    # ── Video consumer (runs for the entire session lifetime) ─────────────────

    async def _consume_video(self) -> None:
        """Receive JPEG frames and control messages from SoulX for the full session.

        Server sends:
          binary              → JPEG frame (idle animation or lip-synced speaking frame)
          {"type":"speaking"} → server confirmed speaking mode; start pairing audio
          {"type":"idle"}     → utterance complete; server returning to idle mode

        During idle   (self._speaking = False): push idle video + silence audio
        During speaking (self._speaking = True): push speaking video + drain _pcm_buffer
        On "idle" message: drain any remaining _pcm_buffer, flush resampler, push BotStopped
        """
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    # All binary messages now carry a 1-byte type prefix:
                    #   0x00 = video frame (JPEG)
                    #   0x01 = audio echoed from server (int16, FRAME_AUDIO_BYTES = 1280 bytes)
                    # The server sends pairs: 0x01 audio THEN 0x00 video for each speaking frame.
                    # Idle frames are 0x00 only (no audio echo — client uses silence instead).
                    if len(msg.data) < 1:
                        continue
                    msg_type_byte = msg.data[0]
                    payload = msg.data[1:]

                    if msg_type_byte == 1:
                        # Audio echoed from server — perfectly matched to the next video frame.
                        if self._ignore_binary:
                            # In-transit during barge-in: push silence to keep clock running.
                            await self.push_frame(
                                TTSAudioRawFrame(
                                    audio=self._silence_audio,
                                    sample_rate=self._out_sample_rate,
                                    num_channels=1,
                                )
                            )
                            continue
                        if self._speaking:
                            if self._metrics_on:
                                self._pace_audio_bytes += len(payload)
                            # Hold this chunk; it is pushed together with its paired
                            # video frame (the following 0x00) so audio cannot outrun it.
                            self._pending_audio = bytes(payload)
                        else:
                            await self.push_frame(
                                TTSAudioRawFrame(
                                    audio=self._silence_audio,
                                    sample_rate=self._out_sample_rate,
                                    num_channels=1,
                                )
                            )

                    elif msg_type_byte == 0:
                        # Video frame (JPEG).
                        if self._ignore_binary:
                            # Discard in-transit video; audio silence was pushed via 0x01 above.
                            continue
                        # A/V probe: time SoulX's delivery cadence at ARRIVAL (before decode),
                        # so a bursty stream (clumps + gaps that still average 25fps) is visible.
                        if self._speaking and self._metrics_on:
                            _arr = time.monotonic()
                            if self._pace_prev_frame_t is not None:
                                _gap = _arr - self._pace_prev_frame_t
                                self._pace_max_gap_s = max(self._pace_max_gap_s, _gap)
                                if _gap > 0.08:  # >2× the 40ms frame interval
                                    self._pace_gap_count += 1
                            self._pace_prev_frame_t = _arr
                        image_bytes, w, h = await asyncio.to_thread(
                            _decode_jpeg_to_rgb, payload
                        )
                        await self.push_frame(
                            OutputImageRawFrame(
                                image=image_bytes, size=(w, h), format="RGB"
                            )
                        )
                        if (
                            self._speaking
                            and self._metrics_on
                            and not self._first_speaking_frame_logged
                            and self._t_speak_start is not None
                        ):
                            logger.info(
                                "[soulx-latency] b2 speak_start→first speaking frame: {:.1f}ms",
                                (time.monotonic() - self._t_speak_start) * 1000.0,
                            )
                            self._first_speaking_frame_logged = True
                        if self._speaking and self._metrics_on:
                            _nowf = time.monotonic()
                            self._pace_frames += 1
                            if self._pace_first_frame_t is None:
                                self._pace_first_frame_t = _nowf
                            self._pace_last_frame_t = _nowf
                        if not self._speaking:
                            # Idle frame: push silence (no 0x01 audio for idle frames).
                            await self.push_frame(
                                TTSAudioRawFrame(
                                    audio=self._silence_audio,
                                    sample_rate=self._out_sample_rate,
                                    num_channels=1,
                                )
                            )
                        elif self._pending_audio is not None:
                            # Speaking: emit the paired audio buffered from the preceding
                            # 0x01 now that this frame is decoded — pushing them together
                            # keeps audio and video in lockstep (no audio-ahead).
                            if self._metrics_on and self._pace_first_audio_t is None:
                                self._pace_first_audio_t = time.monotonic()
                            if self._metrics_on:
                                self._record_audio_push(self._pending_audio)
                            await self._push_audio_chunk(self._pending_audio)
                            self._pending_audio = None
                        else:
                            # Speaking frame with no paired audio (frame boundary): push
                            # silence so the audio clock stays continuous with the video.
                            await self.push_frame(
                                TTSAudioRawFrame(
                                    audio=self._silence_audio,
                                    sample_rate=self._out_sample_rate,
                                    num_channels=1,
                                )
                            )

                elif msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except Exception:
                        continue

                    if data.get("type") == "speaking":
                        # Server confirmed it cancelled the idle loop and started inference.
                        # Clear _ignore_binary so speaking frames are not discarded.
                        self._ignore_binary = False
                        self._speaking = True
                        if self._metrics_on and self._t_speak_start is not None:
                            logger.info(
                                "[soulx-latency] b1 speak_start→\"speaking\" confirmed: {:.1f}ms",
                                (time.monotonic() - self._t_speak_start) * 1000.0,
                            )

                    elif data.get("type") == "idle":
                        # Server is back in idle mode. Always clear _ignore_binary here,
                        # regardless of _speaking state. When the bot WAS SPEAKING during
                        # a barge-in, the server sends "idle" after processing the
                        # interrupt — that's the signal to allow idle frames through again.
                        self._ignore_binary = False
                        if self._speaking:
                            # Normal utterance complete path — SoulX animated all audio.
                            # Discard any sub-frame remainder rather than draining as
                            # audio-only (which would play during idle animation).
                            self._pcm_buffer.clear()
                            self._pending_audio = None
                            self._flush_resampler()
                            self._speaking          = False
                            self._speak_start_sent  = False
                            self._llm_response_done = False
                            _t0 = self._t_speak_start  # capture before reset (for A/V offset metrics)
                            self._t_speak_start = None
                            self._first_speaking_frame_logged = False
                            if (
                                self._metrics_on
                                and self._pace_first_frame_t is not None
                                and self._pace_last_frame_t is not None
                            ):
                                _wall = self._pace_last_frame_t - self._pace_first_frame_t
                                _audio_s = self._pace_audio_bytes / 2 / 16000.0  # echoed int16 mono 16k
                                _fps = (self._pace_frames / _wall) if _wall > 0 else 0.0
                                _ratio = (_audio_s / _wall) if _wall > 0 else 0.0
                                # A/V probe: first-audio vs first-frame offset (≈0 = pairing holds;
                                # large = audio reaches its track ahead of video → desync) and
                                # inter-frame arrival jitter (large gaps = bursty SoulX delivery).
                                _ff_ms = (self._pace_first_frame_t - _t0) * 1000.0 if _t0 else 0.0
                                _fa_ms = (
                                    (self._pace_first_audio_t - _t0) * 1000.0
                                    if (_t0 and self._pace_first_audio_t is not None) else 0.0
                                )
                                _av_off_ms = (
                                    (self._pace_first_audio_t - self._pace_first_frame_t) * 1000.0
                                    if self._pace_first_audio_t is not None else 0.0
                                )
                                logger.info(
                                    "[soulx-pacing] frames={} delivered_fps={:.1f} "
                                    "speaking_wall_s={:.2f} audio_delivered_s={:.2f} ratio={:.2f} "
                                    "max_frame_gap_ms={:.0f} large_gaps={} "
                                    "first_frame_ms={:.0f} first_audio_ms={:.0f} av_start_offset_ms={:.0f} "
                                    "peak_audio_push_rate={:.1f} max_push_burst_ms={:.0f}",
                                    self._pace_frames, _fps, _wall, _audio_s, _ratio,
                                    self._pace_max_gap_s * 1000.0, self._pace_gap_count,
                                    _ff_ms, _fa_ms, _av_off_ms,
                                    self._peak_push_rate, self._max_push_burst_ms,
                                )
                            self._pace_frames = 0
                            self._pace_audio_bytes = 0
                            self._pace_first_frame_t = None
                            self._pace_last_frame_t = None
                            self._pace_prev_frame_t = None
                            self._pace_max_gap_s = 0.0
                            self._pace_gap_count = 0
                            self._pace_first_audio_t = None
                            self._push_window.clear()
                            self._peak_push_rate = 0.0
                            self._max_push_burst_ms = 0.0
                            if self._eof_task and not self._eof_task.done():
                                self._eof_task.cancel()
                            self._eof_task = None
                            # Tell BargeInManager the bot finished speaking
                            await self.push_frame(
                                BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM
                            )
                            self._publish_speaking(False)
                            logger.info("[soulx] utterance complete — idle resumed")

                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break

        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.error("[soulx] video consumer crashed: {} — resetting state", exc)
            self._ignore_binary = False
            if self._speaking or self._speak_start_sent:
                self._flush_resampler()
                self._pcm_buffer.clear()
                self._pending_audio = None
                self._speaking         = False
                self._speak_start_sent = False
                await self.push_frame(
                    BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM
                )
                self._publish_speaking(False)

    # ── Frame processing ───────────────────────────────────────────────────────

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            try:
                await self._connect()
            except Exception as exc:
                logger.error("[soulx] connection failed at startup: {}", exc)
            await self.push_frame(frame, direction)

        elif isinstance(frame, LLMFullResponseStartFrame):
            self._llm_response_done = False
            # Switch server to thinking frames while the LLM processes.
            logger.info("[soulx] LLMFullResponseStartFrame received ws_ok={} speak_start_sent={}",
                        bool(self._ws and not self._ws.closed), self._speak_start_sent)
            if self._ws and not self._ws.closed and not self._speak_start_sent:
                try:
                    await self._ws.send_str(json.dumps({"type": "thinking"}))
                    self._thinking_started = True
                    logger.info("[soulx] thinking message sent")
                except Exception as exc:
                    logger.warning("[soulx] thinking send failed: {}", exc)
            await self.push_frame(frame, direction)

        elif isinstance(frame, LLMFullResponseEndFrame):
            # LLM is fully done — no more TTS batches are coming.
            # Use a SHORT 50ms debounce rather than sending EOF immediately.
            # When a barge-in cancels the brain, the pipeline order is:
            #   LLMFullResponseEndFrame (from cancelled brain's finally)
            #   InterruptionFrame       (ALWAYS-CLEAR, arrives ~1ms later)
            # The InterruptionFrame handler cancels _eof_task, preventing EOF
            # from being sent for the cancelled brain. Without the debounce, EOF
            # fires immediately and SoulX animates the whole cancelled response
            # before the interrupt reaches it.
            self._llm_response_done = True
            if self._speak_start_sent and self._ws and not self._ws.closed:
                if self._eof_task and not self._eof_task.done():
                    self._eof_task.cancel()
                self._eof_task = asyncio.create_task(self._deferred_eof(0.05))
            await self.push_frame(frame, direction)

        elif isinstance(frame, TTSAudioRawFrame):
            if not self._speak_start_sent:
                # Wait for the WebRTC client to connect before starting inference.
                # The greeting TTS fires immediately on pipeline start, but the
                # WebRTC transport isn't ready for ~2.7s (ICE + DTLS negotiation).
                # Any speaking frames pushed before on_client_connected are dropped
                # by the transport's bounded queue — causing the first words to be cut.
                _t_audio = time.monotonic() if self._metrics_on else 0.0
                await self._client_connected.wait()
                if self._metrics_on:
                    logger.info(
                        "[soulx-latency] b0 first_audio→speak_start "
                        "(incl. client-connected gate): {:.1f}ms",
                        (time.monotonic() - _t_audio) * 1000.0,
                    )
                # First audio of a new utterance — switch server to speaking mode.
                if self._ws and not self._ws.closed:
                    try:
                        await self._ws.send_str(json.dumps({"type": "speak_start"}))
                    except Exception as exc:
                        logger.error("[soulx] speak_start failed: {}", exc)
                        return
                self._speak_start_sent  = True
                self._thinking_started  = False
                if self._metrics_on:
                    self._t_speak_start = time.monotonic()
                    self._first_speaking_frame_logged = False
                # Tell BargeInManager the bot is speaking (early signal, before frames arrive)
                await self.push_frame(
                    BotStartedSpeakingFrame(), FrameDirection.UPSTREAM
                )
                self._publish_speaking(True)

            # Forward float32 PCM to SoulX for inference.
            # The server will echo this audio back alongside each video frame (0x01 messages),
            # so no _pcm_buffer buffering is needed on the client side.
            if self._ws and not self._ws.closed:
                f32 = (
                    np.frombuffer(frame.audio, dtype=np.int16) / 32768.0
                ).astype(np.float32)
                try:
                    await self._ws.send_bytes(f32.tobytes())
                except Exception as exc:
                    logger.error("[soulx] audio send failed: {}", exc)

            # Cancel any pending deferred EOF — more audio is arriving
            if self._eof_task and not self._eof_task.done():
                self._eof_task.cancel()
                self._eof_task = None

        elif isinstance(frame, TTSStoppedFrame):
            if self._speak_start_sent and self._ws and not self._ws.closed:
                if self._llm_response_done:
                    # LLM finished AND TTS finished — send EOF with short debounce so
                    # an InterruptionFrame (barge-in) can still cancel it if needed.
                    if self._eof_task and not self._eof_task.done():
                        self._eof_task.cancel()
                    self._eof_task = asyncio.create_task(self._deferred_eof(0.05))
                else:
                    # LLM still generating — do NOT send EOF yet.
                    # 500ms was too short: if LLM takes 4s and TTS batch 1 fires at
                    # t=2.5s, the 500ms debounce fires at t=3.0s before batch 2 arrives.
                    # Use 10s safety net; LLMFullResponseEndFrame cancels it immediately
                    # when LLM actually finishes (replacing with a 50ms debounce).
                    if self._eof_task and not self._eof_task.done():
                        self._eof_task.cancel()
                    self._eof_task = asyncio.create_task(self._deferred_eof(10.0))
            await self.push_frame(frame, direction)

        elif isinstance(frame, InterruptionFrame):
            # Barge-in: tell server to discard queued audio immediately.
            # _ignore_binary: discard any speaking frames still in transit from the
            # old utterance (they would show speaking animation with silence audio).
            self._llm_response_done = False  # reset for next LLM turn
            self._ignore_binary = True
            self._pending_audio = None  # drop any unpaired chunk from the old utterance
            # If bot was in thinking state (not yet speaking), cancel thinking → idle.
            if self._thinking_started and not self._speak_start_sent:
                self._thinking_started = False
                if self._ws and not self._ws.closed:
                    try:
                        await self._ws.send_str(json.dumps({"type": "thinking_cancel"}))
                    except Exception:
                        pass
            if self._speak_start_sent:
                if self._eof_task and not self._eof_task.done():
                    self._eof_task.cancel()
                self._eof_task = None
                if self._ws and not self._ws.closed:
                    try:
                        await self._ws.send_str(json.dumps({"type": "interrupt"}))
                        # Switch the avatar to THINKING immediately so it never freezes
                        # on the last speaking frame while the new turn is prepared. The
                        # server (now preemptible) aborts the in-flight batch within ~1
                        # frame and returns to idle, then this flips it straight to
                        # thinking frames — so barge-in reads as instant rather than a
                        # multi-hundred-ms freeze. (The new turn's LLMFullResponseStart
                        # will re-send "thinking" harmlessly.)
                        await self._ws.send_str(json.dumps({"type": "thinking"}))
                        self._thinking_started = True
                    except Exception:
                        pass
                self._pcm_buffer.clear()
                self._flush_resampler()
                self._speaking         = False
                self._speak_start_sent = False
                # Reset per-utterance pacing/probe counters so the barged utterance
                # doesn't bleed into the next one's [soulx-pacing] line (otherwise
                # frames/gaps/first-frame span the barge → negative/garbage metrics).
                self._first_speaking_frame_logged = False
                self._pace_frames = 0
                self._pace_audio_bytes = 0
                self._pace_first_frame_t = None
                self._pace_last_frame_t = None
                self._pace_prev_frame_t = None
                self._pace_max_gap_s = 0.0
                self._pace_gap_count = 0
                self._pace_first_audio_t = None
                self._push_window.clear()
                self._peak_push_rate = 0.0
                self._max_push_burst_ms = 0.0
                await self.push_frame(
                    BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM
                )
                self._publish_speaking(False)
            await self.push_frame(frame, direction)

        elif isinstance(frame, (EndFrame, CancelFrame)):
            await self._disconnect()
            self._flush_resampler()
            await self.push_frame(frame, direction)

        else:
            await self.push_frame(frame, direction)
