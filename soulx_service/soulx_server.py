"""SoulX-FlashHead WebSocket inference server — persistent session model.

Run from ~/SoulX-FlashHead/:
    XFORMERS_IGNORE_FLASH_VERSION_CHECK=1 PYTHONPATH=. \
    uvicorn soulx_server:app --host 0.0.0.0 --port 8011

Protocol (one WebSocket connection per browser peer):

  HANDSHAKE (once):
    Client → {"image_path": ..., "session_id": ...}
    Server → {"status": "ready"}
    Server enters IDLE mode immediately

  IDLE MODE:
    Server → binary JPEG frames from idle_frames.pkl, looped at 25fps
    Server → {"type": "idle"}   (one-time confirmation after handshake / after returning from speak)

  SPEAK TRANSITION:
    Client → {"type": "speak_start"}
    Server stops idle loop, enters SPEAKING mode

  SPEAKING MODE:
    Client → binary float32 PCM chunks (16kHz)
    Server → binary JPEG frames (speaking animation, concurrent)

  EOF (end of utterance):
    Client → {"type": "eof"}
    Server → [remaining speaking JPEG frames]
    Server → {"type": "idle"}   ← utterance complete signal
    Server re-enters IDLE mode

  INTERRUPT (barge-in):
    Client → {"type": "interrupt"}
    Server discards audio buffer immediately
    Server → {"type": "idle"}
    Server re-enters IDLE mode

  CLOSE (session end):
    Client → {"type": "close"}
    Server closes WebSocket
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import pickle
import pathlib
import sys
import tempfile
import time
import traceback
from collections import deque
from contextlib import asynccontextmanager
from typing import AsyncIterator

import cv2
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from PIL import Image

# Worker identity within a multi-process pool (one process per concurrent stream,
# all sharing one GPU, fronted by nginx — see entrypoint.sh). The PRIMARY worker
# ("0") is the only one allowed to generate the shared idle/thinking .pkl caches;
# every other worker waits for those files to appear (set by the entrypoint).
_WORKER_ID = os.environ.get("SOULX_WORKER_ID", "0")
_IS_PRIMARY = _WORKER_ID == "0"

log = logging.getLogger("soulx")
logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s [%(levelname)s] soulx[w{_WORKER_ID}]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_SOULX_ROOT = os.path.expanduser("~/SoulX-FlashHead")
os.chdir(_SOULX_ROOT)
if _SOULX_ROOT not in sys.path:
    sys.path.insert(0, _SOULX_ROOT)

os.environ.setdefault("XFORMERS_IGNORE_FLASH_VERSION_CHECK", "1")

from flash_head.inference import (  # type: ignore
    get_pipeline,
    get_base_data,
    get_audio_embedding,
    run_pipeline,
    get_infer_params,
)
import mediapipe as mp  # type: ignore
from mediapipe.tasks import python as mp_python  # type: ignore
from mediapipe.tasks.python import vision as mp_vision  # type: ignore

# ── Configuration ──────────────────────────────────────────────────────────────
_CKPT_DIR       = os.environ.get("SOULX_CKPT_DIR",    "/data/soulx-models/SoulX-FlashHead-1_3B")
_WAV2VEC_DIR    = os.environ.get("SOULX_WAV2VEC_DIR", "/data/soulx-models/wav2vec2-base-960h")
_MODEL_TYPE      = os.environ.get("SOULX_MODEL_TYPE",       "lite")
# Model used ONLY for generating cached idle/thinking frames at startup.
# Pro model produces natural head movement (nods, blinks) with near-silence audio
# while Lite model does not. Defaults to "pro" so idle animation looks alive.
_IDLE_MODEL_TYPE = os.environ.get("SOULX_IDLE_MODEL_TYPE",  "pro")
_REFERENCE_IMAGE = os.environ.get(
    "SOULX_REFERENCE_IMAGE", "/home/aegisuser/ditto_service/face.jpg"
)
# Paste-back: feed SoulX a tight face crop (good lip-sync) and composite the
# animated head back onto the original full photo so the whole frame stays shown.
_PASTE_BACK   = os.environ.get("SOULX_PASTE_BACK", "1") != "0"
_FACE_RATIO   = float(os.environ.get("SOULX_FACE_RATIO", "2.0"))   # SoulX default
_OUT_MAX_SIDE = int(os.environ.get("SOULX_OUTPUT_MAX_SIDE", "0"))  # 0 = native
_FACE_MODEL   = os.environ.get(
    "SOULX_FACE_MODEL", "/home/aegisuser/ditto_service/blaze_face_short_range.tflite")
# Head/hair segmentation → crop auto-includes the full head (any hairstyle) so the
# feathered seam falls on static chest/background, not through moving hair.
_SEG_MODEL    = os.environ.get(
    "SOULX_SEG_MODEL", "/home/aegisuser/ditto_service/selfie_segmenter.tflite")
# Low threshold so soft/dark hair edges (low foreground confidence) count as head;
# over-including a little background above the head is harmless (static → blends).
_SEG_THRESHOLD    = float(os.environ.get("SOULX_SEG_THRESHOLD", "0.2"))
_HEAD_TOP_MARGIN  = float(os.environ.get("SOULX_HEAD_TOP_MARGIN", "0.22"))  # frac of head height
_HEAD_CROP_FACTOR = float(os.environ.get("SOULX_HEAD_CROP_FACTOR", "1.6"))  # crop side = head_h * this
# Feather inset (frac of crop dim). Narrower → the fully-animated interior reaches
# closer to the crop edge, so the head clears the blend band (less hair ghosting).
_FEATHER          = float(os.environ.get("SOULX_FEATHER", "0.06"))

_pipeline = None
_PARAMS: dict = {}
# Serialises all pipeline calls — run_pipeline mutates internal motion latent
# state and is not re-entrant.
_pipeline_lock = asyncio.Lock()

# True while this worker is serving a WebSocket session. Each worker handles ONE
# stream at a time (the pool gives concurrency via N worker processes, not by
# multiplexing one); the nginx front uses max_conns=1 to enforce that. Exposed on
# /health so the pool occupancy is observable.
_busy = False

# Cached idle and thinking JPEG frames, loaded once at startup
_idle_frames:     list[bytes] = []
_thinking_frames: list[bytes] = []

# ── Paste-back state ────────────────────────────────────────────────────────────
# Set per reference image (startup + per session). Serialised by _pipeline_lock.
_pb_orig: "np.ndarray | None" = None              # (H,W,3) RGB uint8 original
_pb_box:  "tuple[int,int,int,int] | None" = None  # clamped x1,y1,x2,y2
_pb_mask: "np.ndarray | None" = None              # (bh,bw,1) float32 in [0,1]
_detector = None   # mediapipe Tasks FaceDetector (lazy, built once)
_segmenter = None  # mediapipe Tasks ImageSegmenter (lazy, built once)


def _feather_mask(bw: int, bh: int, feather: float = _FEATHER) -> np.ndarray:
    """Soft-edged alpha mask so the composited head blends into the still."""
    m = np.zeros((bh, bw), np.float32)
    px, py = max(1, int(bw * feather)), max(1, int(bh * feather))
    m[py:bh - py, px:bw - px] = 1.0
    m = cv2.GaussianBlur(m, (px * 2 + 1, py * 2 + 1), 0)
    return m[..., None]


def _detect_face_bbox(rgb: np.ndarray) -> "tuple[int,int,int,int] | None":
    """Largest face as absolute (x1,y1,x2,y2) via mediapipe Tasks BlazeFace, else None."""
    global _detector
    if _detector is None:
        opts = mp_vision.FaceDetectorOptions(
            base_options=mp_python.BaseOptions(model_asset_path=_FACE_MODEL),
            running_mode=mp_vision.RunningMode.IMAGE)
        _detector = mp_vision.FaceDetector.create_from_options(opts)
    mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
    res = _detector.detect(mp_img)
    if not res.detections:
        return None
    bb = max(res.detections,
             key=lambda d: d.bounding_box.width * d.bounding_box.height).bounding_box
    return (int(bb.origin_x), int(bb.origin_y),
            int(bb.origin_x + bb.width), int(bb.origin_y + bb.height))


def _person_mask(rgb: np.ndarray) -> np.ndarray:
    """Boolean foreground (person) mask via mediapipe Tasks ImageSegmenter (selfie)."""
    global _segmenter
    if _segmenter is None:
        opts = mp_vision.ImageSegmenterOptions(
            base_options=mp_python.BaseOptions(model_asset_path=_SEG_MODEL),
            running_mode=mp_vision.RunningMode.IMAGE,
            output_confidence_masks=True)
        _segmenter = mp_vision.ImageSegmenter.create_from_options(opts)
    mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
    conf = np.squeeze(_segmenter.segment(mp_img).confidence_masks[0].numpy_view())  # (H,W)
    return conf >= _SEG_THRESHOLD


def _head_crop_box(rgb: np.ndarray, face_box) -> "tuple[int,int,int,int]":
    """Square crop containing the full head — hair top located via segmentation —
    extending down through the chin to mid-chest. All offsets are fractions of the
    measured head height, so framing adapts to any face size / hair volume / hat."""
    fx1, fy1, fx2, fy2 = face_box
    cx, cy, fw, chin = (fx1 + fx2) // 2, (fy1 + fy2) // 2, fx2 - fx1, fy2
    h, w = rgb.shape[:2]
    mask = _person_mask(rgb)
    bl, br = max(0, int(cx - 1.2 * fw)), min(w, int(cx + 1.2 * fw))
    rows = np.where(mask[:max(1, cy), bl:br].any(axis=1))[0]   # foreground above face center
    head_top = int(rows.min()) if len(rows) else fy1           # fallback: face-box top
    head_h = max(1, chin - head_top)
    half = (head_h * _HEAD_CROP_FACTOR) / 2.0
    cols = np.where(mask[head_top:max(head_top + 1, fy1), bl:br].any(axis=0))[0]  # hair width
    if len(cols):
        half = max(half, cx - (bl + int(cols.min())), (bl + int(cols.max())) - cx)
    top = head_top - _HEAD_TOP_MARGIN * head_h
    x1, x2 = int(max(0, cx - half)), int(min(w, cx + half))
    y1, y2 = int(max(0, top)), int(min(h, top + 2 * half))
    return x1, y1, x2, y2


def _prepare_paste_back(image_path: str) -> str:
    """Detect the face, set _pb_orig/_pb_box/_pb_mask, write a 512 crop to a temp
    file and return that path (to feed get_base_data). On disabled / no-face / ANY
    detector error, clears state and returns image_path → graceful full-frame
    fallback (never crashes startup).

    Crop box is sized via head/hair segmentation (_head_crop_box) so the full head is
    inside the animated region and the feathered seam lands on the static chest/
    background. If segmentation fails, falls back to the face-box ratio/bias method
    (mirrors flash_head.utils.facecrop.get_scaled_bbox); if no face / any error,
    returns image_path → full-frame (never crashes startup).
    """
    global _pb_orig, _pb_box, _pb_mask
    _pb_orig = _pb_box = _pb_mask = None
    if not _PASTE_BACK:
        return image_path
    try:
        img = Image.open(image_path).convert("RGB")
        rgb = np.array(img)
        h, w = rgb.shape[:2]
        bbox = _detect_face_bbox(rgb)
        if bbox is None:
            log.warning("paste-back: no face in %s — full-frame fallback", image_path)
            return image_path
        try:
            x1, y1, x2, y2 = _head_crop_box(rgb, bbox)
            mode = "seg"
        except Exception as seg_exc:  # segmentation unavailable → face-box fallback
            log.warning("paste-back: segmentation failed (%s) — face-box crop", seg_exc)
            bx1, by1, bx2, by2 = bbox
            cx, cy = (bx1 + bx2) / 2, (by1 + by2) / 2
            side = (bx2 - bx1) * _FACE_RATIO
            x1 = int(max(0, cx - 0.50 * side)); x2 = int(min(w, cx + 0.50 * side))
            y1 = int(max(0, cy - 0.55 * side)); y2 = int(min(h, cy + 0.45 * side))
            mode = "facebox"
        _pb_orig, _pb_box = rgb, (x1, y1, x2, y2)
        _pb_mask = _feather_mask(x2 - x1, y2 - y1)
        # Per-worker-unique temp path: the K pool workers are separate processes
        # sharing the container's /tmp, so a fixed filename here gets raced —
        # one worker overwrites the crop while another's get_base_data is mid-read,
        # yielding "image file is truncated" and a dropped session. Each worker
        # serves one session at a time, so worker-id + pid is collision-free and
        # self-cleaning (the same worker just overwrites its own file next session).
        tmp = os.path.join(tempfile.gettempdir(), f"soulx_crop_w{_WORKER_ID}_{os.getpid()}.jpg")
        img.crop((x1, y1, x2, y2)).resize((512, 512)).save(tmp, "JPEG", quality=95)
        log.info("paste-back: box=%s img=%dx%d mode=%s", _pb_box, w, h, mode)
        return tmp
    except Exception as exc:  # never let detection take down startup
        log.warning("paste-back: detection failed (%s) — full-frame fallback", exc)
        _pb_orig = _pb_box = _pb_mask = None
        return image_path


def _composite(frame_rgb: np.ndarray) -> np.ndarray:
    """512×512 RGB animated head → full-frame composite onto the original still.

    Resizing the square output back to the (possibly non-square) clamped box inverts
    SoulX's crop→512 stretch exactly, so the head lands at the right aspect.
    """
    x1, y1, x2, y2 = _pb_box  # type: ignore[misc]
    resized = cv2.resize(frame_rgb, (x2 - x1, y2 - y1), interpolation=cv2.INTER_LINEAR)
    out = _pb_orig.copy()  # type: ignore[union-attr]
    region = out[y1:y2, x1:x2].astype(np.float32)
    out[y1:y2, x1:x2] = (_pb_mask * resized + (1 - _pb_mask) * region).astype(np.uint8)
    if _OUT_MAX_SIDE and max(out.shape[:2]) > _OUT_MAX_SIDE:
        h, w = out.shape[:2]
        s = _OUT_MAX_SIDE / max(h, w)
        out = cv2.resize(out, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    return out


def _load_idle_frames() -> list[bytes]:
    idle_path = pathlib.Path(_REFERENCE_IMAGE).parent / "idle_frames.pkl"
    if not idle_path.exists():
        log.warning("idle_frames.pkl not found at %s — idle animation disabled", idle_path)
        return []
    with open(idle_path, "rb") as f:
        frames: list[bytes] = pickle.load(f)
    log.info("idle frames loaded: %d frames", len(frames))
    return frames


def _load_thinking_frames() -> list[bytes]:
    thinking_path = pathlib.Path(_REFERENCE_IMAGE).parent / "thinking_frames.pkl"
    if not thinking_path.exists():
        log.warning("thinking_frames.pkl not found — falling back to idle frames for thinking state")
        return []
    with open(thinking_path, "rb") as f:
        frames: list[bytes] = pickle.load(f)
    log.info("thinking frames loaded: %d frames", len(frames))
    return frames


# Per-avatar idle/thinking frame cache, keyed by absolute image path, so a worker
# serving different avatars across sequential sessions doesn't re-read the .pkl each
# time. The default reference avatar is served from the startup module globals.
_avatar_frames: "dict[str, tuple[list[bytes], list[bytes]]]" = {}


def _frame_paths_for(image_path: str) -> "tuple[pathlib.Path, pathlib.Path]":
    """idle/thinking .pkl paths for an avatar. The default reference image keeps the
    legacy unprefixed names (so existing seeding + startup generation still apply);
    any other avatar is keyed by its filename stem alongside its photo."""
    p = pathlib.Path(image_path)
    if os.path.abspath(image_path) == os.path.abspath(_REFERENCE_IMAGE):
        return p.parent / "idle_frames.pkl", p.parent / "thinking_frames.pkl"
    return p.parent / f"{p.stem}.idle_frames.pkl", p.parent / f"{p.stem}.thinking_frames.pkl"


def _load_frames_for(image_path: str) -> "tuple[list[bytes], list[bytes]]":
    """Load (and cache) an avatar's idle/thinking frames for a session.

    Different streams may use different photos; each needs its own idle/thinking
    animation. The default reference avatar reuses the startup-loaded module globals.
    For any other avatar we read its pre-generated .pkl caches. We do NOT generate on
    the request path (that takes minutes) — a missing cache falls back to the default
    avatar's frames with a warning, so the stream still works (idle face won't match,
    but it never blocks). Pre-seed per-avatar caches alongside the photo."""
    key = os.path.abspath(image_path)
    if key == os.path.abspath(_REFERENCE_IMAGE):
        return _idle_frames, _thinking_frames
    if key in _avatar_frames:
        return _avatar_frames[key]
    idle_path, thinking_path = _frame_paths_for(image_path)
    idle: list[bytes] = []
    thinking: list[bytes] = []
    try:
        if idle_path.exists():
            with open(idle_path, "rb") as f:
                idle = pickle.load(f)
        if thinking_path.exists():
            with open(thinking_path, "rb") as f:
                thinking = pickle.load(f)
    except Exception as exc:  # noqa: BLE001 — never let a bad cache kill the session
        log.warning("avatar frames load failed for %s (%s) — using default", image_path, exc)
        idle = thinking = []
    if not idle:
        log.warning("no idle cache for avatar %s — falling back to default avatar's idle frames "
                    "(pre-seed <stem>.idle_frames.pkl beside the photo to fix)", image_path)
        idle, thinking = _idle_frames, _thinking_frames
    else:
        log.info("avatar frames loaded for %s: idle=%d thinking=%d", image_path, len(idle), len(thinking))
    _avatar_frames[key] = (idle, thinking)
    return idle, thinking


def _generate_idle_frames_if_needed() -> None:
    """Auto-generate idle clips on first startup; skip if already cached."""
    out_dir       = pathlib.Path(_REFERENCE_IMAGE).parent
    idle_path     = out_dir / "idle_frames.pkl"
    thinking_path = out_dir / "thinking_frames.pkl"

    if idle_path.exists() and thinking_path.exists():
        log.info("idle frames already cached — skipping generation")
        return

    if not os.path.isfile(_REFERENCE_IMAGE):
        log.warning("no reference image at %s — skipping idle frame generation", _REFERENCE_IMAGE)
        return

    sr  = _PARAMS["sample_rate"]
    fps = _PARAMS["tgt_fps"]
    fn  = _PARAMS["frame_num"]
    mn  = _PARAMS["motion_frames_num"]
    vf  = fn - mn
    secs          = 6
    total_frames  = secs * fps
    total_samples = secs * sr + sr

    def _converge(converge_seed: int = 0, num_passes: int = 10) -> None:
        dur   = (num_passes * vf + fn) / fps + 1.0
        audio = np.random.default_rng(converge_seed).normal(0, 0.001, int(dur * sr)).astype(np.float32)
        for ci in range(num_passes):
            end = (ci + 1) * vf + mn
            st  = end - fn
            run_pipeline(_pipeline, get_audio_embedding(_pipeline, audio, st, end))

    def _gen_clip(seed: int = 42, amplitude: float = 0.001) -> list[bytes]:
        audio = np.random.default_rng(seed).normal(0, amplitude, total_samples).astype(np.float32)
        frames: list[bytes] = []
        ci = 0
        while True:
            end = (ci + 1) * vf + mn
            st  = max(0, end - fn)
            if end > total_frames:
                break
            result = run_pipeline(_pipeline, get_audio_embedding(_pipeline, audio, st, end))
            for i in range(mn, fn):
                frame_np = result[i].cpu().numpy().astype("uint8")
                if _pb_box is not None:
                    frame_np = _composite(frame_np)
                img = Image.fromarray(frame_np, mode="RGB")
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                frames.append(buf.getvalue())
            ci += 1
        return frames

    # Detect face + build paste-back state once; feed the tight crop to SoulX so
    # cached idle/thinking frames use the same framing as live speaking frames.
    ref_path = _prepare_paste_back(_REFERENCE_IMAGE)

    t0 = time.monotonic()
    log.info("generating idle frames (first startup only — ~60s)...")
    get_base_data(_pipeline, ref_path, 42, False)
    _converge()
    idle_frames = _gen_clip()
    # Atomic write (temp + os.replace) so a peer worker polling for this file never
    # reads a half-written pickle in the multi-process pool.
    _tmp = str(idle_path) + ".tmp"
    with open(_tmp, "wb") as f:
        pickle.dump(idle_frames, f)
    os.replace(_tmp, idle_path)
    log.info("idle frames saved: %d frames (%.1fs)", len(idle_frames), time.monotonic() - t0)

    t1 = time.monotonic()
    # Generate thinking frames as a MIXTURE of 3 different convergence states.
    # Seeds 0/500/1000 are more diverse; increasing num_passes (10→30→50) pushes
    # the model further into different pose regions, increasing motion variety.
    #   ~40% clip_a  +  ~40% clip_b  +  ~20% clip_c  =  nod + tilt + look-elsewhere
    get_base_data(_pipeline, ref_path, 42, False)
    _converge(converge_seed=0,    num_passes=10)   # pattern A — 10 passes
    clip_a = _gen_clip()
    get_base_data(_pipeline, ref_path, 42, False)
    _converge(converge_seed=500,  num_passes=30)   # pattern B — 30 passes, seed 500
    clip_b = _gen_clip()
    get_base_data(_pipeline, ref_path, 42, False)
    _converge(converge_seed=1000, num_passes=50)   # pattern C — 50 passes, seed 1000
    clip_c = _gen_clip()
    thinking_frames = clip_a + clip_b + clip_c[: len(clip_a) // 3]
    _tmp_t = str(thinking_path) + ".tmp"
    with open(_tmp_t, "wb") as f:
        pickle.dump(thinking_frames, f)
    os.replace(_tmp_t, thinking_path)
    log.info("thinking frames saved: %d frames (%.1fs)", len(thinking_frames), time.monotonic() - t1)
    log.info("idle frame generation complete — total: %.1fs", time.monotonic() - t0)


def _await_frames(idle_path: pathlib.Path, thinking_path: pathlib.Path,
                  timeout_s: float = 900.0) -> None:
    """Block until BOTH idle/thinking caches exist (non-primary workers).

    In the multi-process pool the PRIMARY worker generates the shared .pkl caches
    once; every other worker reuses them. Rather than each worker regenerating
    (K simultaneous ~8-min jobs thrashing the GPU), the non-primary workers simply
    wait here for the primary to finish, then load. Writes are atomic (os.replace),
    so seeing the file means it is complete."""
    start = time.monotonic()
    logged = False
    while not (idle_path.exists() and thinking_path.exists()):
        if time.monotonic() - start > timeout_s:
            log.warning("timed out (%.0fs) waiting for idle/thinking caches — "
                        "proceeding (idle animation may be empty)", timeout_s)
            return
        if not logged:
            logged = True
            log.info("waiting for primary worker to generate idle/thinking caches...")
        time.sleep(2.0)
    log.info("idle/thinking caches present after %.0fs", time.monotonic() - start)


def _warm_up() -> None:
    if not (_REFERENCE_IMAGE and os.path.exists(_REFERENCE_IMAGE)):
        log.warning("no reference image for warm-up, skipping")
        return
    log.info("warming up: face preprocess + JIT compilation (~120s)...")
    t0 = time.monotonic()
    get_base_data(_pipeline, _REFERENCE_IMAGE, 42, False)
    log.info("face preprocess done in %.1fs", time.monotonic() - t0)

    sr:           int = _PARAMS["sample_rate"]
    fps:          int = _PARAMS["tgt_fps"]
    frame_num:    int = _PARAMS["frame_num"]
    motion_frames:int = _PARAMS["motion_frames_num"]
    cached_secs   = 8
    cached_samples = sr * cached_secs
    chunk_samples  = (frame_num - motion_frames) * sr // fps
    audio_end_idx  = cached_secs * fps
    audio_start_idx = audio_end_idx - frame_num

    t1 = time.monotonic()
    _noise_audio = np.random.default_rng(0).normal(0, 0.1, cached_samples).astype(np.float32)
    _noise_dq: deque = deque(_noise_audio.tolist(), maxlen=cached_samples)
    _noise_dq.extend(np.random.default_rng(1).normal(0, 0.1, chunk_samples).astype(np.float32).tolist())
    emb = get_audio_embedding(_pipeline, np.array(_noise_dq, dtype=np.float32), audio_start_idx, audio_end_idx)
    run_pipeline(_pipeline, emb)
    log.info("first JIT pass (noise): %.1fs", time.monotonic() - t1)

    t2 = time.monotonic()
    _silence_dq: deque = deque([0.0] * cached_samples, maxlen=cached_samples)
    _silence_dq.extend([0.0] * chunk_samples)
    emb = get_audio_embedding(_pipeline, np.array(_silence_dq, dtype=np.float32), audio_start_idx, audio_end_idx)
    run_pipeline(_pipeline, emb)
    log.info(
        "second JIT pass (silence reset): %.1fs — total warm-up: %.1fs",
        time.monotonic() - t2,
        time.monotonic() - t0,
    )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global _pipeline, _PARAMS, _idle_frames, _thinking_frames
    import gc, torch as _torch

    loop = asyncio.get_running_loop()

    idle_path     = pathlib.Path(_REFERENCE_IMAGE).parent / "idle_frames.pkl"
    thinking_path = pathlib.Path(_REFERENCE_IMAGE).parent / "thinking_frames.pkl"
    frames_present = idle_path.exists() and thinking_path.exists()
    # Only the PRIMARY worker generates the shared caches; peers wait (see
    # _await_frames). When caches are pre-seeded in the mount, nobody generates.
    generate_here = (not frames_present) and _IS_PRIMARY

    # ── Phase 1: Generate idle/thinking frames (Pro model gives natural motion) ──
    if generate_here:
        gen_type = _IDLE_MODEL_TYPE
        log.info("loading frame-generation model type=%s from %s", gen_type, _CKPT_DIR)
        _pipeline = get_pipeline(1, _CKPT_DIR, gen_type, _WAV2VEC_DIR)
        _PARAMS   = get_infer_params()
        log.info(
            "frame-gen model ready  frame_num=%d  motion_frames=%d  fps=%d  sample_rate=%d",
            _PARAMS["frame_num"], _PARAMS["motion_frames_num"],
            _PARAMS["tgt_fps"], _PARAMS["sample_rate"],
        )
        await loop.run_in_executor(None, _warm_up)
        await loop.run_in_executor(None, _generate_idle_frames_if_needed)
        if gen_type != _MODEL_TYPE:
            log.info("unloading frame-generation model, freeing GPU memory...")
            del _pipeline
            _pipeline = None
            gc.collect()
            _torch.cuda.empty_cache()

    # ── Phase 2: Load the serving model (Lite by default) ──
    log.info("loading serving model type=%s from %s", _MODEL_TYPE, _CKPT_DIR)
    _pipeline = get_pipeline(1, _CKPT_DIR, _MODEL_TYPE, _WAV2VEC_DIR)
    _PARAMS   = get_infer_params()
    log.info(
        "model ready  frame_num=%d  motion_frames=%d  fps=%d  sample_rate=%d",
        _PARAMS["frame_num"], _PARAMS["motion_frames_num"],
        _PARAMS["tgt_fps"], _PARAMS["sample_rate"],
    )
    await loop.run_in_executor(None, _warm_up)
    if _IS_PRIMARY:
        # No-op when caches already exist; the safety net that generates with the
        # serving (Lite) model if Phase 1 was skipped but caches are still missing.
        await loop.run_in_executor(None, _generate_idle_frames_if_needed)
    elif not frames_present:
        # Non-primary worker on a cold cache: wait for the primary to finish.
        await loop.run_in_executor(None, _await_frames, idle_path, thinking_path)
    _idle_frames     = _load_idle_frames()
    _thinking_frames = _load_thinking_frames()
    log.info("ready — all requests will use cached JIT paths")
    yield
    log.info("shutdown")


app = FastAPI(title="SoulX Inference Service", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {
        "ok":               _pipeline is not None,
        "model_loaded":     _pipeline is not None,
        "idle_frames_ready": len(_idle_frames) > 0,
        "worker_id":        _WORKER_ID,
        "busy":             _busy,
    }


def _infer(arr: np.ndarray, start: int, end: int) -> object:
    emb = get_audio_embedding(_pipeline, arr, start, end)
    return run_pipeline(_pipeline, emb)


def _encode_frame(frame_tensor) -> bytes:
    frame_np = frame_tensor.cpu().numpy().astype(np.uint8)
    if _pb_box is not None:
        frame_np = _composite(frame_np)
    img = Image.fromarray(frame_np, mode="RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    global _busy
    await ws.accept()
    session_id = "unknown"
    _busy = True

    try:
        # ── Step 1: Handshake ──────────────────────────────────────────────────
        try:
            raw  = await ws.receive_text()
            init = json.loads(raw)
        except json.JSONDecodeError as exc:
            await ws.send_text(json.dumps({"error": f"invalid init: {exc}"}))
            return
        # Default to the server's SOULX_REFERENCE_IMAGE; a non-empty client image_path
        # overrides it so different streams can animate different avatars.
        image_path = (init.get("image_path") or "").strip() or _REFERENCE_IMAGE
        session_id = init.get("session_id", "unknown")

        log.info("session=%s image=%s", session_id, image_path)

        if not os.path.isfile(image_path):
            await ws.send_text(json.dumps({"error": f"image not found: {image_path}"}))
            return

        loop = asyncio.get_running_loop()

        # Idle/thinking frames for THIS session's avatar (the default avatar reuses the
        # startup module globals; any other avatar loads its own pre-seeded caches,
        # falling back to the default if absent). Used by the senders below.
        sess_idle, sess_thinking = await loop.run_in_executor(
            None, _load_frames_for, image_path
        )

        # ── Step 2: Acquire pipeline, preprocess face (once per session) ───────
        async with _pipeline_lock:
            t0 = time.monotonic()
            # Tight face crop for SoulX (good lip-sync); paste-back state captured
            # for compositing the animated head onto the full original photo.
            crop_path = await loop.run_in_executor(None, _prepare_paste_back, image_path)
            await loop.run_in_executor(None, get_base_data, _pipeline, crop_path, 42, False)
            log.info("session=%s preprocess: %.2fs", session_id, time.monotonic() - t0)

            # Inference parameters
            sample_rate:   int = _PARAMS["sample_rate"]
            tgt_fps:       int = _PARAMS["tgt_fps"]
            frame_num:     int = _PARAMS["frame_num"]
            motion_frames: int = _PARAMS["motion_frames_num"]
            cached_secs    = 8
            cached_samples = sample_rate * cached_secs
            audio_end_idx  = cached_secs * tgt_fps
            audio_start_idx = audio_end_idx - frame_num
            valid_frames   = frame_num - motion_frames
            chunk_samples  = valid_frames * sample_rate // tgt_fps

            # ── Per-session speak warm-up ──────────────────────────────────────
            # The FIRST live speaking inference of a session pays a spin-up (~1.4s
            # observed as client-side b1) — JIT/motion-latent init for this session's
            # freshly-preprocessed face. Idle/thinking frames are served from cached
            # .pkls (no live inference), so nothing primes the speaking path until the
            # first real utterance. Run ONE throwaway inference on silence HERE, inside
            # the handshake lock and BEFORE "ready", so the browser only connects once
            # the speaking path is warm → the first utterance starts immediately.
            # Never fail the handshake on warm-up error (lazy first-turn cost remains).
            try:
                _tw = time.monotonic()
                _sil = np.zeros(cached_samples + chunk_samples, dtype=np.float32)
                await loop.run_in_executor(
                    None, _infer, _sil, audio_start_idx, audio_end_idx
                )
                log.info(
                    "[soulx-firstspeak] session=%s warm-speak: %.2fs",
                    session_id, time.monotonic() - _tw,
                )
            except Exception as exc:  # noqa: BLE001 — warm-up must never break the session
                log.warning("session=%s warm-speak failed: %s", session_id, exc)

            await ws.send_text(json.dumps({"status": "ready"}))

            # ── Idle frame sender ──────────────────────────────────────────────
            idle_idx: list[int] = [0]

            async def _send_idle() -> None:
                """Stream this session avatar's idle frames at 25fps until cancelled.
                Uses 0x00 type-byte prefix so the client can distinguish frame types."""
                try:
                    while True:
                        if sess_idle:
                            await ws.send_bytes(b'\x00' + sess_idle[idle_idx[0] % len(sess_idle)])
                            idle_idx[0] += 1
                        await asyncio.sleep(1.0 / tgt_fps)
                except asyncio.CancelledError:
                    pass

            async def _send_thinking() -> None:
                """Stream this session avatar's thinking frames at 25fps until cancelled.
                Falls back to its idle frames if no thinking cache was loaded."""
                frames = sess_thinking if sess_thinking else sess_idle
                ti = [0]
                try:
                    while True:
                        if frames:
                            await ws.send_bytes(b'\x00' + frames[ti[0] % len(frames)])
                            ti[0] += 1
                        await asyncio.sleep(1.0 / tgt_fps)
                except asyncio.CancelledError:
                    pass

            async def _start_idle() -> asyncio.Task:
                t = asyncio.create_task(_send_idle())
                await ws.send_text(json.dumps({"type": "idle"}))
                return t

            async def _cancel_idle(idle_task: asyncio.Task | None) -> None:
                if idle_task and not idle_task.done():
                    idle_task.cancel()
                    try:
                        await idle_task
                    except asyncio.CancelledError:
                        pass

            # ── Session-level audio context ────────────────────────────────────
            # Initialised once per WebSocket session and KEPT across utterances.
            # Resetting to fresh noise at every speak_start causes a 9-frame
            # transition artifact (motion_frames=9) where the Lite model animates
            # noise-context motion rather than the real audio → perceived 1-2 word lag.
            # Keeping the previous utterance's context primes the motion latent state
            # so animation matches audio from the first output frame of each utterance.
            _session_noise = np.random.default_rng(42).normal(0, 0.001, cached_samples).astype(np.float32)
            audio_dq: deque[float] = deque(_session_noise.tolist(), maxlen=cached_samples)

            # ── Enter idle mode ────────────────────────────────────────────────
            idle_task: asyncio.Task | None = await _start_idle()
            log.info("session=%s idle mode started", session_id)

            # ── Single persistent receiver for the whole session ───────────────
            # ONE in-flight ws.receive() shared by the idle loop AND the speaking
            # send loops. They never receive concurrently (the speaking loop awaits
            # its send helpers inline), so a single task is safe. `_drain_recv()`
            # (defined in the speaking section) lets the per-frame send loops act on
            # a barge-in within ~1 frame instead of after a whole ~1s batch; any
            # non-control message it consumes mid-send is stashed in `pending_msgs`
            # for the next `_next_msg()`.
            pending_msgs: "deque[dict]" = deque()
            recv_task: asyncio.Task = asyncio.ensure_future(ws.receive())

            async def _next_msg() -> dict:
                """Next client message: a stashed one first, else block on recv_task."""
                nonlocal recv_task
                if pending_msgs:
                    return pending_msgs.popleft()
                await asyncio.wait({recv_task})
                msg = recv_task.result()
                recv_task = asyncio.ensure_future(ws.receive())
                return msg

            # ── Main session loop ──────────────────────────────────────────────
            should_close = False
            while not should_close:
                msg = await _next_msg()
                if msg.get("type") == "websocket.disconnect":
                    await _cancel_idle(idle_task)
                    return

                text = msg.get("text")
                if text is None:
                    continue  # ignore unexpected binary in idle mode

                try:
                    cmd      = json.loads(text)
                    msg_type = cmd.get("type", "")
                except Exception:
                    continue

                if msg_type == "close":
                    await _cancel_idle(idle_task)
                    should_close = True
                    break

                if msg_type == "thinking":
                    # Switch to thinking frames while the bot processes the response.
                    log.info("session=%s thinking mode", session_id)
                    await _cancel_idle(idle_task)
                    idle_task = asyncio.create_task(_send_thinking())
                    continue

                if msg_type == "thinking_cancel":
                    # User interrupted while bot was thinking — return to idle.
                    log.info("session=%s thinking cancelled → idle", session_id)
                    await _cancel_idle(idle_task)
                    idle_task = await _start_idle()
                    continue

                if msg_type != "speak_start":
                    continue

                # ── Switch to SPEAKING mode ────────────────────────────────────
                await _cancel_idle(idle_task)
                idle_task = None
                # Confirm to client that idle frames have stopped and we are in
                # speaking mode. Client uses this to start pairing audio with frames.
                await ws.send_text(json.dumps({"type": "speaking"}))

                # audio_dq is intentionally NOT reset here — keeping the previous
                # utterance's context primes the motion latent state so the Lite
                # model's 9 motion-context frames don't cause a cold-start artifact.
                pcm_buffer      = bytearray()
                frames_sent     = 0
                first_chunk_log = False
                t_speak         = time.monotonic()
                log.info("session=%s speaking mode", session_id)

                # ── Speaking inner loop (pipelined) ────────────────────────────
                # Concurrent pipeline: after inference N completes, immediately
                # launch inference N+1 in the thread pool, then echo batch N with
                # 25fps pacing. The asyncio.sleep in the echo loop yields to the
                # event loop, letting inference N+1 run in the thread pool concurrently.
                # For the Pro model this reduces inter-batch gaps from ~1050ms → ~170ms.
                # For a Lite model the gaps would be ~0ms (inference < echo duration).
                next_fut:         asyncio.Future | None = None  # pending inference for next batch
                next_chunk:       np.ndarray | None     = None  # chunk_f32 to echo when it resolves
                echo_deadline:    float                 = 0.0   # wall-clock deadline for next frame
                first_batch_done: bool                  = False
                pre_fill_task:    asyncio.Task | None   = None
                interrupted:      bool                  = False  # barge-in seen mid-send

                def _drain_recv() -> bool:
                    """Non-blocking poll of the client socket from INSIDE the send
                    loops so a barge-in is acted on within ~1 frame instead of after
                    a whole ~1s batch. Returns True if the current send should ABORT
                    (interrupt / close / disconnect). Non-control messages (audio
                    chunks, eof) are stashed in `pending_msgs` for the main loop."""
                    nonlocal recv_task, interrupted, should_close
                    if not recv_task.done():
                        return False
                    try:
                        m = recv_task.result()
                    except Exception:           # receive failed → treat as disconnect
                        should_close = interrupted = True
                        return True
                    recv_task = asyncio.ensure_future(ws.receive())   # re-arm
                    if m.get("type") == "websocket.disconnect":
                        should_close = interrupted = True
                        return True
                    t = m.get("text")
                    if t is not None:
                        try:
                            mt = json.loads(t).get("type", "")
                        except Exception:
                            return False
                        if mt == "interrupt":
                            interrupted = True
                            return True
                        if mt == "close":
                            should_close = interrupted = True
                            return True
                        pending_msgs.append(m)   # eof / other text → main loop
                        return False
                    pending_msgs.append(m)       # binary audio → main loop
                    return False

                def _launch_next() -> None:
                    """If buffer has a full chunk and no inference is pending, start one."""
                    nonlocal next_fut, next_chunk
                    if next_fut is not None or len(pcm_buffer) < chunk_samples * 4:
                        return
                    chunk_bytes = bytes(pcm_buffer[:chunk_samples * 4])
                    del pcm_buffer[:chunk_samples * 4]
                    c   = np.frombuffer(chunk_bytes, dtype=np.float32)
                    audio_dq.extend(c.tolist())
                    arr = np.array(audio_dq, dtype=np.float32)
                    next_chunk = c
                    next_fut   = loop.run_in_executor(None, _infer, arr, audio_start_idx, audio_end_idx)

                async def _echo_batch(c_f32: np.ndarray, sframes) -> None:
                    """Echo audio+video pairs at exactly 25fps using wall-clock deadlines.

                    Encoding JPEG outside the timing loop and sleeping to an absolute
                    deadline (not a fixed duration) prevents the 1.6ms/frame encode+jitter
                    overhead from accumulating into hundreds of ms of video lag.
                    """
                    nonlocal frames_sent, first_chunk_log, echo_deadline
                    a16 = (c_f32 * 32768.0).clip(-32768, 32767).astype(np.int16)
                    pfa = a16.reshape(valid_frames, 640)

                    # Pre-encode all JPEG frames before the send loop so encoding
                    # time doesn't eat into the per-frame sleep budget.
                    jpegs = [_encode_frame(sframes[i]) for i in range(motion_frames, frame_num)]

                    _now = asyncio.get_event_loop().time

                    for idx, jpeg in enumerate(jpegs):
                        if echo_deadline == 0.0:
                            echo_deadline = _now()       # anchor: first frame of utterance
                        await ws.send_bytes(b'\x00' + jpeg)
                        await ws.send_bytes(b'\x01' + pfa[idx].tobytes())
                        frames_sent += 1
                        echo_deadline += 1.0 / tgt_fps  # advance by exactly 40ms
                        sleep_time = echo_deadline - _now()
                        if sleep_time > 0.001:
                            await asyncio.sleep(sleep_time)
                        if _drain_recv():        # barge-in mid-batch → abort immediately
                            return

                    if not first_chunk_log:
                        first_chunk_log = True
                        log.info(
                            "session=%s first chunk at t=%.2fs",
                            session_id, time.monotonic() - t_speak,
                        )

                async def _gap_fill(sframes) -> None:
                    """Stream last frame + silence at 25fps until pending inference completes.

                    Uses the same wall-clock deadline as _echo_batch so that when inference
                    completes and _echo_batch resumes, echo_deadline is current — preventing
                    burst delivery of the first real batch after the gap.
                    """
                    nonlocal echo_deadline
                    last_jpeg    = _encode_frame(sframes[frame_num - 1])
                    silence_1280 = bytes(640 * 2)   # 640 int16 zeros = 40ms @ 16kHz
                    _now_gf = asyncio.get_event_loop().time
                    while next_fut is not None and not next_fut.done():
                        await ws.send_bytes(b'\x00' + last_jpeg)
                        await ws.send_bytes(b'\x01' + silence_1280)
                        echo_deadline += 1.0 / tgt_fps
                        sleep_time = echo_deadline - _now_gf()
                        if sleep_time > 0.001:
                            await asyncio.sleep(sleep_time)
                        if _drain_recv():        # barge-in during gap-fill → abort
                            return

                async def _pre_inference_fill() -> None:
                    """Send silence + idle frames at 25fps during the initial inference gap.

                    After speak_start, the idle animation is cancelled and the server sends
                    nothing for ~1.14s (audio buffer fill + inference). Chrome's jitter-buffer
                    adapts to this gap and sets a ~1s video playout delay for the entire
                    session — causing the persistent "2-word lag" the user sees.

                    This task keeps the video track alive with silence + idle animation until
                    the first real inference completes, so the browser never sees a gap and
                    sets a small (~100ms) jitter buffer instead.
                    """
                    nonlocal echo_deadline
                    _now_pf = asyncio.get_event_loop().time
                    fi      = idle_idx[0]           # local counter; doesn't advance global idx
                    sil     = bytes(640 * 2)         # 640 int16 zeros = 40ms silence @ 16kHz
                    try:
                        while True:
                            jpeg = sess_idle[fi % len(sess_idle)] if sess_idle else b''
                            fi  += 1
                            await ws.send_bytes(b'\x00' + jpeg)
                            await ws.send_bytes(b'\x01' + sil)
                            if echo_deadline == 0.0:
                                echo_deadline = _now_pf()
                            echo_deadline += 1.0 / tgt_fps
                            sleep_time = echo_deadline - _now_pf()
                            if sleep_time > 0.001:
                                await asyncio.sleep(sleep_time)
                            # NOTE: do NOT poll _drain_recv() here — _pre_inference_fill
                            # runs as a concurrent background task while the main loop is
                            # blocked in _next_msg(), so both would race on recv_task. A
                            # barge-in during the pre-inference gap is caught by the main
                            # loop's _next_msg() directly (it's actively awaiting then).
                    except asyncio.CancelledError:
                        pass

                # Start pre-fill: keeps the WebRTC video track alive during inference so
                # Chrome doesn't inflate its jitter buffer to match the gap duration.
                pre_fill_task = asyncio.create_task(_pre_inference_fill())

                while True:
                    # Barge-in (or close/disconnect) seen MID-SEND by _drain_recv (or
                    # via _next_msg below): stop the old animation NOW and return to
                    # idle — this is what makes barge-in feel instant instead of
                    # waiting out the current ~1s batch.
                    if interrupted:
                        if pre_fill_task and not pre_fill_task.done():
                            pre_fill_task.cancel()
                        pre_fill_task = None
                        if next_fut is not None and not next_fut.done():
                            next_fut.cancel()
                        next_fut = next_chunk = None
                        pcm_buffer.clear()
                        pending_msgs.clear()    # drop stale audio/eof from the killed utterance
                        log.info("session=%s interrupted after %d frames", session_id, frames_sent)
                        if not should_close:
                            idle_task = await _start_idle()
                        break

                    # ── PRIORITY: echo any pipelined inference that has completed ──
                    # For the Lite model this is almost always true → batches chain
                    # back-to-back here without reaching _next_msg, which is exactly
                    # why the send loops self-poll (_drain_recv) for barge-in.
                    if next_fut is not None and next_fut.done():
                        sframes      = await next_fut
                        c            = next_chunk
                        next_fut     = next_chunk = None
                        _launch_next()                  # pipeline batch N+2 if ready
                        await _echo_batch(c, sframes)   # may set `interrupted` mid-batch
                        if interrupted:
                            continue                    # → handled at loop top
                        if next_fut is not None and not next_fut.done():
                            await _gap_fill(sframes)
                        continue

                    # ── Next client message (a stashed one first, else block) ──────
                    msg2 = await _next_msg()
                    if msg2.get("type") == "websocket.disconnect":
                        should_close = True
                        break

                    text2 = msg2.get("text")
                    if text2 is not None:
                        try:
                            cmd2      = json.loads(text2)
                            msg_type2 = cmd2.get("type", "")
                        except Exception:
                            continue

                        if msg_type2 == "eof":
                            # Drain any pipelined batch first
                            if next_fut is not None:
                                sframes  = await next_fut
                                await _echo_batch(next_chunk, sframes)
                                next_fut = next_chunk = None
                                if interrupted:
                                    continue
                            # Flush remaining PCM
                            if len(pcm_buffer) >= 4:
                                chunk_f32 = np.frombuffer(bytes(pcm_buffer), dtype=np.float32)
                                if len(chunk_f32) < chunk_samples:
                                    chunk_f32 = np.pad(chunk_f32, (0, chunk_samples - len(chunk_f32)))
                                audio_dq.extend(chunk_f32.tolist())
                                audio_arr  = np.array(audio_dq, dtype=np.float32)
                                sample_frames = await loop.run_in_executor(
                                    None, _infer, audio_arr, audio_start_idx, audio_end_idx
                                )
                                await _echo_batch(chunk_f32, sample_frames)
                                if interrupted:
                                    continue
                            log.info(
                                "session=%s done: %d frames in %.1fs",
                                session_id, frames_sent, time.monotonic() - t_speak,
                            )
                            idle_task = await _start_idle()
                            break

                        elif msg_type2 == "interrupt":
                            interrupted = True
                            continue   # → handled at loop top (cleanup + idle)

                        elif msg_type2 == "close":
                            should_close = True
                            break

                        continue  # ignore other text messages in speaking mode

                    # Binary audio chunk
                    data = msg2.get("bytes")
                    if not data:
                        continue
                    pcm_buffer.extend(data)

                    # Run inference as soon as we have a full chunk (and none pending).
                    # Then immediately pipeline the NEXT chunk before starting the echo.
                    if next_fut is None and len(pcm_buffer) >= chunk_samples * 4:
                        chunk_bytes = bytes(pcm_buffer[:chunk_samples * 4])
                        del pcm_buffer[:chunk_samples * 4]
                        chunk_f32 = np.frombuffer(chunk_bytes, dtype=np.float32)
                        audio_dq.extend(chunk_f32.tolist())
                        audio_arr = np.array(audio_dq, dtype=np.float32)

                        sample_frames = await loop.run_in_executor(
                            None, _infer, audio_arr, audio_start_idx, audio_end_idx
                        )
                        # Stop the pre-fill on first batch: undo its last deadline advance
                        # so _echo_batch's first advance puts the deadline at ~now+40ms.
                        if not first_batch_done:
                            first_batch_done = True
                            if pre_fill_task and not pre_fill_task.done():
                                pre_fill_task.cancel()
                                await asyncio.gather(pre_fill_task, return_exceptions=True)
                            pre_fill_task = None
                            echo_deadline -= 1.0 / tgt_fps
                        # Immediately start next inference (runs in thread pool while we echo)
                        _launch_next()
                        await _echo_batch(chunk_f32, sample_frames)
                        if interrupted:
                            continue                    # → handled at loop top
                        if next_fut is not None and not next_fut.done():
                            await _gap_fill(sample_frames)

            # Clean up idle task if still running when should_close
            await _cancel_idle(idle_task)

    except WebSocketDisconnect:
        log.info("session=%s client disconnected", session_id)
    except Exception as exc:
        log.error("session=%s error: %s", session_id, exc)
        traceback.print_exc()
    finally:
        _busy = False
        try:
            recv_task.cancel()   # drop the persistent ws.receive() (no orphaned task)
        except Exception:        # noqa: BLE001 — undefined if we failed before handshake
            pass
        log.info("session=%s closed", session_id)
