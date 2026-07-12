"""Local parser — pypdf for PDFs, raw read for txt/md. No API key, works offline.

The fallback when LlamaParse isn't configured. Good enough for plain text and
simple PDFs; LlamaParse v2 is preferred for complex layouts / tables.
"""
from __future__ import annotations

from pathlib import Path


class LocalParser:
    name = "local"

    def to_markdown(self, path: str, ext: str) -> str:
        if ext in ("txt", "md", "markdown"):
            return Path(path).read_text(encoding="utf-8", errors="replace")
        if ext == "pdf":
            return self._pdf(path)
        raise ValueError(f"LocalParser cannot handle '.{ext}' files")

    @staticmethod
    def _pdf(path: str) -> str:
        try:
            from pypdf import PdfReader
        except ModuleNotFoundError as e:  # pragma: no cover
            raise RuntimeError("pypdf is not installed. pip install -r requirements.txt") from e
        reader = PdfReader(path)
        # One '## Page N' heading per page so the chunker keeps page structure.
        parts: list[str] = []
        for i, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or "").strip()
            if text:
                parts.append(f"## Page {i}\n\n{text}")
        return "\n\n".join(parts)
