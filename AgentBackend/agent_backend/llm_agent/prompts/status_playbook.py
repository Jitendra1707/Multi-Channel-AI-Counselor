"""Status playbook — the brain's per-status opening-line hint.

Same function, status-aware output: the brain reads the lead's status off
LEAD PROFILE and uses the matching hint here as a steer for how to open the
turn. Not a script — just one sentence of intent the LLM frames in its own
voice.

Edit a status's hint here without touching the slot composer.
"""
from __future__ import annotations

from agent_backend.data.leads import LeadStatus


# Each hint is ONE sentence of intent for the first turn — the LLM frames it in
# its own voice. This dict covers the OPERATIONAL status axis only; the admissions
# lifecycle (application_*) lives in funnel_playbook.py and lead temperature
# (hot/warm/cold) is rendered separately as the lead's priority. They encode the
# SNU Counsellor Call Handbook scenarios:
#   RAW                → "raw data" inbound (Scenario 2 tone, we already have them)
#   CALLED/FOLLOWUP    → returning-contact nurture (Handbook §7.3 cadence)
# The CARE method (Connect → Ask → Recommend → Enable) underlies them all.
_STATUS_OPENING_HINT: dict[LeadStatus, str] = {
    LeadStatus.NEW: (
        "First contact. Greet by name, then ask their name back to confirm and one short "
        "open question about their goal. (Any call-recording disclosure is handled by the "
        "voice OUTPUT STYLE — never mention recording on text channels.)"
    ),
    LeadStatus.RAW: (
        "This is an INBOUND contact whose details we ALREADY have — do NOT ask for their name "
        "or basic details again, and don't make them repeat themselves. Acknowledge them warmly "
        "and get straight to what they're calling about; have a natural counselling "
        "conversation. If they show genuine interest in a programme, treat them as a real lead "
        "and move toward the next concrete step."
    ),
    LeadStatus.WELCOMED: (
        "They've messaged on WhatsApp before but haven't talked yet. Acknowledge that prior "
        "contact briefly and pick up from there."
    ),
    LeadStatus.SCHEDULING: (
        "Confirming or adjusting a slot. Reconfirm the time, ask if anything's changed, then "
        "let them know what to expect on the call."
    ),
    LeadStatus.SCHEDULED: (
        "This is the scheduled call. Dive into their goals and the relevant programme. "
        "(Any call-recording disclosure is handled by the voice OUTPUT STYLE — never "
        "mention recording on text channels.)"
    ),
    LeadStatus.IN_CALL: (
        "Mid-conversation; no greeting needed. Just continue naturally."
    ),
    LeadStatus.CALLED: (
        "You have ALREADY been in touch with this candidate before — this is a follow-up, NOT "
        "first contact. Do not re-introduce yourself or re-ask the basics; briefly acknowledge "
        "you've spoken before and carry the conversation forward from where it left off "
        "(reference the last session summary / known details in LEAD PROFILE if present)."
    ),
    LeadStatus.FOLLOWUP: (
        "Light-touch nurture call. Don't push; ask if anything's changed and answer their "
        "questions briefly. Only suggest next steps if they show interest."
    ),
    LeadStatus.DELEGATED: (
        "A human counsellor has been looped in for this candidate. Be helpful and continue "
        "naturally; reinforce that a senior counsellor will support them, and don't re-do the "
        "handoff."
    ),
    LeadStatus.NOT_INTERESTED: (
        "They previously showed no interest. Lead with empathy and curiosity — ask what's "
        "changed; do NOT pitch unless they open the door."
    ),
    LeadStatus.CONVERTED: (
        "They've already accepted a seat. Likely an onboarding / logistics question — be helpful "
        "and route them to the right team if it's not your specialty."
    ),
    LeadStatus.LOST: (
        "Re-engagement attempt. Lead with empathy. Ask what changed; don't pitch unless they "
        "open the door."
    ),
    LeadStatus.CLOSED: (
        "Conversation closed previously. Acknowledge that and ask why they're reaching out now."
    ),
}


def opening_hint(status: LeadStatus | str | None) -> str:
    """Return the one-line STATUS opening hint (operational/quality axis only).

    The admissions-LIFECYCLE guidance lives separately in funnel_playbook.py and
    is rendered alongside this one (LEAD PROFILE shows BOTH). Tolerates raw
    strings (status loaded from JSON) and None.
    """
    if status is None:
        return _STATUS_OPENING_HINT[LeadStatus.NEW]
    if isinstance(status, str):
        try:
            status = LeadStatus(status)
        except ValueError:
            return _STATUS_OPENING_HINT[LeadStatus.NEW]
    return _STATUS_OPENING_HINT.get(status, _STATUS_OPENING_HINT[LeadStatus.NEW])
