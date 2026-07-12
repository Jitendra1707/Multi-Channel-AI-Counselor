"""Meeting → BusinessLayer analysis handoff (backend side).

When a meeting ends, the runner hands the DIARISED transcript here. Unlike the
voice/whatsapp close path (which pulls the agent's own conversation buffer from
the in-RAM ConversationStore), the meeting transcript is mostly human↔human
speech the bridge captured with speaker tags — so we send it EXPLICITLY rather
than relying on the conversation buffer (which only holds the agent's turns).

We reuse the BusinessLayer's existing `POST /sessions/{id}/close` contract: it
stores the transcript and queues the session for analysis. The transcript turns
carry the speaker role in `role`:

    candidate / counsellor / agent   (mapped from the bridge's labels)

The BusinessLayer analyzer recognises `channel == "meeting"` and runs the DUAL
rubric (candidate + counsellor) over these speaker-tagged turns. Everything is
best-effort + no-op when BUSINESS_LAYER_URL is unset (same contract as the other
channels) — a meeting still works end-to-end without the BusinessLayer; it just
won't be analysed.
"""

from __future__ import annotations

from agent_backend.infra import get_logger
from agent_backend.integrations import business as biz
from agent_backend.llm_agent.session import Session

log = get_logger(__name__)


def _to_turns(transcript: list[tuple[str, str]]) -> list[dict[str, str]]:
    """Map the bridge's (role, text) lines to the BusinessLayer turn wire shape.

    Roles are preserved verbatim (candidate / counsellor / agent) so the dual
    analyzer can tell the two humans apart AND distinguish the agent's
    contributions. Empty lines are dropped."""
    out: list[dict[str, str]] = []
    for role, text in transcript or []:
        t = (text or "").strip()
        if not t:
            continue
        out.append({"role": (role or "candidate").strip().lower(), "text": t})
    return out


async def submit_meeting_for_analysis(
    *,
    session: Session,
    room: str,
    transcript: list[tuple[str, str]],
    candidate_lead_id: str | None,
    end_reason: str,
) -> None:
    """Push the diarised meeting transcript to the BusinessLayer for dual
    analysis. Best-effort; never raises into teardown."""
    turns = _to_turns(transcript)
    if not turns:
        log.info("[meeting] no transcript to analyze", room=room)
        return

    if not biz._enabled():  # type: ignore[attr-defined]
        log.info(
            "[meeting] BusinessLayer not configured — skipping analysis "
            "(set BUSINESS_LAYER_URL to enable)",
            room=room, turns=len(turns),
        )
        return

    # Open the session first if it wasn't (idempotent on the BusinessLayer side),
    # then close it WITH the explicit diarised transcript so the analyzer reads
    # the full human↔human conversation, not just the agent's turns.
    try:
        await biz.open_session(session, direction="meeting")
    except Exception as e:  # noqa: BLE001
        log.debug("[meeting] open_session before close failed", err=str(e))

    payload = {
        "end_reason": end_reason,
        "transcript": turns,
        "lead_id": candidate_lead_id,
        "channel": "meeting",
        "direction": "meeting",
    }
    try:
        # Use the BusinessLayer client's low-level POST so we control the exact
        # transcript (the high-level close_session would overwrite it with the
        # agent-only conversation buffer).
        await biz._post(  # type: ignore[attr-defined]
            f"/sessions/{session.conversation_id}/close", payload
        )
        log.info(
            "[meeting] submitted for dual analysis",
            room=room, turns=len(turns), lead_id=candidate_lead_id, end_reason=end_reason,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("[meeting] analysis submit failed", room=room, err=str(e))


__all__ = ["submit_meeting_for_analysis"]
