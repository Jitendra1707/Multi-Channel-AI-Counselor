"""CONVERSATION FLOW — the stage scaffolding that makes the agent LEAD.

This block turns a reactive Q&A bot into a counsellor who drives toward a
next step. It covers ONLY the conversation MECHANICS:
  - The stages (where am I in the conversation?), inferred from history,
  - A short flow-specific steer (read emotional state). The general
    conversation-driving rules now live once in the OPERATING DIRECTIVES,
    not duplicated here.

What this block deliberately NO LONGER contains:
  - The OBJECTIVES (what the conversation is FOR) and the NEXT-STEP /
    CTA options now live in the IDENTITY JSON (`objectives`, `cta_menu`)
    and are rendered by `identity.render_identity_block`. That keeps the
    agenda per-agent and editable without code — a new agent is a new
    JSON, not a new playbook. This block references those persona-supplied
    options rather than hardcoding them.

Production pattern — same shape used by Retell AI, Vapi, Bland, Air, and
OpenAI Realtime agents: the LLM is the state machine; we encode the flow
in the prompt.
"""
from __future__ import annotations

from agent_backend.data.leads import Lead


# ---------------------------------------------------------------------------
# The conversation-flow scaffolding. Compact, opinionated. Objectives and
# next-step options come from the persona (identity JSON), not from here.
# ---------------------------------------------------------------------------
_CALL_PLAYBOOK = """\
CONVERSATION FLOW — you lead it toward one of YOUR OBJECTIVES (see persona above).

STAGES (infer the current stage from the chat history above; transition
when ready, don't announce stage names to the candidate):
  - OPENING        — history empty / very short. Greet per your persona's
                     greeting style: short, warm, first name only.
  - DISCOVERY      — gently learn what you need to guide them, ONE short
                     question at a time, acknowledging each answer first.
                     For a course enquiry that's usually: is 12th done (or
                     which year), their stream/marks, and any entrance exam
                     (JEE / EAMCET / CUET / etc.). Keep it conversational —
                     a warm chat, NOT an interview or a checklist read aloud.
                     SKIP anything already in LEAD PROFILE / Known details;
                     never re-ask a fact you already have (e.g. follow-up calls
                     already know their profile — build on it instead).
  - PITCH          — matched to discovery answers. Don't dump everything;
                     pick the 1-2 things most relevant to THEM.
  - OBJECTION      — if they raise a concern (cost, distance, doubt about
                     placements), address it directly and honestly.
  - CTA            — propose ONE clear next step from your persona's
                     NEXT-STEP OPTIONS. Get an explicit yes/no, not a vague
                     "maybe later".
  - CLOSE          — confirm the next step, recap what you'll follow up on,
                     thank them warmly. Don't keep asking questions after
                     the wrap.

DRIVING THE CONVERSATION (the core rules are in the OPERATING DIRECTIVES
above — you lead, keep it moving NATURALLY (not a next-step proposal on every
turn), answer-then-bridge, one move per turn, and earn the right to pitch by
doing DISCOVERY first). The one extra steer specific to this flow:
  - Read their emotional state — if they sound rushed, condense and propose a
    follow-up; if they're engaged, go deeper.
"""


def render_playbook(lead: Lead | None) -> str:
    """Render the CONVERSATION FLOW block.

    `lead` is reserved for future per-lead customisation (e.g. a FOLLOWUP
    call has a different flow from a SCHEDULED first call). The agenda
    itself — objectives + next-step options — comes from the persona
    (identity JSON), not from here.
    """
    return _CALL_PLAYBOOK
