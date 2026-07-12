"""Pluggable document parsers for ingestion.

`get_parser()` returns the configured parser:
  - RAG_PARSER=auto (default): LlamaParse v2 if LLAMA_CLOUD_API_KEY is set, else
    the local pypdf/text parser.
  - RAG_PARSER=llamaparse: force LlamaParse v2 (errors if no key).
  - RAG_PARSER=local: force the local parser (no API key, works offline).

Every parser implements `Parser.to_markdown(path, ext) -> str`, returning the
document as markdown/plain text for the chunker. New parsers (e.g. a future
Python-capable LiteParse) drop in as one more implementation.
"""
from agent_backend.rag.ingestion.parsers.base import Parser, get_parser

__all__ = ["Parser", "get_parser"]
