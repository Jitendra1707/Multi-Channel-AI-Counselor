# SoulX inference service

Audio-driven talking-head avatar server (FastAPI + WebSocket on port **8011**).
Animates a tight head crop with SoulX-FlashHead and composites it back onto the
full reference photo (paste-back), so waist-up portraits stay fully visible while
the head lip-syncs. The AegisBackend connects to this service over WebSocket
(`ditto_service_url`); this service does not depend on the backend.

## Concurrency (worker pool)
SoulX-FlashHead is **not re-entrant** — `run_pipeline` mutates per-session motion-latent
state inside one pipeline, so a single process can drive only **one** stream at a time. To
serve N concurrent streams from one GPU we run **`SOULX_POOL_SIZE` worker processes** (one
pipeline each, ~9 GB VRAM/worker) behind **nginx** on 8011 — concurrency by *replication*,
not by multiplexing one pipeline (which would cross-corrupt). `entrypoint.sh` renders the
nginx + supervisord configs and launches the pool:

```
nginx :8011  ──least_conn, max_conns=1──▶  worker 0 :8012  (PRIMARY: makes the .pkl caches)
                                           worker 1 :8013
                                           worker i :8012+i
```

`max_conns=1` pins each WebSocket to one worker for its lifetime and caps concurrency at K
(a (K+1)-th client gets a 502, never a corrupted stream). Only worker 0 generates the shared
idle/thinking `.pkl` caches; the others wait for them (no K-way regen storm).

### Sizing K (load-tested on the NC40ads_H100_v5)
VRAM is **not** the limit (~6.5 GB/worker; 10 workers ≈ 65 GB of 96 GB). **Compute is** — K Lite
inferences time-slice the one GPU and must each hold real-time 25 fps. Measured from a
high-bandwidth client as **ratio = delivered-video-seconds / wall** (1.0 =
real-time), with all N streams talking continuously (worst case):

| K | ratio | GPU duty | note |
|---|-------|----------|------|
| 6 | ~1.00 | ~50% | smooth, big headroom |
| 8 | 1.00 | ~80% | smooth, ~20% headroom |
| 9 | 0.97 | ~100% | real-time, zero headroom |
| 10 | 0.88 | 100% (pinned) | over the edge — ~22 fps, lip-drift |

**Real-time ceiling ≈ 9.** Shipping **K=10** for max capacity: the GPU runs at the redline and all
active streams soften to ~0.88 only when ~all of them talk *simultaneously* (rare — real calls are
bursty, so the GPU catches up during pauses). Drop to **8** for ~20% headroom if peak-time choppiness
shows up. Tune via `SOULX_POOL_SIZE` (Dockerfile default + the k8s env).

> Measure from a **high-bandwidth client in/near the cluster** (the in-cluster backend, the GPU/build
> VM, or loopback inside the pod) — a laptop over the WAN saturates its own downlink past ~6 streams
> and the backpressure fakes a low ceiling. Note K only sizes the **SoulX GPU**; total concurrent
> calls = min(this, the CPU backend's pipeline capacity, LLM limits).

### Multiple avatars
A stream may pass its own `image_path` in the handshake to animate a **different** avatar.
Its speaking frames lip-sync that photo (per-session `get_base_data`), and its idle/thinking
animation loads from **per-avatar caches named `<stem>.idle_frames.pkl` / `<stem>.thinking_frames.pkl`
beside the photo** (the default `/refs/face.jpg` keeps the legacy unprefixed names). These are
**not** generated on the request path (that takes minutes) — **pre-seed** each avatar's photo +
two `.pkl`s into the mount. A missing cache falls back to the default avatar's idle frames (with
a warning) so the stream still works; the idle face just won't match until you seed it.

## Contents
- `soulx_server.py` — the inference server (handshake → idle/thinking/speaking frames).
- `debug_crop.py` — standalone tool to visualize the head-crop framing on a reference
  image without a full server cache regen (`python debug_crop.py <image> [seg_threshold]`).
- `Dockerfile` / `requirements.txt` / `.dockerignore` — container build.

## What's baked vs mounted
The image is **self-contained for models** — a multi-stage build bakes the ~14.7 GB of
weights in (downloaded from HuggingFace at build time). Only the avatar photo and the
writable `.pkl` cache come from a runtime mount.

| Thing | Size | In image? |
|---|---|---|
| SoulX-FlashHead lib (`flash_head`) | small | ✅ cloned at pinned commit + patched |
| App code (`soulx_server.py`, `debug_crop.py`) | small | ✅ |
| mediapipe Tasks models (BlazeFace, selfie segmenter) | ~0.5 MB | ✅ baked |
| Python deps (torch+cu128, flash_attn, …) | ~8 GB | ✅ baked (compiled in builder stage) |
| **Model weights** — `SoulX-FlashHead-1_3B` (Pro 6.0 + Lite 6.1 + VAE_LTX 1.7 + VAE_Wan 0.5) + `wav2vec2-base-960h` (0.4) | **~14.7 GB** | ✅ **baked** (`hf download` → `/models/soulx-models`) |
| **Reference photo `face.jpg` + idle/thinking `.pkl` cache** | tiny / a few MB | ❌ **mount at `/refs`** (Azure Blob CSI, read-write) |

The weights are baked to make the image portable (no weights PVC / azcopy). Cost: image is
**~25–30 GB** and changing weights needs a rebuild. The reference photo stays out of the
image so the avatar is swappable and the `.pkl` cache can persist in Blob.

## Build
```bash
az acr build -r <acr> -t sketch-avatar/soulx-service:1 soulx_service/
# or locally: docker build -t sketch-avatar/soulx-service:1 soulx_service/
```
- **Multi-stage (both stages on the CUDA *devel* base):** the builder compiles deps +
  downloads weights; the final stage carries the packages, app code, and baked weights — and
  **keeps the devel toolchain** (nvcc/gcc/Python headers), because Triton/Inductor JIT-compiles
  CUDA kernels at runtime, so a slim runtime base crashes at warmup.
- `requirements.txt` is the **exact `pip freeze` lockfile from the working VM** (torch
  2.7.1+cu128, flash_attn 2.8.3, mediapipe 0.10.35, …). Regenerate it if the VM env changes.
- **No GPU needed at build** (the devel base ships `nvcc`); an NVIDIA GPU is required only
  **at runtime**. The build agent needs **~60–80 GB free disk** (~15 GB HF download + ~30 GB
  image) — if ACR Tasks' default agent is too small, build on a VM with Docker and push.
- Build needs network access to GitHub (SoulX-FlashHead), PyPI + the PyTorch cu128 index,
  HuggingFace (weights), and the mediapipe model CDN. Budget **~30–50 min**.
- `flash_attn==2.8.3` is **sdist-only** and its build imports torch, so the Dockerfile
  installs torch first then builds flash_attn with `--no-build-isolation` (single flat
  `pip install -r requirements.txt` would fail with `ModuleNotFoundError: torch`).

## Run (single container — only the reference photo is mounted)
```bash
docker run --gpus all -p 8011:8011 \
  -v /data/refs:/refs \
  -e SOULX_REFERENCE_IMAGE=/refs/face.jpg \
  sketch-avatar/soulx-service:1
# health: curl localhost:8011/health   ;   avatar WS: ws://<host>:8011/ws
```
Weights are already in the image; mount **only** `/refs` (with a `face.jpg` in it). Keep the
mount **writable** — first start regenerates the idle/thinking `.pkl`s (~8 min, Pro model)
into `/refs`, so a persistent writable mount lets them survive restarts.

## Configuration (env)
| Var | Default | Purpose |
|---|---|---|
| `SOULX_POOL_SIZE` | `3` | **Worker processes (concurrent streams) on the one GPU.** Set `1` for old single-process behaviour. |
| `SOULX_REFERENCE_IMAGE` | `/refs/face.jpg` | **The single source of truth for the avatar photo.** |
| `SOULX_CKPT_DIR` / `SOULX_WAV2VEC_DIR` | `/models/...` | Weight locations. |
| `SOULX_FACE_MODEL` / `SOULX_SEG_MODEL` | `/models/mp/*.tflite` | mediapipe Tasks models. |
| `SOULX_PASTE_BACK` | `1` | Paste-back on/off (`0` = raw full-frame). |
| `SOULX_HEAD_TOP_MARGIN` / `SOULX_HEAD_CROP_FACTOR` / `SOULX_FEATHER` | `0.22` / `1.6` / `0.06` | Head-crop framing + seam feather. |
| `SOULX_SEG_THRESHOLD` | `0.2` | Person-mask threshold (lower = more inclusive of hair). |
| `SOULX_FACE_RATIO` / `SOULX_OUTPUT_MAX_SIDE` | `2.0` / `0` | Face-box fallback ratio; output downscale cap. |

## AKS notes (manifests are a follow-up)
- Schedule on a **GPU node pool**; request `nvidia.com/gpu: 1` (needs the NVIDIA device
  plugin). The image is built on CUDA **12.8** — the node driver must be ≥ compatible
  (newer drivers are backward-compatible with the 12.8 torch build).
- **No weights PVC** — they're baked into the image. Mount **only** `/refs` from an **Azure
  Blob container via the Blob CSI driver, read-write**, so `face.jpg` is supplied and the
  idle/thinking `.pkl` cache persists across pod restarts (else ~8 min regen on every cold
  start). Override `SOULX_REFERENCE_IMAGE` only if the filename differs.
- Expose port 8011 via a Service; the AegisBackend's `ditto_service_url` points at it
  (`ws://soulx-service:8011/ws`).
- Use a long **startup probe** on `/health` (~120 s warmup + up to ~8 min first-run `.pkl`
  regen) and pull the ~25–30 GB image onto the node before scheduling expectations.
