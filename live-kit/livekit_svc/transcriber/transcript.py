"""Transcript accumulator + one-file writer.

Collects diarized segments `(start_time, end_time, speaker, text)` as they arrive
from every participant's Deepgram socket, then on finalize sorts them into one
chronological, speaker-labeled transcript and writes a single local file (JSON +
a human-readable .txt) under TRANSCRIPT_OUTPUT_DIR.

Speaker = the track's participant identity/name, mapped at join time — so this is
true diarization with zero diarization model (each segment came from exactly one
participant's track).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

from livekit_svc.config import get_settings
from livekit_svc.logging import get_logger

log = get_logger(__name__)


@dataclass(order=True)
class Segment:
    start: float
    end: float = field(compare=False)
    speaker: str = field(compare=False, default="")
    text: str = field(compare=False, default="")


class Transcript:
    """Thread-safe-ish accumulator for one room's transcript. Segments arrive
    from multiple async STT tasks; appends are guarded by a lock."""

    def __init__(self, room: str, *, started_at: float) -> None:
        self.room = room
        self.started_at = started_at
        self._segments: list[Segment] = []
        self._lock = threading.Lock()
        # participant identity -> display label (counsellor/candidate/name)
        self._speaker_labels: dict[str, str] = {}

    def set_speaker_label(self, identity: str, label: str) -> None:
        with self._lock:
            self._speaker_labels[identity] = label

    def add(self, *, speaker: str, text: str, start: float, end: float) -> None:
        with self._lock:
            label = self._speaker_labels.get(speaker, speaker)
            self._segments.append(Segment(start=start, end=end, speaker=label, text=text))

    def segment_count(self) -> int:
        with self._lock:
            return len(self._segments)

    def to_dict(self, *, ended_at: float | None = None) -> dict:
        with self._lock:
            ordered = sorted(self._segments)
            return {
                "room": self.room,
                "started_at": self.started_at,
                "ended_at": ended_at,
                "speakers": sorted({s.speaker for s in ordered}),
                "segment_count": len(ordered),
                "segments": [
                    {"start": s.start, "end": s.end, "speaker": s.speaker, "text": s.text}
                    for s in ordered
                ],
            }

    def to_text(self, *, ended_at: float | None = None) -> str:
        d = self.to_dict(ended_at=ended_at)
        lines = [f"# Meeting transcript — room {self.room}", ""]
        for seg in d["segments"]:
            ts = _fmt_ts(seg["start"])
            lines.append(f"[{ts}] {seg['speaker']}: {seg['text']}")
        return "\n".join(lines) + "\n"

    def write_files(self, *, ended_at: float | None = None) -> dict[str, str]:
        """Write <dir>/<room>-<started>.json and .txt. Returns the paths.
        `started_at` is used in the filename (not the unavailable wall clock — the
        runtime forbids Date.now()-style calls in some contexts, so we stamp from
        the start time the caller passed in)."""
        s = get_settings()
        out_dir = Path(s.transcript_output_dir)
        # Only create the dir if it's genuinely missing. On a bind-mounted volume
        # (Docker on Windows) the path already exists as the mount, and
        # mkdir(exist_ok=True) can still raise FileExistsError(17) on that mount —
        # so we guard with an explicit is_dir() check instead of relying on it.
        if not out_dir.is_dir():
            out_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{self.room}-{int(self.started_at)}"
        json_path = out_dir / f"{stem}.json"
        txt_path = out_dir / f"{stem}.txt"
        json_path.write_text(json.dumps(self.to_dict(ended_at=ended_at), indent=2), encoding="utf-8")
        txt_path.write_text(self.to_text(ended_at=ended_at), encoding="utf-8")
        log.info(
            "transcript written",
            room=self.room, segments=self.segment_count(),
            json=str(json_path), txt=str(txt_path),
        )
        return {"json": str(json_path), "txt": str(txt_path)}


def _fmt_ts(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


__all__ = ["Transcript", "Segment"]
