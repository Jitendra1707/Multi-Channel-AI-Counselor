# SoulX talking-head inference service — self-contained AKS image (weights baked in).
#
# Multi-stage (both stages on the CUDA *devel* base):
#   • builder: installs the exact VM dependency lockfile + SoulX-FlashHead (pinned commit) +
#     compiles flash_attn from source, then downloads the ~14.7 GB model weights from HuggingFace.
#   • runtime: carries the installed packages, app code, and baked weights. It KEEPS the devel
#     toolchain (nvcc/gcc/Python headers) because Triton/Inductor JIT-compiles CUDA kernels at
#     runtime — a slim runtime base fails at warmup. Image is ~30 GB.
#
# What's baked: SoulX-FlashHead-1_3B (Pro+Lite+VAEs), wav2vec2-base-960h, the mediapipe Tasks
# models, and all Python deps. What's NOT baked: the avatar photo `face.jpg` and the writable
# idle/thinking `.pkl` cache — both come from an Azure Blob mount at /refs at runtime.
# Requires an NVIDIA GPU at runtime (not at build).

# ============ Stage 1: builder (CUDA devel — provides nvcc for flash_attn) ============
FROM nvidia/cuda:12.8.1-cudnn-devel-ubuntu24.04 AS builder
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.12 python3-pip python3.12-dev git ffmpeg libgl1 libglib2.0-0 \
        wget build-essential ninja-build \
    && ln -sf /usr/bin/python3.12 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

# ── Exact dep lockfile from the working VM soulx-env (pip freeze) ──
# Staged install: flash_attn==2.8.3 is sdist-only and its setup.py does `import torch` at
# build time, so a single flat `pip install -r requirements.txt` fails under PEP 517 build
# isolation (ModuleNotFoundError: torch). Install the torch stack FIRST, then build flash_attn
# with --no-build-isolation (so it sees the installed torch), then the rest of the lockfile.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --break-system-packages \
      --extra-index-url https://download.pytorch.org/whl/cu128 \
      torch==2.7.1+cu128 torchvision==0.22.1+cu128 torchaudio==2.7.1+cu128
RUN pip install --no-cache-dir --break-system-packages ninja packaging setuptools wheel
# MAX_JOBS bounds the ninja compile's RAM (flash_attn OOMs with all cores on small agents).
RUN MAX_JOBS=4 pip install --no-cache-dir --break-system-packages \
      --no-build-isolation flash_attn==2.8.3
# Debian's apt-installed wheel/setuptools have no pip RECORD file, so the lockfile step below
# can't "uninstall" them when it pins newer versions (it aborts with a RECORD-not-found error).
# Pre-install the pinned versions with --ignore-installed so the lockfile finds them satisfied.
RUN pip install --no-cache-dir --break-system-packages --ignore-installed \
      setuptools==70.2.0 wheel==0.47.0
RUN pip install --no-cache-dir --break-system-packages \
      --extra-index-url https://download.pytorch.org/whl/cu128 \
      -r /tmp/requirements.txt

# ── SoulX-FlashHead library (provides `flash_head`) — pinned to the VM commit ──
# NOTE: we do NOT install its requirements.txt — the lockfile above is the complete, working
# dep set (the lib pins mediapipe==0.10.9, which would break our Tasks-API code).
ARG SOULX_REPO=https://github.com/Soul-AILab/SoulX-FlashHead.git
ARG SOULX_REF=9bc03de06bb0de82cd6bc477804512ae06144bf2
RUN git clone ${SOULX_REPO} /opt/SoulX-FlashHead && \
    cd /opt/SoulX-FlashHead && git checkout ${SOULX_REF}

# Re-apply the only setup_soulx.sh code patch present on the VM (sample_steps=4).
# (color_correction_strength is already 1.0 at this commit.)
RUN cd /opt/SoulX-FlashHead && INF=flash_head/inference.py && \
    (grep "sample_steps" "$INF" | grep -v "20" | grep -q "4" || { \
       LINE=$(grep -n "sample_steps" "$INF" | grep -v "20" | head -1 | cut -d: -f1); \
       sed -i "${LINE}s/.*/        infer_params[\"sample_steps\"] = 4/" "$INF"; })

# ── App code (imported alongside flash_head) ──
COPY soulx_server.py debug_crop.py /opt/SoulX-FlashHead/

# ── Small mediapipe Tasks models baked in (~0.5 MB total) ──
RUN mkdir -p /models/mp && \
    wget -qO /models/mp/blaze_face_short_range.tflite \
      https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite && \
    wget -qO /models/mp/selfie_segmenter.tflite \
      https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter/float16/latest/selfie_segmenter.tflite

# ── Bake the ~14.7 GB model weights from HuggingFace (own layer → cached across rebuilds) ──
# SoulX-FlashHead-1_3B = Model_Pro (~6.0 GB) + Model_Lite (~6.1 GB) + VAE_LTX (~1.7 GB) +
# VAE_Wan (~0.5 GB); wav2vec2-base-960h (~0.38 GB, safetensors only). hf_transfer = fast pulls.
ENV HF_HUB_ENABLE_HF_TRANSFER=1
RUN pip install --no-cache-dir --break-system-packages "huggingface_hub[hf_transfer]"
RUN hf download Soul-AILab/SoulX-FlashHead-1_3B \
        --local-dir /models/soulx-models/SoulX-FlashHead-1_3B && \
    hf download facebook/wav2vec2-base-960h --exclude "*.bin" \
        --local-dir /models/soulx-models/wav2vec2-base-960h && \
    rm -rf /root/.cache/huggingface

# ============ Stage 2: runtime (CUDA *devel* — keeps the toolchain for Triton's runtime JIT) ===
# Triton/Inductor compiles CUDA kernels at runtime, which needs: nvcc + CUDA headers (devel
# base), gcc (build-essential), and Python.h (python3.12-dev). libgles2/libegl1 provide the GL
# that mediapipe uses for the paste-back face detection/segmentation. A slim runtime base omits
# all of these and the server crashes during warmup — hence the devel base here too.
FROM nvidia/cuda:12.8.1-cudnn-devel-ubuntu24.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.12 python3.12-dev ffmpeg libgl1 libglib2.0-0 libgles2 libegl1 build-essential \
        nginx supervisor \
    && ln -sf /usr/bin/python3.12 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

# Installed Python packages (incl. the compiled flash_attn .so + torch's bundled CUDA libs),
# console scripts, app code, and the baked models — copied from the builder stage.
COPY --from=builder /usr/local/lib/python3.12/dist-packages /usr/local/lib/python3.12/dist-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /opt/SoulX-FlashHead /opt/SoulX-FlashHead
COPY --from=builder /models /models

# soulx_server.py does os.chdir(os.path.expanduser("~/SoulX-FlashHead")); in-container ~ is
# /root, but the lib lives at /opt — symlink so the chdir resolves (else it crashes on import).
RUN ln -s /opt/SoulX-FlashHead /root/SoulX-FlashHead

WORKDIR /opt/SoulX-FlashHead
ENV PYTHONPATH=/opt/SoulX-FlashHead \
    XFORMERS_IGNORE_FLASH_VERSION_CHECK=1 \
    SOULX_FACE_MODEL=/models/mp/blaze_face_short_range.tflite \
    SOULX_SEG_MODEL=/models/mp/selfie_segmenter.tflite \
    SOULX_CKPT_DIR=/models/soulx-models/SoulX-FlashHead-1_3B \
    SOULX_WAV2VEC_DIR=/models/soulx-models/wav2vec2-base-960h \
    # Avatar photo + writable .pkl cache come from an Azure Blob mount at /refs (NOT baked):
    SOULX_REFERENCE_IMAGE=/refs/face.jpg

# Pool entrypoint: runs SOULX_POOL_SIZE worker processes behind nginx on :8011
# (one stream per worker, all sharing the one GPU). Set SOULX_POOL_SIZE=1 to get
# the old single-process behaviour (worker on :8012, nginx fronting it).
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
# Strip any CRLF (file is authored on Windows) so the shebang resolves on Linux.
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh && chmod +x /usr/local/bin/entrypoint.sh
ENV SOULX_POOL_SIZE=10

EXPOSE 8011
# nginx fronts the pool on 8011; each worker uvicorn runs on 127.0.0.1:8012+i via
# `python -m` (the console-script shebang points at an uninstalled /usr/bin/python3;
# the `python` symlink → python3.12 always resolves).
CMD ["/usr/local/bin/entrypoint.sh"]
