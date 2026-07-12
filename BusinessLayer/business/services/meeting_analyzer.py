"""Dual meeting analyzer — judges BOTH the candidate AND the counsellor.

The standard `analyzer.py` reads a transcript between the AI counsellor ("Aisha")
and a candidate and produces a single candidate-centric analysis. A *meeting* is
different: a real HUMAN counsellor guides the candidate while the AI agent sits
in and occasionally helps. So a meeting needs TWO rubrics:

  1. CANDIDATE — same shape the normal analyzer produces (interest / fit /
     concerns / facts / next-best-action), so the candidate's lead record keeps
     updating exactly as it does after a call. We REUSE the normal analyzer for
     this half by feeding it a candidate-only view of the transcript.

  2. COUNSELLOR — a performance evaluation of the human counsellor: clarity,
     coverage of the candidate's concerns, factual accuracy, objection handling,
     talk-ratio / listening, and next-step quality. This is NEW and meeting-only.

The transcript arrives speaker-tagged (role ∈ {candidate, counsellor, agent}),
which makes both halves possible. The combined result is stored on the session's
`analysis` JSON: the candidate half is applied to the lead (via the normal merge)
and the counsellor half is kept under `analysis["counsellor"]` for the report.
"""

from __future__ import annotations

import json
from typing import Any

from business.config import get_settings
from business.logging import get_logger
from business.models import Session

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Counsellor-evaluation rubric
# ---------------------------------------------------------------------------
_COUNSELLOR_SYSTEM_PROMPT = """\
You are a senior admissions-team coach reviewing how a HUMAN counsellor handled a
live counselling meeting with a prospective student. An AI assistant ("agent")
was also present and occasionally answered factual questions when asked — judge
ONLY the human counsellor, not the agent or the candidate.

You receive the full meeting transcript, speaker-tagged as [COUNSELLOR],
[CANDIDATE], and [AGENT]. Ground every judgement in what was actually said.

Return STRICT JSON with exactly these keys:
{
  "overall_score": integer,        // 0-100, holistic counselling quality
  "clarity": integer,              // 0-100, how clearly the counsellor explained things
  "concern_coverage": integer,     // 0-100, did they surface AND address the candidate's concerns
  "accuracy": integer,             // 0-100, factual correctness (flag anything that contradicts the agent's stated facts)
  "objection_handling": integer,   // 0-100, how well they handled hesitation/objections
  "listening": integer,            // 0-100, did they listen vs monologue (consider talk balance)
  "next_step_quality": integer,    // 0-100, was a concrete, appropriate next step set
  "talk_ratio_counsellor": number, // 0.0-1.0, rough share of words spoken BY the counsellor
  "strengths": [string],           // 2-4 specific things the counsellor did well (quote/paraphrase)
  "improvements": [string],        // 2-4 specific, actionable coaching points
  "missed_questions": [string],    // candidate questions/concerns left unanswered or dodged
  "summary": string                // 3-5 sentences: how the counsellor performed overall
}

Rules:
- Output ONLY the JSON object — no prose, no markdown fences.
- Be specific and fair: cite concrete moments, don't give generic praise.
- If the counsellor stated something that contradicts a fact the AGENT provided,
  call it out in "improvements" and lower "accuracy".
- Judge the HUMAN counsellor only.
"""


def _tagged_transcript(transcript: list[dict[str, Any]]) -> str:
    """Render the speaker-tagged transcript for the counsellor rubric."""
    lines: list[str] = []
    for t in transcript or []:
        role = (t.get("role") or "").lower()
        tag = {
            "counsellor": "COUNSELLOR",
            "candidate": "CANDIDATE",
            "agent": "AGENT",
            # tolerate the generic labels too
            "user": "CANDIDATE",
            "bot": "AGENT",
        }.get(role, role.upper() or "SPEAKER")
        text = (t.get("text") or "").strip()
        if text:
            lines.append(f"[{tag}] {text}")
    return "\n".join(lines)


def _candidate_view(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project the meeting transcript into the {user, bot} shape the standard
    candidate analyzer expects: the CANDIDATE becomes 'user', and BOTH the human
    counsellor and the AI agent become 'bot' (from the candidate's perspective,
    both are "the institution speaking"). This lets us reuse the proven candidate
    rubric verbatim so the lead record updates identically to a normal call."""
    out: list[dict[str, Any]] = []
    for t in transcript or []:
        role = (t.get("role") or "").lower()
        text = (t.get("text") or "").strip()
        if not text:
            continue
        if role in ("candidate", "user"):
            out.append({"role": "user", "text": text})
        else:  # counsellor / agent / bot → the institution side
            out.append({"role": "bot", "text": text})
    return out


async def _call_counsellor_llm(transcript_text: str) -> dict[str, Any] | None:
    """One LLM pass for the counsellor evaluation. Reuses the standard analyzer's
    model-adaptive call so it works across OpenAI model families."""
    from business.services.analyzer import _create_adaptive  # reuse the adapter

    s = get_settings()
    if not s.llm_api_key:
        return None
    try:
        from openai import AsyncOpenAI
    except ModuleNotFoundError:
        log.error("openai SDK not installed — counsellor analysis disabled")
        return None

    client = AsyncOpenAI(api_key=s.llm_api_key, base_url=s.llm_api_url)
    messages = [
        {"role": "system", "content": _COUNSELLOR_SYSTEM_PROMPT},
        {"role": "user", "content": f"MEETING TRANSCRIPT:\n{transcript_text}\n"},
    ]
    try:
        resp = await _create_adaptive(client, messages, max_out=s.analyzer_max_tokens)
    except Exception as e:  # noqa: BLE001
        log.warning("counsellor analysis LLM call failed", err=str(e)[:200])
        return None
    finally:
        await client.close()

    content = (resp.choices[0].message.content or "").strip() if resp and resp.choices else ""
    if not content:
        return None
    try:
        raw = json.loads(content)
    except json.JSONDecodeError:
        log.warning("counsellor analysis returned non-JSON", sample=content[:200])
        return None
    return raw if isinstance(raw, dict) else None


async def analyze_meeting(sess: Session) -> bool:
    """Analyze a meeting session with BOTH rubrics.

    1. Candidate half — reuse `analyzer.analyze_session` on a candidate-view of
       the transcript so the lead record updates exactly as after a call.
    2. Counsellor half — run the counsellor rubric and fold the result into the
       session's analysis JSON under "counsellor".

    Returns True if at least one half was applied.
    """
    from business.services.analyzer import analyze_session as analyze_candidate
    from business.store import get_store

    store = get_store()
    full_transcript = sess.transcript or []
    if not _tagged_transcript(full_transcript).strip():
        await store.apply_session_analysis(session_id=sess.session_id, analysis={"summary": None})
        return False

    # --- 1. Candidate half (reuse the proven path) -----------------------
    # Swap the session's transcript to the candidate-view for the standard
    # analyzer, then restore. analyze_candidate applies the lead merge + emits
    # action tasks, identical to a normal call.
    #
    # RECURSION GUARD: analyze_candidate IS analyzer.analyze_session, which routes
    # `channel == "meeting"` BACK here. Temporarily present the session as a
    # "voice" channel for the candidate half so it takes the call rubric, then
    # restore the real channel. (The candidate-view transcript is already in the
    # {user, bot} shape that rubric expects.)
    original = sess.transcript
    original_channel = sess.channel
    candidate_applied = False
    try:
        sess.transcript = _candidate_view(full_transcript)
        sess.channel = "voice"
        candidate_applied = await analyze_candidate(sess)
    except Exception as e:  # noqa: BLE001
        log.warning("meeting candidate-half analysis failed", session_id=sess.session_id, err=str(e)[:200])
    finally:
        sess.transcript = original
        sess.channel = original_channel

    # --- 2. Counsellor half (new) ----------------------------------------
    counsellor = await _call_counsellor_llm(_tagged_transcript(full_transcript))
    if counsellor is not None:
        # Re-read the (possibly candidate-updated) session and fold the
        # counsellor evaluation into its analysis JSON without clobbering the
        # candidate half.
        try:
            fresh = await store.get_session(sess.session_id)
            merged = dict(fresh.analysis or {}) if fresh else {}
            merged["counsellor"] = counsellor
            await store.apply_session_analysis(session_id=sess.session_id, analysis=merged)
            log.info(
                "meeting counsellor analyzed",
                session_id=sess.session_id,
                overall=counsellor.get("overall_score"),
                improvements=len(counsellor.get("improvements") or []),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("storing counsellor analysis failed", session_id=sess.session_id, err=str(e)[:200])

    return bool(candidate_applied or counsellor is not None)


__all__ = ["analyze_meeting"]
