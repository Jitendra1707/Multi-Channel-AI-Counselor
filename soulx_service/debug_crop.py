#!/usr/bin/env python3
"""Visualize the paste-back crop box on a reference image — for tuning the head
segmentation WITHOUT an 8-min SoulX server cache regen.

Runs the SAME face-detect + person-segmentation as soulx_server.py at a chosen
threshold, prints the detected head_top + crop box, and writes <image>_cropdbg.jpg
with the segmentation mask (green) and the crop box (red) drawn so you can eyeball
whether the box clears all the hair.

Usage (on the VM, soulx-env active):
    python debug_crop.py ~/ditto_service/face.jpg [threshold]
    # try a few:  python debug_crop.py ~/ditto_service/face.jpg 0.3
    #             python debug_crop.py ~/ditto_service/face.jpg 0.1
    #             python debug_crop.py ~/ditto_service/face.jpg 0.05

Env (same as the server): SOULX_FACE_MODEL, SOULX_SEG_MODEL,
SOULX_HEAD_TOP_MARGIN, SOULX_HEAD_CROP_FACTOR, SOULX_SEG_THRESHOLD.

If even threshold ~0.05 can't make the green mask cover the upper hair, the binary
selfie model is inadequate → switch SOULX_SEG_MODEL to the multiclass model
(selfie_multiclass_256x256.tflite) and combine its hair+skin+body categories.
"""
import os
import sys

import cv2
import numpy as np
from PIL import Image
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

_HOME = os.path.expanduser("~")
FACE_MODEL = os.environ.get("SOULX_FACE_MODEL", f"{_HOME}/ditto_service/blaze_face_short_range.tflite")
SEG_MODEL  = os.environ.get("SOULX_SEG_MODEL",  f"{_HOME}/ditto_service/selfie_segmenter.tflite")
TOP_MARGIN  = float(os.environ.get("SOULX_HEAD_TOP_MARGIN", "0.22"))
CROP_FACTOR = float(os.environ.get("SOULX_HEAD_CROP_FACTOR", "1.6"))
FEATHER     = float(os.environ.get("SOULX_FEATHER", "0.06"))


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python debug_crop.py <image> [threshold]")
        raise SystemExit(2)
    image_path = sys.argv[1]
    thresh = float(sys.argv[2]) if len(sys.argv) > 2 else float(os.environ.get("SOULX_SEG_THRESHOLD", "0.2"))

    img = Image.open(image_path).convert("RGB")
    rgb = np.array(img)
    h, w = rgb.shape[:2]
    mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))

    fd = mp_vision.FaceDetector.create_from_options(mp_vision.FaceDetectorOptions(
        base_options=mp_python.BaseOptions(model_asset_path=FACE_MODEL),
        running_mode=mp_vision.RunningMode.IMAGE))
    res = fd.detect(mp_img)
    if not res.detections:
        print("NO FACE DETECTED")
        raise SystemExit(1)
    bb = max(res.detections, key=lambda d: d.bounding_box.width * d.bounding_box.height).bounding_box
    fx1, fy1, fx2, fy2 = bb.origin_x, bb.origin_y, bb.origin_x + bb.width, bb.origin_y + bb.height

    seg = mp_vision.ImageSegmenter.create_from_options(mp_vision.ImageSegmenterOptions(
        base_options=mp_python.BaseOptions(model_asset_path=SEG_MODEL),
        running_mode=mp_vision.RunningMode.IMAGE, output_confidence_masks=True))
    conf = np.squeeze(seg.segment(mp_img).confidence_masks[0].numpy_view())  # (H,W)
    mask = conf >= thresh

    cx, cy, fw, chin = (fx1 + fx2) // 2, (fy1 + fy2) // 2, fx2 - fx1, fy2
    bl, br = max(0, int(cx - 1.2 * fw)), min(w, int(cx + 1.2 * fw))
    rows = np.where(mask[:max(1, cy), bl:br].any(axis=1))[0]
    head_top = int(rows.min()) if len(rows) else fy1
    head_h = max(1, chin - head_top)
    half = (head_h * CROP_FACTOR) / 2.0
    cols = np.where(mask[head_top:max(head_top + 1, fy1), bl:br].any(axis=0))[0]
    if len(cols):
        half = max(half, cx - (bl + int(cols.min())), (bl + int(cols.max())) - cx)
    top = head_top - TOP_MARGIN * head_h
    x1, x2 = int(max(0, cx - half)), int(min(w, cx + half))
    y1, y2 = int(max(0, top)), int(min(h, top + 2 * half))

    # Opaque interior = crop box inset by the feather band; only THIS region animates
    # 100% (outside it is a static→animated blend). The whole head must sit inside it.
    ix, iy = int(FEATHER * (x2 - x1)), int(FEATHER * (y2 - y1))
    ox1, oy1, ox2, oy2 = x1 + ix, y1 + iy, x2 - ix, y2 - iy

    print(f"thresh={thresh}  face_box=({fx1},{fy1},{fx2},{fy2})  head_top={head_top}  "
          f"box=({x1},{y1},{x2},{y2})  opaque=({ox1},{oy1},{ox2},{oy2})  img={w}x{h}")

    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    over = bgr.copy()
    over[mask] = (0, 255, 0)
    bgr = cv2.addWeighted(over, 0.35, bgr, 0.65, 0)
    cv2.rectangle(bgr, (x1, y1), (x2, y2), (0, 0, 255), 3)            # crop box (red)
    cv2.rectangle(bgr, (ox1, oy1), (ox2, oy2), (0, 255, 255), 2)      # opaque interior (yellow)
    cv2.line(bgr, (bl, head_top), (br, head_top), (255, 0, 0), 2)    # detected head_top (blue)
    out = os.path.splitext(image_path)[0] + "_cropdbg.jpg"
    cv2.imwrite(out, bgr)
    print("saved", out)


if __name__ == "__main__":
    main()
