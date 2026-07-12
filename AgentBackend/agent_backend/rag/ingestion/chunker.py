"""Markdown-aware, token-sized chunking for ingestion.

Self-contained (the old rag chunker was retired with the .npz store). Strategy:
  - Split on markdown headings (#/##/###) — natural topic boundaries; the
    heading breadcrumb is carried as the chunk's `heading`.
  - Pack section content to ~target tokens (tiktoken cl100k_base), with overlap
    between consecutive prose chunks so a fact split across a boundary survives.
  - Markdown tables are ATOMIC — a fee/scholarship table is never split.
  - Each chunk gets metadata the QUERY side reads: heading, topic (keyword
    classified), fee/date sensitivity flags, plus source/version/page.

Produces `Chunk(heading, text, meta)` ready for embedding + upsert.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


@lru_cache(maxsize=1)
def _encoder():
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoder().encode(text))


def _truncate(text: str, max_tokens: int) -> str:
    enc = _encoder()
    return enc.decode(enc.encode(text)[:max_tokens])


def _tail(text: str, n: int) -> str:
    enc = _encoder()
    toks = enc.encode(text)
    return enc.decode(toks[-n:]) if n > 0 and toks else ""


# --- topic + sensitivity (keyword rules; mirror the query-side payload) ----
_TOPIC_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("fees",          ("fee", "scholarship", "tuition", "waiver", "cgpa")),
    ("eligibility",   ("eligibility", "eligible", "cutoff", "criteria")),
    ("application",   ("application", "apply", "document", "procedure", "deadline")),
    ("admission",     ("counselling", "counseling", "seat allotment", "allotment",
                       "admission", "sucet", "eapcet", "forfeit")),
    ("programs",      ("pathway", "b.tech", "btech", "bba", "school of",
                       "specialization", "learning area")),
    ("placements",    ("placement", "recruiter", "salary", "package", "hiring", "ctc")),
    ("reservation",   ("reservation", "seat classification", "quota")),
    ("certification", ("sap", "certification", "add-on", "global certification")),
    ("governance",    ("regulatory", "governance", "leadership", "approval", "act")),
    ("overview",      ("overview", "about", "campus", "contact", "located", "group")),
]
_FEE_PAT = re.compile(r"(₹|rs\.?\s*\d|fee|scholarship|tuition|deposit)", re.I)
_DATE_PAT = re.compile(
    r"(deadline|last date|due date|\b20\d{2}\b|prevailing|exchange rate|"
    r"\b\d{1,2}(st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec))",
    re.I,
)


def classify_topic(heading: str, text: str) -> str:
    hay = f"{heading}\n{text}".lower()
    for topic, kws in _TOPIC_RULES:
        if any(kw in hay for kw in kws):
            return topic
    return "general"


def sensitivity_flags(heading: str, text: str) -> dict[str, bool]:
    blob = f"{heading}\n{text}"
    return {
        "fee_sensitive": bool(_FEE_PAT.search(blob)),
        "date_sensitive": bool(_DATE_PAT.search(blob)),
    }


@dataclass(frozen=True)
class Chunk:
    heading: str
    text: str
    meta: dict[str, Any] = field(default_factory=dict)

    def for_embedding(self) -> str:
        return f"{self.heading}\n{self.text}".strip() if self.heading else self.text


# --- section parsing -------------------------------------------------------
def _sections(md: str) -> list[tuple[str, str]]:
    stack: list[tuple[int, str]] = []
    out: list[tuple[str, str]] = []
    body: list[str] = []

    def crumb() -> str:
        return " > ".join(t for lvl, t in stack if lvl > 1)

    def flush() -> None:
        text = re.sub(r"\n-{3,}\n", "\n", "\n".join(body)).strip()
        if text:
            out.append((crumb(), text))
        body.clear()

    for line in md.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, m.group(2).strip()))
        else:
            body.append(line)
    flush()
    return out


@dataclass
class _Block:
    text: str
    is_table: bool


def _blocks(body: str) -> list[_Block]:
    """Split a section body into ATOMIC blocks: each prose paragraph and each
    table is one block. A block is the smallest unit the packer will NEVER cut
    in the middle — so a paragraph or a table is always kept whole."""
    blocks: list[_Block] = []
    buf: list[str] = []
    in_table = False

    def flush_prose() -> None:
        chunk = "\n".join(buf).strip()
        buf.clear()
        for p in re.split(r"\n\s*\n", chunk):
            if p.strip():
                blocks.append(_Block(p.strip(), False))

    def flush_table() -> None:
        if buf:
            blocks.append(_Block("\n".join(buf).strip(), True))
            buf.clear()

    for line in body.splitlines():
        is_row = bool(_TABLE_ROW_RE.match(line))
        if is_row and not in_table:
            flush_prose(); in_table = True
        elif not is_row and in_table:
            flush_table(); in_table = False
        buf.append(line)
    flush_table() if in_table else flush_prose()
    return blocks


# Sentence boundary for the LAST-RESORT split of a single block that is bigger
# than the hard ceiling. We split BETWEEN sentences (after . ! ? : or a newline),
# never mid-sentence.
_SENT_SPLIT = re.compile(r"(?<=[.!?:])\s+(?=[A-Z(0-9])|\n{2,}")


def _split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_SPLIT.split(text) if p and p.strip()]
    return parts or [text]


def _emit_oversize_block(b: "_Block", *, target: int, hard_max: int) -> list[str]:
    """A SINGLE block (paragraph/table) that exceeds the target.

    Boundary-respecting rule: keep the whole block as ONE chunk (let it overflow)
    so we never cut a topic in half. Only if it's also bigger than the hard
    ceiling do we split — and then:
      - a TABLE is hard-cut at the ceiling (a table can't be sentence-split);
      - PROSE is packed by WHOLE SENTENCES into ceiling-sized pieces (never
        mid-sentence).
    """
    bt = count_tokens(b.text)
    if bt <= hard_max:
        return [b.text]                       # overflow whole — never cut a unit
    if b.is_table:
        return [_truncate(b.text, hard_max)]  # tables can't be sentence-split
    # Pathological huge paragraph: pack full sentences up to the ceiling.
    out: list[str] = []
    cur: list[str] = []
    cur_tok = 0
    for sent in _split_sentences(b.text):
        st = count_tokens(sent)
        if cur and cur_tok + st > hard_max:
            out.append(" ".join(cur)); cur, cur_tok = [], 0
        cur.append(sent); cur_tok += st
    if cur:
        out.append(" ".join(cur))
    return out


def _pack(blocks: list[_Block], *, target: int, overlap: int, hard_max: int) -> list[str]:
    """Pack atomic blocks into chunks WITHOUT ever cutting a block in half.

    Rules (boundary-respecting / 'complete the topic'):
      - A block (paragraph or table) is atomic — it is never split across chunks.
      - Blocks are accumulated until adding the NEXT one would exceed the target;
        then the current chunk is emitted and the next block starts a new one. A
        block that alone exceeds the target becomes its own (overflowing) chunk
        via `_emit_oversize_block` — we let it complete rather than cut it.
      - A HEADING-only first block stays glued to the content that follows (the
        section breadcrumb is also carried in metadata), and a TABLE stays with
        the prose block immediately before it when they fit together — so a
        title/intro is never stranded from its table/topic.
      - Overlap (tail of the previous PROSE chunk) is prepended to the next prose
        chunk, but never around tables.
    """
    chunks: list[str] = []
    cur: list[_Block] = []
    cur_tok = 0
    seed = ""
    prev_table = False

    def emit() -> None:
        nonlocal cur, cur_tok, seed, prev_table
        if not cur:
            return
        text = "\n\n".join(b.text for b in cur).strip()
        is_table_chunk = all(b.is_table for b in cur)
        if seed and not is_table_chunk and not prev_table:
            text = f"{seed}\n\n{text}"
        chunks.append(text)
        prev_table = is_table_chunk
        seed = "" if is_table_chunk else _tail(text, overlap)
        cur, cur_tok = [], 0

    i = 0
    n = len(blocks)
    while i < n:
        b = blocks[i]
        bt = count_tokens(b.text)

        # KEEP-TOGETHER: a table stays with the prose block right before it when
        # the pair fits in the target — so the intro sentence and its table land
        # in the SAME chunk (table not stranded from its topic).
        if (
            b.is_table
            and cur
            and not cur[-1].is_table
            and cur_tok + bt <= hard_max
        ):
            cur.append(b)
            cur_tok += bt
            i += 1
            continue

        # A single block bigger than the target: flush what we have, then emit
        # the block whole (overflow) / sentence-split only if beyond the ceiling.
        if bt > target:
            emit()
            for piece in _emit_oversize_block(b, target=target, hard_max=hard_max):
                cur = [_Block(piece, b.is_table)]
                cur_tok = count_tokens(piece)
                emit()
            i += 1
            continue

        # Normal packing: would adding this block exceed the target? If so, close
        # the current chunk at the block boundary FIRST (never mid-block).
        if cur and cur_tok + bt > target:
            emit()
        cur.append(b)
        cur_tok += bt
        i += 1

    emit()
    return [c for c in chunks if c.strip()]


def chunk_document(
    md_text: str,
    *,
    target_tokens: int,
    overlap_tokens: int,
    hard_max_tokens: int,
    source_doc: str,
    source: str,
    tenant_id: str,
    version: str,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for heading, body in _sections(md_text):
        for piece in _pack(
            _blocks(body), target=target_tokens, overlap=overlap_tokens, hard_max=hard_max_tokens
        ):
            meta = {
                "heading": heading,
                "text": piece,
                "section": heading,
                "topic": classify_topic(heading, piece),
                "source_doc": source_doc,
                "source": source,
                "tenant_id": tenant_id,
                "version": version,
                "tokens": count_tokens(piece),
                **sensitivity_flags(heading, piece),
            }
            chunks.append(Chunk(heading=heading, text=piece, meta=meta))
    return chunks
