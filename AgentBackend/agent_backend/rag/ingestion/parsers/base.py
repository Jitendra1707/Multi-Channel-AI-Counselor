"""Parser protocol + factory."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_backend.rag.ingestion import settings as ing_settings


@runtime_checkable
class Parser(Protocol):
    name: str

    def to_markdown(self, path: str, ext: str) -> str:
        """Return the document at `path` as markdown / plain text. `ext` is the
        lowercased extension without the dot (e.g. 'pdf', 'txt', 'md')."""
        ...


def get_parser() -> Parser:
    """Resolve the configured parser (see module docstring in __init__)."""
    mode = ing_settings.PARSER
    if mode == "local":
        from agent_backend.rag.ingestion.parsers.local import LocalParser

        return LocalParser()
    if mode == "llamaparse":
        from agent_backend.rag.ingestion.parsers.llamaparse import LlamaParseV2

        return LlamaParseV2()
    # auto: LlamaParse when a key is present, else local.
    if ing_settings.llama_cloud_key():
        from agent_backend.rag.ingestion.parsers.llamaparse import LlamaParseV2

        return LlamaParseV2()
    from agent_backend.rag.ingestion.parsers.local import LocalParser

    return LocalParser()
