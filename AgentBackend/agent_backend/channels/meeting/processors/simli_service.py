"""Drop-in replacement for pipecat's SimliVideoService using simli-ai 2.0.x API.

Why this exists
---------------
pipecat-ai 0.0.89 ships a `SimliVideoService` written for simli-ai 0.1.x. That
version of the Python client was retired; the live Simli servers now only serve
the 2.0.x protocol (different endpoints, `x-simli-api-key` header, JWT tokens).
Importing the old class against simli-ai 2.0.x causes AttributeError or HTTP 403.

This file implements the identical pipecat FrameProcessor interface — same frame
types, same position in the pipeline, same lifecycle — but drives the 2.0.x
SimliClient which works with the current Simli API.

Key differences (old API → new API)
-------------------------------------
  SimliClient(config, ...)          → SimliClient(api_key, config, ...)
  SimliConfig(apiKey=..., ...)      → SimliConfig(faceId=...)  [no apiKey field]
  client.Initialize()               → client.start()           [async context manager]
  client.playImmediate(audio)       → client.sendImmediate(audio)
  stop() did NOT reset self.starting → 2.0.x stop() resets self.starting = False first

Frame interface (unchanged)
----------------------------
  Input  (from TTS):  TTSAudioRawFrame, TTSStoppedFrame, InterruptionFrame,
                      UserStartedSpeakingFrame, StartFrame, EndFrame, CancelFrame
  Output (to SmallWebRTCTransport.output()):
                      TTSAudioRawFrame  (Simli's avatar voice, lip-synced)
                      OutputImageRawFrame  (Simli's avatar video frames, RGB)
"""

from __future__ import annotations

import asyncio
import time

import numpy as np
from loguru import logger

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    OutputImageRawFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor, StartFrame

try:
    from av.audio.frame import AudioFrame
    from av.audio.resampler import AudioResampler
    from simli import SimliClient, SimliConfig
    from simli.events import SimliEvent
except ModuleNotFoundError as e:
    logger.error(f"simli-ai 2.0.x not installed: {e}")
    raise


from agent_backend.channels.meeting.processors.av_sync import TIME_BASE_META_KEY
from agent_backend.channels.meeting.events import BotSpeakingEvent, EventBus
from agent_backend.config import get_settings


def _frame_to_rgb_bytes(video_frame) -> tuple[bytes, int, int]:
    """Convert a Simli av.VideoFrame to packed RGB24 bytes + (width, height).

    Runs in a worker thread (via asyncio.to_thread) so this CPU-bound, GIL-holding
    work never blocks the event loop's realtime audio/RTP delivery. Using
    to_ndarray(format='rgb24') is cheaper than the old to_rgb().to_image() PIL path.
    """
    arr = video_frame.to_ndarray(format="rgb24")  # (H, W, 3) uint8
    return arr.tobytes(), video_frame.width, video_frame.height


class AgentSimliVideoService(FrameProcessor):
    """Pipecat FrameProcessor that drives simli-ai 2.0.x.

    Usage (identical to the old SimliVideoService):
        from agent_backend.channels.meeting.processors.simli_service import AgentSimliVideoService
        from simli import SimliConfig

        simli_config = SimliConfig(faceId=face_id, maxSessionLength=1800, maxIdleTime=120)
        service = AgentSimliVideoService(
            api_key=simli_api_key,
            simli_config=simli_config,
            is_trinity_avatar=False,
        )
    """

    def __init__(
        self,
        *,
        api_key: str,
        simli_config: SimliConfig,
        is_trinity_avatar: bool = False,
        simli_url: str = "https://api.simli.ai",
        out_sample_rate: int = 48000,
        bus: EventBus | None = None,
    ) -> None:
        super().__init__()

        # 2.0.x: api_key is the FIRST positional arg; SimliConfig has no apiKey field
        self._simli_client = SimliClient(
            api_key=api_key,
            config=simli_config,
            simliURL=simli_url,
        )

        self._is_trinity_avatar = is_trinity_avatar
        self._audio_buffer = bytearray()
        self._previously_interrupted = is_trinity_avatar
        self._initialized = False

        # Output rate for the avatar audio we push downstream. Must match the
        # transport's audio_out_sample_rate (48 kHz for WebRTC) so there is NO
        # resample between here and the wire. Simli emits 48 kHz natively, so
        # requesting 48 kHz from its iterator is also a no-op resample → cleanest.
        self._out_sample_rate = out_sample_rate

        # Simli REQUIRES 16 kHz mono input; this resampler feeds TTS audio to it.
        # (Benign: TTS is already 16 kHz, so this is a no-op pass-through.)
        self._simli_resampler = AudioResampler("s16", "mono", 16000)

        # Output resampler: Simli emits 48 kHz STEREO; the output transport wants
        # MONO at out_sample_rate (48 kHz). Let av's resampler handle the channel
        # downmix + (no-op) rate match correctly — hand-rolled numpy reshaping of
        # the raw frame is fragile and was producing truncated/garbled audio.
        self._out_resampler = AudioResampler("s16", "mono", out_sample_rate)

        # GLITCH FIX: pipecat's RawAudioTrack.add_audio_bytes() REQUIRES audio in
        # exact 10ms-multiple chunks (raises ValueError otherwise) and breaks the
        # stream into 10ms frames internally. av's resampler emits variable-size
        # frames, so we accumulate into this byte buffer and only emit complete
        # 10ms chunks, carrying the remainder forward. This gives the WebRTC track
        # a clean, evenly-paced stream — no gaps/glitches from odd-sized frames.
        self._chunk_bytes = (out_sample_rate * 10 // 1000) * 2  # 10ms, s16 mono
        self._audio_out_buf = bytearray()

        # Background tasks for consuming Simli output
        self._audio_task: asyncio.Task | None = None
        self._video_task: asyncio.Task | None = None

        # AUTHORITATIVE bot-speaking signal for the SilenceMonitor. We derive it
        # from the TEXT-driven TTSStartedFrame/TTSStoppedFrame that arrive here
        # (TTS sits immediately upstream), NOT from the output transport's
        # audio-derived BotStarted/StoppedSpeakingFrame. The transport flags
        # "bot speaking" on ANY TTSAudioRawFrame (base_output: is_speaking=True
        # for every TTSAudioRawFrame), and Simli re-emits a CONTINUOUS audio
        # stream (idle silence frames too) — so its queue never drains and
        # BotStoppedSpeakingFrame never fires → the silence monitor never ARMS
        # → no follow-up nudges. TTSStoppedFrame fires when TTS finishes
        # synthesising the reply text, which is the real "done talking" moment.
        self._bus = bus
        # Simli buffers a little audio ahead of the wire, so the avatar is still
        # lip-syncing the tail of the reply for a beat after TTSStopped. Hold the
        # "bot stopped" signal this long so the silence clock starts only once the
        # avatar has actually gone quiet (avoids nudging over the reply's tail).
        # NOTE: this TTS-frame path is only a FALLBACK. When Simli's own
        # SPEAK/SILENT server events register successfully, those drive the
        # bot-speaking signal instead (authoritative; covers the whole reply +
        # playout) and this drain path is disabled — see _simli_events_registered.
        self._bot_stop_drain_s = 0.8
        self._bot_stop_task: asyncio.Task | None = None
        # True once Simli SPEAK/SILENT callbacks are registered; when set, the
        # TTSStarted/TTSStopped handlers stop publishing BotSpeakingEvent so we
        # don't emit two conflicting signals.
        self._simli_events_registered = False

        # True only once start() fully succeeded AND the underlying transport's
        # wsConnection exists. Guards every send: simli-ai 2.0.x's send() blindly
        # does `self.Connection.wsConnection.send(...)`, which AttributeErrors if
        # the connection failed to initialize (e.g. Simli out of credits /
        # concurrent-session limit / transient connect failure). Without this
        # guard, every TTS audio frame crashes the pipeline with a stack trace.
        self._connected = False

        # ── Startup-latency profiling (observe-only; gated by AVATAR_ENABLE_METRICS) ──
        # Parity stamp for the SoulX A/B: time from the first TTS audio of an
        # utterance to the next avatar video frame pushed downstream. Simli streams
        # video continuously, so this should be ~one frame interval (≈0) — confirming
        # Simli has no per-utterance renderer spin-up, unlike SoulX's b1+b2.
        self._metrics_on: bool = get_settings().avatar_enable_metrics
        self._t_utt_first_audio: float | None = None
        self._utt_frame_logged: bool = True

        # Pass-2 speech-pacing accounting (observe-only): output audio delivered vs
        # wall-clock during a speaking window (baseline vs SoulX's [soulx-pacing]).
        # Bounded by SPEAK/SILENT (TTSStarted/Stopped fallback). ratio ≈1.0 expected.
        self._pace_speaking: bool = False
        self._pace_audio_bytes: int = 0
        self._pace_t0: float | None = None
        self._pace_tlast: float | None = None

    # ------------------------------------------------------------------
    # Bot-speaking signal for SilenceMonitor
    #
    # PRIMARY: Simli's server-side SPEAK/SILENT events (authoritative — they fire
    # when the avatar ACTUALLY starts/stops producing speech, covering the whole
    # multi-sentence reply and the playout buffer).
    # FALLBACK: TTSStarted/TTSStopped (+ drain) if the callbacks can't register.
    # ------------------------------------------------------------------

    async def _on_simli_speak(self) -> None:
        """Simli reports the avatar started speaking → bot is speaking."""
        self._cancel_bot_stop()
        if self._bus is not None:
            self._bus.publish(BotSpeakingEvent(speaking=True))
        self._pace_start()
        logger.debug("[avatar-video] Simli SPEAK → bot_speaking=True")

    async def _on_simli_silent(self) -> None:
        """Simli reports its audio buffer is depleted → avatar genuinely quiet.

        This is the REAL end-of-reply moment (after the FULL reply + playout),
        so the silence clock starts here. No fixed drain needed — Simli already
        waited for its buffer to empty.
        """
        if self._bus is not None:
            self._bus.publish(BotSpeakingEvent(speaking=False))
        self._pace_end()
        logger.debug("[avatar-video] Simli SILENT → bot_speaking=False")


    def _schedule_bot_stop(self) -> None:
        """Publish BotSpeakingEvent(speaking=False) after a short playout drain.

        TTSStoppedFrame means the TEXT is fully synthesised, but Simli is still
        lip-syncing the buffered tail for a beat. We wait `_bot_stop_drain_s` so
        the silence clock only starts once the avatar has actually gone quiet.
        A new TTSStartedFrame (next sentence) cancels this via _cancel_bot_stop.
        """
        if self._bus is None:
            return
        self._cancel_bot_stop()
        self._bot_stop_task = asyncio.create_task(self._bot_stop_after_drain())

    async def _bot_stop_after_drain(self) -> None:
        try:
            await asyncio.sleep(self._bot_stop_drain_s)
            if self._bus is not None:
                self._bus.publish(BotSpeakingEvent(speaking=False))
        except asyncio.CancelledError:
            raise

    def _cancel_bot_stop(self) -> None:
        if self._bot_stop_task is not None and not self._bot_stop_task.done():
            self._bot_stop_task.cancel()
        self._bot_stop_task = None

    # ── Pass-2 speech-pacing (observe-only) ───────────────────────────────────
    def _pace_start(self) -> None:
        """Begin a speaking-window accounting (first caller wins; idempotent)."""
        if not self._metrics_on or self._pace_speaking:
            return
        self._pace_speaking = True
        self._pace_audio_bytes = 0
        self._pace_t0 = None
        self._pace_tlast = None

    def _pace_end(self) -> None:
        """Close the window and log output-audio delivered vs wall-clock."""
        if not self._metrics_on or not self._pace_speaking:
            return
        self._pace_speaking = False
        if self._pace_t0 is not None and self._pace_tlast is not None:
            wall = self._pace_tlast - self._pace_t0
            audio_s = self._pace_audio_bytes / 2 / self._out_sample_rate  # int16 mono
            ratio = (audio_s / wall) if wall > 0 else 0.0
            logger.info(
                "[simli-pacing] audio_delivered_s={:.2f} speaking_wall_s={:.2f} ratio={:.2f}",
                audio_s, wall, ratio,
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _start_connection(self) -> None:
        """Connect to Simli (called on StartFrame). 2.0.x uses start() not Initialize()."""
        if self._initialized:
            return
        self._initialized = True  # mark attempted so we don't retry per-frame
        # start() is the 2.0.x replacement for Initialize().
        # It internally calls /compose/token (with x-simli-api-key header) and
        # /compose/ice, then connects to /compose/webrtc/p2p with session_token
        # in the URL — all properly authenticated. Raises on failure.
        try:
            await self._simli_client.start()
        except Exception as e:  # noqa: BLE001
            # Common real causes: Simli account out of credits, concurrent-session
            # limit reached, or a transient connect failure. Leave _connected False
            # so every downstream send() is skipped (no AttributeError crash spam),
            # and surface ONE clear error instead of a stack trace per audio frame.
            #
            # NOTE: simli-ai raises a BARE ValueError() (empty message) from
            # critical_exceptions.SimliExceptions._missing_ when the server sends
            # an empty/blank critical-event code — which in practice maps to an
            # ACCOUNT/BILLING problem (its enum is INVALID_API_KEY / BILLING_ERROR
            # / MISSING_BILLING_INFO / INVALID_FACE_ID / UNKNOWN_ERROR, and the
            # default case is BILLING_ERROR). So a blank ValueError here is NOT an
            # app bug — it's Simli rejecting the session (credits/billing/limit/key).
            cause = str(e).strip() or (
                "blank critical code from Simli — almost always an ACCOUNT issue "
                "(out of credits / billing / concurrent-session limit / bad API key "
                "or face id). Check the Simli dashboard."
            )
            logger.error(
                "[avatar-video] Simli connection FAILED — avatar will have no "
                f"audio/video this session. Cause: {type(e).__name__}: {cause}"
            )
            return

        self._connected = True

        # AUTHORITATIVE who-is-speaking signal, straight from Simli's server.
        # Simli emits SPEAK when the avatar actually starts producing speech audio
        # and SILENT when its audio buffer is fully depleted (avatar genuinely
        # stopped). This is the REAL "the avatar finished talking" moment — it
        # already accounts for the full multi-sentence reply AND Simli's playout
        # buffer, so the silence clock starts only once the avatar is truly quiet.
        # (Replaces the fragile TTSStarted/TTSStopped + fixed-drain heuristic,
        # which fired between sentences mid-explanation.)
        if self._bus is not None:
            try:
                self._simli_client.registerEventCallback(
                    SimliEvent.SPEAK, self._on_simli_speak
                )
                self._simli_client.registerEventCallback(
                    SimliEvent.SILENT, self._on_simli_silent
                )
                self._simli_events_registered = True
                logger.info("[avatar-video] registered Simli SPEAK/SILENT callbacks")
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"[avatar-video] could not register Simli SPEAK/SILENT "
                    f"callbacks ({e}); falling back to TTS-frame bot-speaking signal"
                )

        await self._simli_client.sendSilence()
        # Start consumers immediately — Simli streams idle audio+video on connect.
        self._audio_task = self.create_task(self._consume_audio())
        self._video_task = self.create_task(self._consume_video())

    async def _stop(self) -> None:
        """Stop Simli and cancel background tasks."""
        self._connected = False
        self._cancel_bot_stop()
        await self._simli_client.stop()
        if self._audio_task:
            await self.cancel_task(self._audio_task)
            self._audio_task = None
        if self._video_task:
            await self.cancel_task(self._video_task)
            self._video_task = None

    # ------------------------------------------------------------------
    # Output consumers (background tasks)
    # ------------------------------------------------------------------

    async def _consume_audio(self) -> None:
        """Pull avatar audio from Simli and push it downstream at out_sample_rate.

        Simli emits 48 kHz stereo frames. We request the iterator's native rate
        (48 kHz, no resample there) and run each frame through av's resampler to
        get clean MONO s16 at out_sample_rate (48 kHz) — av handles the stereo→
        mono downmix and frame sizing correctly. The output transport is also
        48 kHz, so there is no further resample on the wire.
        """
        async for audio_frame in self._simli_client.getAudioStreamIterator(
            targetSampleRate=self._out_sample_rate
        ):
            for resampled in self._out_resampler.resample(audio_frame):
                arr = resampled.to_ndarray()
                if arr.size == 0:
                    continue
                # Accumulate, then emit ONLY in exact 10ms-multiple chunks so the
                # WebRTC RawAudioTrack gets evenly-sized frames (it rejects
                # odd-sized audio and re-chunks at 10ms). Silent frames are kept —
                # Simli's track is continuous; dropping silence desyncs the clock.
                self._audio_out_buf.extend(arr.astype(np.int16).tobytes())
                while len(self._audio_out_buf) >= self._chunk_bytes:
                    # Emit a whole number of 10ms chunks in one frame (cheap and
                    # keeps the queue shallow); RawAudioTrack splits it to 10ms.
                    n = (len(self._audio_out_buf) // self._chunk_bytes) * self._chunk_bytes
                    out = bytes(self._audio_out_buf[:n])
                    del self._audio_out_buf[:n]
                    if self._metrics_on and self._pace_speaking:
                        _now = time.monotonic()
                        if self._pace_t0 is None:
                            self._pace_t0 = _now
                        self._pace_tlast = _now
                        self._pace_audio_bytes += len(out)
                    await self.push_frame(
                        TTSAudioRawFrame(
                            audio=out,
                            sample_rate=self._out_sample_rate,
                            num_channels=1,
                        )
                    )
        # If this loop EXITS, Simli stopped sending audio (stream ended/closed).
        # Seeing this mid-conversation means the Simli connection dropped.
        logger.warning("[avatar-video] audio stream iterator ENDED (Simli stopped sending audio)")

    async def _consume_video(self) -> None:
        """Pull video from Simli and push OutputImageRawFrame downstream.

        Note: video is NOT gated on the audio resampler. Simli emits idle/silent
        avatar video immediately on connect (when handleSilence=True), so we want
        the avatar visible the moment the room is up — not only after the first
        TTS reply. (The audio consumer still waits for the resampler since it
        needs the pipeline's sample rate, which is only known once TTS sends audio.)
        """
        import time as _time

        first = True
        # Measure Simli's ACTUAL delivered FPS over the first ~2s window so we
        # can drive the WebRTC output track at the matching rate (the source of
        # the lip-sync drift is the output track being hardwired to 30fps while
        # Simli delivers a different rate).
        fps_t0 = _time.monotonic()
        fps_count = 0
        fps_logged = False

        async for video_frame in self._simli_client.getVideoStreamIterator(targetFormat="rgb24"):
            # CRACKLE / "broken-radio" FIX: the RGB conversion below is CPU-bound
            # and GIL-holding (~tens of ms per frame at ~25fps). Done inline on the
            # event loop it periodically froze the loop, so the clock-paced audio
            # output track (RawAudioTrack.recv) fired late, drained its 10ms queue,
            # and emitted silence mid-speech → intermittent audio dropouts. Offload
            # the conversion to a worker thread (mirrors aiortc's own encoder
            # offload) so the audio/RTP path is never blocked. `to_ndarray` is also
            # cheaper than the old to_rgb().to_image() PIL path.
            image_bytes, w, h = await asyncio.to_thread(_frame_to_rgb_bytes, video_frame)
            frame = OutputImageRawFrame(
                image=image_bytes,
                size=(w, h),
                format="RGB",
            )
            # A/V SYNC PASSTHROUGH: carry Simli's REAL pts + time_base so the
            # output video track stamps the outgoing frame with the source
            # timeline (not a fabricated fps). Audio rides real sample-count pts,
            # so both share one clock → WebRTC RTCP lip-sync keeps them aligned
            # with zero drift. (av_sync.enable_timestamp_passthrough reads these.)
            frame.pts = video_frame.pts
            if video_frame.time_base is not None:
                frame.metadata[TIME_BASE_META_KEY] = video_frame.time_base
            await self.push_frame(frame)

            if (
                self._metrics_on
                and not self._utt_frame_logged
                and self._t_utt_first_audio is not None
            ):
                logger.info(
                    "[simli-latency] first TTS audio→first avatar frame: {:.1f}ms",
                    (time.monotonic() - self._t_utt_first_audio) * 1000.0,
                )
                self._utt_frame_logged = True

            fps_count += 1
            if first:
                first = False
                logger.info(
                    "[avatar-video] Simli avatar video streaming "
                    f"({video_frame.width}x{video_frame.height})"
                )
            if not fps_logged:
                elapsed = _time.monotonic() - fps_t0
                if elapsed >= 2.0:
                    measured = fps_count / elapsed
                    logger.info(
                        f"[avatar-video] MEASURED Simli video FPS ≈ {measured:.1f} "
                        f"({fps_count} frames / {elapsed:.1f}s) — set "
                        f"AVATAR_VIDEO_FPS to this rounded value for lip-sync"
                    )
                    fps_logged = True

    # ------------------------------------------------------------------
    # Frame processor
    # ------------------------------------------------------------------

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            # Connect to Simli before passing StartFrame downstream so that
            # by the time the output transport is ready, Simli is already running.
            await self._start_connection()

        elif isinstance(frame, TTSStartedFrame):
            # FALLBACK only — if Simli's SPEAK/SILENT events registered, they own
            # the bot-speaking signal (authoritative for the whole reply) and we
            # stay out of the way. Otherwise (callbacks unavailable) drive it from
            # TTS: reply audio starting → bot is speaking; cancel any pending drain
            # so back-to-back sentences don't arm the monitor between them.
            if not self._simli_events_registered:
                self._cancel_bot_stop()
                if self._bus is not None:
                    self._bus.publish(BotSpeakingEvent(speaking=True))
            # Re-arm the per-utterance startup stamp (see __init__).
            if self._metrics_on:
                self._t_utt_first_audio = None
                self._utt_frame_logged = False
            # Begin pacing window (idempotent; _on_simli_speak also calls it when
            # SPEAK/SILENT events are registered).
            self._pace_start()

        elif isinstance(frame, TTSAudioRawFrame):
            # Skip silently if Simli never connected — otherwise send() would
            # AttributeError on the missing wsConnection for every frame.
            if not self._connected:
                return
            if self._metrics_on and self._t_utt_first_audio is None:
                self._t_utt_first_audio = time.monotonic()
            try:
                old_frame = AudioFrame.from_ndarray(
                    np.frombuffer(frame.audio, dtype=np.int16)[None, :],
                    layout="mono" if frame.num_channels == 1 else "stereo",
                )
                old_frame.sample_rate = frame.sample_rate

                # Resample the TTS audio to Simli's required 16 kHz mono input.
                resampled = self._simli_resampler.resample(old_frame)
                for rf in resampled:
                    audio_bytes = rf.to_ndarray().astype(np.int16).tobytes()
                    if self._previously_interrupted:
                        self._audio_buffer.extend(audio_bytes)
                        if len(self._audio_buffer) >= 128_000:
                            # Flush resampler
                            for flush_rf in self._simli_resampler.resample(None):
                                self._audio_buffer.extend(
                                    flush_rf.to_ndarray().astype(np.int16).tobytes()
                                )
                            # 2.0.x: sendImmediate replaces playImmediate
                            await self._simli_client.sendImmediate(bytes(self._audio_buffer))
                            self._previously_interrupted = False
                            self._audio_buffer = bytearray()
                    else:
                        await self._simli_client.send(audio_bytes)
            except Exception as e:
                logger.exception(f"{self} exception processing TTSAudioRawFrame: {e}")
            return  # Don't push TTSAudioRawFrame downstream again (Simli re-emits it)

        elif isinstance(frame, TTSStoppedFrame):
            if self._connected:
                try:
                    if self._previously_interrupted and self._audio_buffer:
                        await self._simli_client.sendImmediate(bytes(self._audio_buffer))
                        self._previously_interrupted = False
                        self._audio_buffer = bytearray()
                except Exception as e:
                    logger.exception(f"{self} exception on TTSStoppedFrame: {e}")
            # FALLBACK only — when Simli SPEAK/SILENT events aren't available,
            # schedule the "bot stopped" signal after a short drain (Simli is
            # still playing the tail). When they ARE registered, Simli's SILENT
            # event is the real end-of-playout marker, so we don't touch it here.
            if not self._simli_events_registered:
                self._schedule_bot_stop()
                self._pace_end()  # fallback path; primary path ends via _on_simli_silent
            return

        elif isinstance(frame, (EndFrame, CancelFrame)):
            await self._stop()

        elif isinstance(frame, (InterruptionFrame, UserStartedSpeakingFrame)):
            # Confirmed barge / user turn → Simli's queued audio is discarded, so
            # the avatar goes quiet at once. Drop any pending drained "bot stopped"
            # signal: the silence monitor already resets on this turn, and a late
            # stale signal could mis-arm it. (We don't publish speaking=False here
            # because the monitor's TurnEvent/BargeInEvent handling owns that path.)
            self._cancel_bot_stop()
            self._pace_end()  # barge cut the speaking window short
            # clearBuffer() tells Simli to discard queued audio on a confirmed barge.
            if self._connected:
                try:
                    if not self._previously_interrupted:
                        await self._simli_client.clearBuffer()
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[avatar-video] clearBuffer failed: {e}")
                self._previously_interrupted = self._is_trinity_avatar

        await self.push_frame(frame, direction)
