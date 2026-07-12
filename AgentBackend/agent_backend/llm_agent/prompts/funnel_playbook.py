"""Funnel-stage playbook — the brain's per-LIFECYCLE-stage guidance.

SEPARATE from status_playbook.py on purpose. Two independent axes guide a call:

  • status        (status_playbook.py) — operational / quality: new, called,
                  followup, cold/warm/hot. "How the campaign sees them."
  • funnel_stage  (THIS file)          — admissions LIFECYCLE: raw, lead,
                  application_started, fees_pending, application_submitted.
                  "Where they are in the admissions journey."

Both are shown to the brain (LEAD PROFILE renders BOTH hints as two lines); the
LLM blends them — e.g. status 'called' says "you've spoken before, carry forward"
while funnel_stage 'fees_pending' says "their application's done, guide payment".

Each hint is ONE sentence of intent — the LLM frames it in its own voice. They
encode the SNU Counsellor Call Handbook scenarios:
  raw                   → inbound contact we already have data for (don't re-ask)
  application_started   → Scenario 3/4 (help them finish)
  fees_pending          → Scenario 5 (guide the payment)
  application_submitted → Scenario 6 (reassure, keep warm)
'lead' has no dedicated hint (a plain qualified lead opens by its status tier).

Edit a stage's guidance here without touching status_playbook or the composer.
"""
from __future__ import annotations


# Keys are the FunnelStage string values (mirror BusinessLayer FunnelStage).
_FUNNEL_STAGE_HINT: dict[str, str] = {
    "raw": (
        "This is an INBOUND contact whose details we ALREADY have — do NOT ask for their name "
        "or basic details again, and don't make them repeat themselves. Acknowledge them warmly "
        "and get straight to what they're calling about; have a natural counselling "
        "conversation. If they show genuine interest in a programme, treat them as a real lead "
        "and move toward the next concrete step."
    ),
    "application_started": (
        "Their application is started but NOT yet complete. Open by gently and warmly "
        "acknowledging exactly that — e.g. 'I can see you've started your application but haven't "
        "finished it yet.' Then ASK, with genuine curiosity and zero pressure, whether they ran "
        "into any issue or need any information — a confusing field, a document, deciding a branch. "
        "Lead with HELP, not a push to finish now: your job here is to clear whatever's holding "
        "them back and answer their questions. Don't re-ask qualifying questions. Only move toward "
        "completing it if THEY're comfortable; if a document is the blocker, reassure that phone "
        "photos are fine and the branch can be reviewed later. Patient and supportive, not salesy."
    ),
    "fees_pending": (
        "Their application is complete but the fee is NOT yet paid. Open by gently and warmly "
        "acknowledging exactly that — e.g. 'I can see you've completed your application, just the "
        "fee payment is still pending.' Then ASK, with zero pressure, whether they're facing any "
        "issue with the payment or have any questions about it (the process, payment options, "
        "scholarships, or who's paying). Lead with HELP — offer to sort out whatever's blocking it "
        "and explain anything unclear — rather than pushing them to pay right now. Don't re-ask "
        "qualifying questions. Only guide them through paying if they're ready, and offer to "
        "involve a parent if a parent handles the fee. Patient and supportive, not salesy."
    ),
    "application_submitted": (
        "Application is submitted and under review (Scenario 6). This is a reassurance/keep-warm "
        "touch — proactively update them, set expectations on the review timeline, and make the "
        "wait useful (placement report, campus visit). Engage honestly if they mention other "
        "colleges; don't go silent on them."
    ),
}


def funnel_hint(funnel_stage: str | None) -> str | None:
    """The lifecycle guidance for a funnel stage, or None when there's no
    dedicated hint (stage 'lead', blank, or unknown) — caller then relies on the
    status hint alone. Tolerates raw strings and None."""
    fs = (funnel_stage or "").strip().lower()
    return _FUNNEL_STAGE_HINT.get(fs)


__all__ = ["funnel_hint"]
