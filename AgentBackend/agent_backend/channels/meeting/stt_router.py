"""Per-speaker STT router — one recognizer per participant track.

THE BUG THIS FIXES
------------------
The meeting pipeline used a SINGLE `make_stt()` instance for the whole room.
But a LiveKit room delivers `UserAudioRawFrame`s from EVERY participant
(counsellor + candidate + the agent's own published audio), interleaved on one
processor. Azure's `AzureSTTService` writes all of it into ONE
`PushAudioInputStream` and ignores `user_id` — so two human voices get
byte-interleaved into a single continuous recognizer as the VAD opens/closes per
speaker. Azure then tries to decode the mixed stream as one utterance, which
produces exactly the observed failure: ~50% accuracy and hallucinated words.
The phone (voice) channel never hits this because it has only one track.

THE FIX
-------
Give each speaker their OWN recognizer. This router:
  * lazily builds one STT service per distinct human `user_id` (participant SID),
  * routes each `UserAudioRawFrame` to ONLY that speaker's STT,
  * broadcasts non-audio frames (Start/Stop/VAD/Cancel/End…) to every child so
    each recognizer still segments correctly,
  * DROPS the agent's own track entirely (its TTS must never be transcribed back
    as "user speech" — another big hallucination source),
  * bubbles each child's `TranscriptionFrame` (already carrying the right
    `user_id`, because that child only ever saw one speaker) up through this
    router, so the downstream `MeetingAgentBridge` attribution + gate are
    unchanged.

Each child STT is a real `FrameProcessor`: we `setup()` it with the same
`FrameProcessorSetup` the router got, and `link()` it to a tiny collector that
re-emits whatever the child pushes (transcripts, interim, error frames) out of
this router. The child is created lazily on a speaker's first audio frame, so
rooms with 2 or 5 humans just spin up 2 or 5 recognizers automatically.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

from pipecat.frames.frames import (
    AudioRawFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InterimTranscriptionFrame,
    StartFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import (
    FrameDirection,
    FrameProcessor,
    FrameProcessorSetup,
)
from pipecat.services.stt_service import STTService

from agent_backend.infra import get_logger

log = get_logger(__name__)

# Factory that builds a FRESH STT service (one per speaker). Pass `make_stt`.
STTFactory = Callable[[], STTService]


class _Collector(FrameProcessor):
    """Tail of each child STT chain. A child STT pushes its results here; we
    bubble ONLY the transcription results out of the parent router via `emit`.

    CRITICAL: we bubble ONLY `TranscriptionFrame` / `InterimTranscriptionFrame`.
    Everything else a child re-pushes — above all the audio frame itself (each
    STTService has `audio_passthrough` and re-pushes every AudioRawFrame), plus
    StartFrame/EndFrame/Cancel/system frames — is DROPPED here, because the
    router already forwards audio + lifecycle frames down the main pipeline once.
    Without this whitelist every audio frame would be pushed downstream twice
    (router + each child), duplicating audio into the VAD/transport and wrecking
    recognition. We also belt-and-braces disable child passthrough on creation."""

    def __init__(
        self,
        emit: Callable[[Frame, FrameDirection], "object"],
        owner_user_id: str = "",
        was_active: "Callable[[str], bool] | None" = None,
    ) -> None:
        super().__init__()
        self._emit = emit
        # The participant this child STT belongs to. The transcript's user_id is
        # FORCED to this, so a transcript can never be attributed to the wrong
        # speaker no matter what Azure stamped internally.
        self._owner = owner_user_id
        # EMISSION-SIDE GUARD (the leak the feed-gate can't close): Azure is a
        # CONTINUOUS recognizer — audio that slipped in earlier (onset lag, a brief
        # hangover, the pre-first-event fail-open window) stays buffered and gets
        # FINALIZED later, typically at the end-of-turn pause. Gating the FEED
        # can't retract it. So at emission we ALSO ask the router "was this owner
        # an SFU-active speaker recently?" — if not, this transcript is a
        # late-finalized echo of someone else → DROP it. None → no guard.
        self._was_active = was_active

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        # ONLY transcripts escape the child chain. Audio + lifecycle are handled
        # by the router; bubbling them here would double them.
        if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
            stamped = getattr(frame, "user_id", "")
            if isinstance(frame, TranscriptionFrame):
                # debug: duplicates the bridge's ">>> <speaker> text=..." line.
                log.debug(
                    "[meeting-stt] child transcript",
                    owner=self._owner, stamped_user_id=stamped, text=frame.text,
                )
            # EMISSION GUARD: drop a FINAL transcript from an owner the SFU has not
            # reported speaking recently — it's late-finalized recaptured echo, not
            # this person talking. Interim frames pass (they don't reach the brain
            # and the final is the one that matters). Fail-open if no guard wired.
            if (isinstance(frame, TranscriptionFrame) and self._was_active is not None
                    and self._owner):
                try:
                    active_recently = self._was_active(self._owner)
                except Exception:  # noqa: BLE001
                    active_recently = True  # fail-open
                if not active_recently:
                    log.info(
                        "[meeting-stt] dropping late echo transcript (owner not "
                        "SFU-active recently)",
                        owner=self._owner, text=frame.text,
                    )
                    return
            # FORCE the owner as the user_id so attribution is always correct.
            if self._owner:
                try:
                    frame.user_id = self._owner  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass
            await self._emit(frame, direction)


class MeetingSTTRouter(FrameProcessor):
    """Fan audio out to one STT per speaker; fan transcripts back in.

    Args:
        make_stt: factory returning a fresh STT service (e.g. `make_stt`).
        agent_user_id: the agent's own participant identity — its audio frames
            are dropped so the agent never transcribes itself. May be empty if
            unknown (then nothing is excluded, but the agent track usually has a
            distinct SID that simply gets its own harmless recognizer).
        is_agent: optional predicate (user_id) -> bool to identify the agent's
            track by role when the raw id isn't known up front.
    """

    def __init__(
        self,
        *,
        make_stt: STTFactory,
        agent_user_id: str = "",
        is_agent: Callable[[str], bool] | None = None,
        active_speaker_gate: bool = False,
        active_speaker_factor: float = 1.25,
        active_speaker_floor: float = 30.0,
        sfu_gate: bool = False,
        hangover_ms: int = 1200,
        stale_s: float = 1.2,
        lookback_ms: int = 250,
        segmentation_silence_ms: int = 1200,
        watchdog_s: float = 10.0,
    ) -> None:
        super().__init__()
        self._make_stt = make_stt
        self._agent_user_id = (agent_user_id or "").strip()
        self._is_agent = is_agent
        self._stt_by_user: dict[str, STTService] = {}
        self._collector_by_user: dict[str, _Collector] = {}
        self._setup: FrameProcessorSetup | None = None
        self._started = False
        # The StartFrame we must replay into any STT created AFTER startup, so a
        # late-joining speaker's recognizer initialises its sample rate/stream.
        self._start_frame: StartFrame | None = None
        # One-time log of the inbound audio format (sample_rate/channels).
        self._logged_fmt = False
        # DIAGNOSTIC: count audio frames per user_id so we can see if ONE human's
        # voice is being split across MULTIPLE recognizers (→ garbled transcripts).
        self._audio_count_by_user: dict[str, int] = {}
        # Frames actually FED into each speaker's recognizer (post-routing).
        self._fed_by_user: dict[str, int] = {}
        # ── Active-speaker gating (multi-party cross-talk suppression) ────────
        # Each participant's mic also picks up the OTHERS (room bleed), so every
        # recognizer hears a faint copy of everyone and mis-transcribes. We keep a
        # short rolling RMS (EMA) per speaker and only route a frame to its
        # recognizer when that speaker is the DOMINANT (loudest) track — the
        # quieter bleed tracks are dropped. With one speaker this is a no-op.
        self._active_gate = bool(active_speaker_gate)
        # Strict-dominance gate params: a track must be >= factor × the loudest
        # OTHER track AND above the absolute speech floor to be fed. factor>1
        # means a near-equal duplicate copy can never tie its way through.
        self._active_factor = float(active_speaker_factor)
        self._active_floor = float(active_speaker_floor)
        self._rms_ema: dict[str, float] = {}   # user_id → smoothed RMS
        self._gate_dropped: dict[str, int] = {}  # user_id → frames dropped (log)
        # ── SFU server-side active-speaker gate (the PRODUCTION fix) ──────────
        # LiveKit's active_speakers_changed (computed at ingest, immune to the
        # playback-recapture loop) is the source of truth for who is really
        # speaking. We only feed STT for SIDs the SFU reports active (+ a short
        # hangover + leading-edge lookback so onsets/tails aren't clipped). The
        # rtc callback calls set_active_speakers() on the pipeline loop (no lock).
        self._now = time.monotonic
        self._sfu_gate = bool(sfu_gate)
        # Clamp hangover >= Azure end-of-phrase window so an in-phrase pause never
        # opens a silence hole in the push stream the recognizer wouldn't itself
        # close (adversarial fix C).
        self._hangover_s = max(int(hangover_ms), int(segmentation_silence_ms)) / 1000.0
        self._stale_s = float(stale_s)
        self._lookback_ms = int(lookback_ms)
        self._active_speakers: set[str] = set()       # current SFU snapshot (sid|identity)
        self._active_until: dict[str, float] = {}     # id → monotonic hangover deadline
        self._last_active_at: dict[str, float] = {}   # id → last time SFU reported it active
        self._active_last_update: float = 0.0
        self._active_ever_fired: bool = False
        # Per-speaker leading-edge ring buffer: holds recent (downmixed) frames
        # while a SID's gate is closed; flushed on first entry into the active set.
        self._lookback: dict[str, deque] = {}
        # Watchdog: auto-disable if the event never fires within N s of first audio.
        self._first_audio_at: float = 0.0
        self._watchdog_s: float = float(watchdog_s)
        self._watchdog_tripped = False

    # -- active-speaker gating --------------------------------------------
    @staticmethod
    def _rms(audio: bytes) -> float:
        """RMS energy of PCM16 audio (0 on failure)."""
        if not audio:
            return 0.0
        try:
            import audioop

            return float(audioop.rms(audio, 2))
        except Exception:  # noqa: BLE001
            return 0.0

    def _update_rms(self, user_id: str, audio: bytes) -> float:
        """Update + return this track's smoothed (EMA) RMS energy. Called once
        per audio frame for EVERY track so the deterministic dominant-speaker
        pick always compares current levels. EMA smoothing (0.6 prev / 0.4 new)
        so a single quiet/loud blip can't flip the decision frame-by-frame."""
        rms = self._rms(audio)
        prev = self._rms_ema.get(user_id, 0.0)
        ema = prev * 0.6 + rms * 0.4
        self._rms_ema[user_id] = ema
        return ema

    def _is_dominant_speaker(self, user_id: str, audio: bytes) -> bool:
        """Decide whether to feed this track's recognizer — STRICT DOMINANCE.

        THE BUG THIS FIXES (root cause of N-humans→N-transcripts): every
        participant's browser also captures the played-back audio of whoever is
        speaking (AEC is imperfect, remote audio renders to the default speaker),
        so EVERY mic carries a near-FULL-ENERGY copy of the active speaker. The
        earlier relative rule (`ema >= leader*0.55`) passed any track at ≥55% of
        the loudest — so two ~equal-energy copies (ratio ≈ 1.0) BOTH passed and
        BOTH got transcribed. That is arithmetically why it duplicated.

        New rule — only the SINGLE clearly-dominant track is fed:
          - this track must be above an absolute speech floor (else it's silence/
            comfort-noise → drop), AND
          - it must be the leader by a MARGIN (>= dominance_factor × the loudest
            OTHER track). A near-equal duplicate copy fails the margin → dropped.
        So one real speaker + their N-1 re-captured copies → only the original
        (highest-energy, closest mic) feeds STT; the copies are dropped. Genuine
        simultaneous speech degrades to the louder speaker for that window —
        acceptable for 1:1/panel counselling and infinitely better than N-fold
        duplication."""
        rms = self._rms(audio)
        # EMA so a blip/gap doesn't flip the decision frame-by-frame.
        prev = self._rms_ema.get(user_id, 0.0)
        ema = prev * 0.6 + rms * 0.4
        self._rms_ema[user_id] = ema

        # This track effectively silent → never feed (drops comfort noise and a
        # quieter re-captured copy).
        if ema < self._active_floor:
            return False

        # Loudest OTHER track's smoothed energy (exclude self).
        others = [v for k, v in self._rms_ema.items() if k != user_id]
        leader_other = max(others) if others else 0.0

        # Nobody else has meaningful energy → this is the sole speaker → feed.
        if leader_other < self._active_floor:
            return True
        # Both have energy: feed ONLY if this track is the CLEAR leader by margin.
        # A ~equal-energy duplicate of the same speech fails this and is dropped.
        return ema >= leader_other * self._active_factor

    # -- SFU active-speaker gate (production multi-party fix) --------------
    def set_active_speakers(self, ids: set[str]) -> None:
        """Replace the active-speaker allow-set WHOLESALE (snapshot semantics —
        active_speakers_changed always delivers the full current set, never a
        delta). Called synchronously from the rtc callback on the pipeline loop;
        no lock needed. Refreshes the hangover deadline for every active id."""
        now = self._now()
        self._active_speakers = set(ids)
        deadline = now + self._hangover_s
        for _id in ids:
            self._active_until[_id] = deadline
            self._last_active_at[_id] = now   # for the emission-side echo guard
        self._active_last_update = now
        self._active_ever_fired = True

    def was_active_recently(self, user_id: str) -> bool:
        """Emission-side guard for the _Collector: True if this owner was an
        SFU-active speaker within the recent window (segmentation + margin), so
        their FINAL transcript is genuine and not a late-finalized echo.
        FAIL-OPEN when the gate is off or has never fired."""
        if not self._sfu_gate or not self._active_ever_fired:
            return True
        # If the whole signal went stale (event death), don't suppress anyone.
        now = self._now()
        if now - self._active_last_update > self._stale_s:
            return True
        last = self._last_active_at.get(user_id, 0.0)
        # Generous window: a real speaker's last final lands up to ~segmentation
        # after they drop out of the active set; allow hangover + that window.
        return (now - last) <= (self._hangover_s + self._stale_s)

    def _passes_active_gate(self, user_id: str) -> bool:
        """True → this track's audio may be fed to STT right now. FAIL-OPEN only
        on genuine signal ABSENCE (never-fired / stale), NOT on a live empty set.

        Why no empty-set fail-open: when the SFU reports n=0 from a LIVE event it
        means "nobody is speaking right now" (end-of-turn / pause). Admitting
        everyone there is exactly what leaked the duplicate — a SILENT
        participant's recognizer, holding buffered echo of the real speaker,
        would finalize that echo into a ghost transcript right after the speaker
        stopped (observed: Host emitted the speaker's words at the n=0 tick). So
        on a live empty set we feed NOBODY; each real speaker's trailing audio is
        already covered by their per-SID hangover deadline. True event-death is
        handled by the staleness layer below, which DOES fail open."""
        # Layer 1: gate off, or no event has ever fired (protects opening words
        # and any env where the signal is delayed/absent).
        if not self._sfu_gate or not self._active_ever_fired:
            return True
        now = self._now()
        # Layer 2: event fired once then went stale (genuine death) → fail open.
        if now - self._active_last_update > self._stale_s:
            return True
        # Normal path: active now, or still within this SID's hangover tail.
        # (A live empty set falls through to here → not active, no hangover →
        # dropped. That is the fix: the silent track's buffered echo is NOT fed.)
        if user_id in self._active_speakers:
            return True
        return self._active_until.get(user_id, 0.0) > now

    def _sfu_dominant_id(self) -> str | None:
        """DETERMINISTIC single-speaker pick (the real recapture fix).

        The SFU's active set tells us WHO is plausibly speaking — but when B's
        mic recaptures remote A's voice, B's track has genuine energy too, so the
        SFU often lists BOTH A and B. The previous gate then opened for both and
        transcribed A's words twice. The fix exploits a physical invariant: an
        acoustic re-capture is ALWAYS attenuated relative to the original (echo
        loss, AEC residual, speaker→mic distance), so the ORIGINAL track's RMS is
        always higher than its echo copy's. So among the speakers the SFU
        currently considers active (within hangover), we feed STT for ONLY the
        ONE with the highest own-track smoothed RMS — deterministically the real
        speaker, never the copy. No text similarity, no threshold guessing.

        Returns the winning user_id, or None when there's no live active signal
        (→ caller falls back to the plain per-SID gate, which fails open).

        Genuine simultaneous speech degrades to the louder speaker for that
        window — the same documented, acceptable trade-off as before; the gate
        re-evaluates every frame, so as soon as the quieter person is alone or
        becomes louder, they win and are transcribed."""
        if not self._sfu_gate or not self._active_ever_fired:
            return None
        now = self._now()
        # Signal death → no deterministic pick; caller fails open.
        if now - self._active_last_update > self._stale_s:
            return None
        # Candidate set = SFU-active now OR within their hangover tail (so a
        # mid-phrase micro-pause that briefly drops someone from the active set
        # doesn't hand the turn to their own echo on another track).
        candidates = set(self._active_speakers)
        for uid, deadline in self._active_until.items():
            if deadline > now:
                candidates.add(uid)
        if not candidates:
            return None
        # Pick the loudest candidate by smoothed own-track RMS. A candidate with
        # no RMS yet (just entered, no audio measured) sorts to 0 → never beats a
        # speaking track. Ties (identical RMS, effectively impossible with EMA)
        # resolve by id for determinism.
        winner = max(candidates, key=lambda u: (self._rms_ema.get(u, 0.0), u))
        return winner

    # -- audio helpers -----------------------------------------------------
    @staticmethod
    def _to_mono(frame: AudioRawFrame, channels: int) -> AudioRawFrame:
        """Down-mix a multi-channel PCM16 frame to mono (Azure STT opens its
        stream as mono; stereo bytes read as mono are garbled). Returns a NEW
        frame with num_channels=1; on any failure returns the original frame."""
        try:
            import audioop

            mono = audioop.tomono(frame.audio, 2, 0.5, 0.5) if channels == 2 else frame.audio
            if channels == 2:
                # Build a fresh frame of the SAME concrete type, mono.
                new = type(frame)(
                    audio=mono,
                    sample_rate=getattr(frame, "sample_rate", 16000),
                    num_channels=1,
                )
                # Preserve speaker attribution (UserAudioRawFrame carries user_id).
                uid = getattr(frame, "user_id", None)
                if uid is not None:
                    try:
                        new.user_id = uid  # type: ignore[attr-defined]
                    except Exception:  # noqa: BLE001
                        pass
                return new
        except Exception as e:  # noqa: BLE001
            log.debug("[meeting-stt] mono downmix failed; passing original", err=str(e))
        return frame

    # -- lifecycle ---------------------------------------------------------
    async def setup(self, setup: FrameProcessorSetup) -> None:
        await super().setup(setup)
        # Keep the setup so children created later share the same task manager /
        # clock / observer as the rest of the pipeline.
        self._setup = setup

    async def cleanup(self) -> None:
        for stt in self._stt_by_user.values():
            try:
                await stt.cleanup()
            except Exception as e:  # noqa: BLE001
                log.debug("[meeting-stt] child cleanup failed", err=str(e))
        await super().cleanup()

    # -- helpers -----------------------------------------------------------
    def _agent_track(self, user_id: str) -> bool:
        if self._agent_user_id and user_id == self._agent_user_id:
            return True
        if self._is_agent is not None:
            try:
                return bool(self._is_agent(user_id))
            except Exception:  # noqa: BLE001
                return False
        return False

    async def _get_stt(self, user_id: str) -> STTService | None:
        """Return (creating if needed) the recognizer for this speaker."""
        stt = self._stt_by_user.get(user_id)
        if stt is not None:
            return stt
        if self._setup is None:
            # Audio before setup() — shouldn't happen, but never crash.
            return None
        try:
            stt = self._make_stt()
            # The router owns audio forwarding; the child must NOT also re-push
            # every audio frame (that would duplicate audio downstream). Disable
            # its passthrough — the _Collector whitelist is the second guard.
            try:
                stt._audio_passthrough = False  # noqa: SLF001
            except Exception:  # noqa: BLE001
                pass
            collector = _Collector(
                self._emit_from_child,
                owner_user_id=user_id,
                # Emission-side echo guard — only active when the SFU gate is on.
                was_active=(self.was_active_recently if self._sfu_gate else None),
            )
            stt.link(collector)
            await stt.setup(self._setup)
            await collector.setup(self._setup)
            # Replay StartFrame so the child initialises its stream (sample rate,
            # Azure push stream, recognizer) before the first audio chunk.
            # IMPORTANT: feed via queue_frame(), NOT process_frame() — queue_frame
            # routes through the child's OWN ordered input task (exactly how the
            # pipeline drives the single-STT path). Calling process_frame directly
            # bypasses that queue, so audio chunks reach Azure's push stream with
            # ordering/timing jitter → degraded recognition (the per-speaker
            # accuracy loss). This is the core router fix.
            if self._start_frame is not None:
                await stt.queue_frame(self._start_frame, FrameDirection.DOWNSTREAM)
            self._stt_by_user[user_id] = stt
            self._collector_by_user[user_id] = collector
            log.info(
                "[meeting-stt] recognizer created for speaker",
                user_id=user_id, total=len(self._stt_by_user),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("[meeting-stt] failed to create recognizer", user_id=user_id, err=str(e))
            return None
        return stt

    async def _emit_from_child(self, frame: Frame, direction: FrameDirection) -> None:
        """A child STT produced a frame (transcript/interim/error) — push it out
        of the router into the main pipeline."""
        await self.push_frame(frame, direction)

    # -- frame routing -----------------------------------------------------
    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        # Track lifecycle so late-created children can be initialised.
        if isinstance(frame, StartFrame):
            self._start_frame = frame
            self._started = True
            # Forward to any children that already exist, then pass through.
            # queue_frame (not process_frame) → child's ordered input task.
            for stt in self._stt_by_user.values():
                await stt.queue_frame(frame, direction)
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, (EndFrame, CancelFrame)):
            for stt in self._stt_by_user.values():
                await stt.queue_frame(frame, direction)
            await self.push_frame(frame, direction)
            return

        # AUDIO — route to the speaker's own recognizer ONLY.
        if isinstance(frame, AudioRawFrame):
            user_id = getattr(frame, "user_id", "") or ""
            if self._agent_track(user_id):
                # The agent's own audio: never transcribe it. Pass through so any
                # downstream consumer still sees it, but don't feed any STT.
                await self.push_frame(frame, direction)
                return
            # Watchdog (fix B): first human audio time, and auto-disable the SFU
            # gate if the active-speaker event never fires within the window — a
            # silent permission/registration failure self-heals to legacy.
            if self._sfu_gate and self._first_audio_at == 0.0:
                self._first_audio_at = self._now()
            if (self._sfu_gate and not self._watchdog_tripped
                    and not self._active_ever_fired and self._watchdog_s > 0
                    and self._first_audio_at
                    and self._now() - self._first_audio_at > self._watchdog_s):
                self._watchdog_tripped = True
                self._sfu_gate = False
                log.warning(
                    "[meeting-stt] SFU active-speaker event NEVER fired within "
                    "watchdog — auto-disabling gate, reverting to legacy path",
                    watchdog_s=self._watchdog_s,
                )
            # GROUND TRUTH (once): log the real sample_rate + channels the LiveKit
            # transport hands us. The voice (phone) channel is always 16k mono and
            # Azure STT opens its stream as mono; if LiveKit delivers STEREO (a
            # browser mic often does) Azure reads the interleaved L/R as mono and
            # the waveform is GARBLED — the exact failure seen in the logs. So we
            # also DOWN-MIX to mono before STT.
            ch = getattr(frame, "num_channels", 1) or 1
            sr = getattr(frame, "sample_rate", 0) or 0
            if not self._logged_fmt:
                self._logged_fmt = True
                log.info(
                    "[meeting-stt] inbound audio format",
                    sample_rate=sr, channels=ch, expected_sr=16000,
                )
            if ch and ch > 1:
                frame = self._to_mono(frame, ch)
            # DIAGNOSTIC: every ~100 frames, log how audio is distributed across
            # user_ids + how many recognizers exist. If ONE speaker shows up under
            # 2+ user_ids (SID vs identity, or a re-subscribed track), their voice
            # is being SPLIT across recognizers — each hears half → garbled. This
            # is the decisive log for the "two different transcripts of one
            # sentence" symptom.
            self._audio_count_by_user[user_id] = self._audio_count_by_user.get(user_id, 0) + 1
            if self._audio_count_by_user[user_id] % 100 == 1:
                log.debug(
                    "[meeting-stt] audio distribution",
                    frames_by_user=dict(self._audio_count_by_user),
                    recognizers=sorted(self._stt_by_user.keys()),
                )
            stt = await self._get_stt(user_id)
            # ALWAYS update this track's smoothed RMS first, so the deterministic
            # SFU dominant-speaker pick below has a CURRENT level for every track
            # (not just whichever happens to be fed). Cheap; runs per frame.
            self._update_rms(user_id, getattr(frame, "audio", b""))

            # ACTIVE-SPEAKER GATE (optional RMS-only experiment) — kept behind the
            # legacy flag. Single speaker / gate off → no-op. The deterministic SFU
            # gate below supersedes this in production.
            feed = True
            if self._active_gate and len(self._stt_by_user) > 1:
                feed = self._is_dominant_speaker(user_id, getattr(frame, "audio", b""))
                if not feed:
                    self._gate_dropped[user_id] = self._gate_dropped.get(user_id, 0) + 1
                    if self._gate_dropped[user_id] % 100 == 1:
                        log.debug(
                            "[meeting-stt] active-speaker gate dropping bleed",
                            user_id=user_id, dropped=self._gate_dropped[user_id],
                            rms_by_user={k: round(v) for k, v in self._rms_ema.items()},
                        )

            # SFU DETERMINISTIC SINGLE-SPEAKER GATE (the production recapture fix).
            # Of all speakers the SFU currently reports active (+ hangover), feed
            # STT for ONLY the loudest own-track — deterministically the real
            # speaker, never the attenuated echo copy on someone else's mic. The
            # losers' frames are held in a small per-SID lookback ring (flushed on
            # their next real onset so the leading syllable isn't lost) and
            # otherwise dropped before STT. Fails open (dominant=None) when the SFU
            # signal is absent/stale → degrades to the per-SID gate.
            if feed and self._sfu_gate:
                dominant = self._sfu_dominant_id()
                buf = self._lookback.setdefault(user_id, deque())
                # Open the gate when: no deterministic pick available (fail-open to
                # the per-SID gate), OR this track IS the dominant speaker.
                open_gate = (
                    (dominant is None and self._passes_active_gate(user_id))
                    or (dominant is not None and user_id == dominant)
                )
                if open_gate:
                    # Gate open: flush any buffered onset frames first, then feed.
                    if buf and stt is not None:
                        while buf:
                            await stt.queue_frame(buf.popleft(), direction)
                    feed = True
                else:
                    # Gate closed (not the dominant speaker, or per-SID gate shut):
                    # stash as potential onset lookback (bounded), do NOT feed STT.
                    buf.append(frame)
                    max_frames = max(1, self._lookback_ms // 20)
                    while len(buf) > max_frames:
                        buf.popleft()
                    feed = False
                    self._gate_dropped[user_id] = self._gate_dropped.get(user_id, 0) + 1
                    if self._gate_dropped[user_id] % 100 == 1:
                        log.debug(
                            "[meeting-stt] SFU gate: not dominant — dropping (likely echo)",
                            user_id=user_id, dominant=dominant,
                            rms_by_user={k: round(v) for k, v in self._rms_ema.items()},
                            dropped=self._gate_dropped[user_id],
                        )

            if stt is not None and feed:
                # queue_frame → child's ordered input task (NOT process_frame).
                # DIAGNOSTIC: track how many frames EACH child actually receives,
                # keyed by the recognizer it's routed to. If a "silent" speaker's
                # recognizer is receiving frames while someone else speaks, the
                # bleed is in their MIC (acoustic), not the router. Log every ~100.
                self._fed_by_user[user_id] = self._fed_by_user.get(user_id, 0) + 1
                if self._fed_by_user[user_id] % 100 == 1:
                    log.debug(
                        "[meeting-stt] frames FED to recognizers",
                        fed_by_user=dict(self._fed_by_user),
                    )
                await stt.queue_frame(frame, direction)
            # Pass the audio through too (the bridge/VAD chain expects it) —
            # regardless of the STT gate, so VAD/barge-in still see the room.
            await self.push_frame(frame, direction)
            return

        # Everything else (VAD UserStarted/Stopped, interruptions, system frames)
        # — broadcast to every child so each recognizer segments correctly, then
        # pass through unchanged.
        for stt in self._stt_by_user.values():
            await stt.queue_frame(frame, direction)
        await self.push_frame(frame, direction)


__all__ = ["MeetingSTTRouter", "STTFactory"]
