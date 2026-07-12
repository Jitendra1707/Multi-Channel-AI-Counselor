"""LlamaParse v2 parser (LlamaIndex hosted parsing via the `llama-cloud` SDK).

v2 uses a tier system (fast | cost_effective | agentic | agentic_plus) instead
of the old mode/model params. 'agentic' is the strong, table-aware default —
ideal for fee tables, brochures, and structured PDFs. Requires
LLAMA_CLOUD_API_KEY (settings.LLAMA_CLOUD_API_KEY).

Plain text / markdown are read locally (no value in sending them to a hosted
parser). Only PDFs (and other rich docs) go through LlamaParse.

The SDK surface is still young, so the parse call is written defensively: it
tries the documented client flow (files.create → parsing.parse → markdown) and
raises a clear error if the installed SDK shape differs, so failures are
actionable rather than cryptic.
"""
from __future__ import annotations

from pathlib import Path

from agent_backend.rag.ingestion import settings as ing_settings


class LlamaParseV2:
    name = "llamaparse-v2"

    def __init__(self) -> None:
        if not ing_settings.llama_cloud_key():
            raise RuntimeError(
                "LLAMA_CLOUD_API_KEY is not set — cannot use LlamaParse v2. "
                "Set it in AgentBackend/.env, or use RAG_PARSER=local."
            )

    def to_markdown(self, path: str, ext: str) -> str:
        # Plain text/markdown: read directly — no need for the hosted parser.
        if ext in ("txt", "md", "markdown"):
            return Path(path).read_text(encoding="utf-8", errors="replace")

        try:
            from llama_cloud import LlamaCloud
        except ModuleNotFoundError as e:  # pragma: no cover
            raise RuntimeError(
                "llama-cloud is not installed. pip install -r requirements.txt"
            ) from e

        client = LlamaCloud(token=ing_settings.llama_cloud_key())
        try:
            uploaded = client.files.create(file=Path(path), purpose="parse")
            result = client.parsing.parse(
                file_id=uploaded.id,
                tier=ing_settings.LLAMAPARSE_TIER,
                version=ing_settings.LLAMAPARSE_VERSION,
                expand=["markdown"],
            )
            return self._markdown_from_result(result)
        except TypeError as e:
            # SDK signature drift — surface a clear, actionable error.
            raise RuntimeError(
                "LlamaParse v2 SDK call shape differs from expected "
                f"(llama-cloud {_pkg_ver()}). Check the parse() signature, or "
                "set RAG_PARSER=local to use the offline pypdf parser. "
                f"Underlying error: {e}"
            ) from e

    @staticmethod
    def _markdown_from_result(result: object) -> str:
        """Pull markdown out of the parse result across SDK shapes."""
        md = getattr(result, "markdown", None)
        if md is None:
            return str(result)
        # v2: result.markdown.pages[i].markdown
        pages = getattr(md, "pages", None)
        if pages:
            return "\n\n".join(getattr(p, "markdown", "") or "" for p in pages).strip()
        if isinstance(md, str):
            return md
        return str(md)


def _pkg_ver() -> str:
    try:
        import llama_cloud

        return getattr(llama_cloud, "__version__", "?")
    except Exception:  # noqa: BLE001
        return "?"
