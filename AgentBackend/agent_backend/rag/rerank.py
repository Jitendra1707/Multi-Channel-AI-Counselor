"""Optional LLM reranker — a cross-encoder-style second pass.

Given the fused shortlist, ask a small fast model to order candidates by true
relevance to the query. This is the precision lever for overlapping topics
(e.g. choosing between the CSE-core and CSE-SAP scholarship tables). It costs
one extra model call (~300-600 ms), so it's OFF by default; enable per-call or
via RAG_RERANK=1.

Defensive: any error / unpar. output falls back to the input order, so turning
the reranker on can never make retrieval *fail*, only (rarely) not improve it.
"""
from __future__ import annotations

import json

from agent_backend.rag import settings as rag_settings


def rerank(query: str, candidates: list[str], *, top_k: int, model: str | None = None) -> list[int]:
    """Return candidate indices reordered best→worst (length <= top_k).

    `candidates[i]` is the rendered text of the i-th shortlist entry.
    """
    if not candidates:
        return []
    model = model or rag_settings.RERANK_MODEL
    numbered = "\n\n".join(f"[{i}]\n{c[:800]}" for i, c in enumerate(candidates))
    prompt = (
        "You are ranking knowledge-base passages by how well each ANSWERS the "
        "user's question. Return ONLY a JSON array of passage indices, best "
        f"first, no prose. Question:\n{query}\n\nPassages:\n{numbered}\n\n"
        "JSON array of indices (most relevant first):"
    )
    try:
        from openai import OpenAI

        api_key, base_url = rag_settings.openai_credentials()
        client = OpenAI(api_key=api_key, base_url=base_url)
        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = (resp.choices[0].message.content or "").strip()
        start, end = raw.find("["), raw.rfind("]")
        order = json.loads(raw[start : end + 1]) if start >= 0 and end > start else []
        seen, out = set(), []
        for i in order:
            if isinstance(i, int) and 0 <= i < len(candidates) and i not in seen:
                seen.add(i)
                out.append(i)
        # Append any candidates the model omitted, preserving input order.
        out.extend(i for i in range(len(candidates)) if i not in seen)
        return out[:top_k]
    except Exception:
        return list(range(min(top_k, len(candidates))))
