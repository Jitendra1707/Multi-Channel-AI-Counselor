"""Post-call analyzer — ONE LLM pass per finished conversation.

Polls for ended, un-analyzed sessions. For each it runs a single LLM call over
the transcript producing analysis + extracted facts in one shot, folds the
result into the lead (idempotent merge), and emits follow-up tasks. No per-turn
LLM calls anywhere — extraction happens here, once.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from business.config import get_settings
from business.logging import get_logger
from business.models import Session
from business.store import get_store

log = get_logger(__name__)

# India is a fixed UTC+5:30 (no DST), so a fixed offset is exact and needs no
# tzdata. "Now" for every date computation the analyzer does is IST.
_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist() -> datetime:
    return datetime.now(timezone.utc).astimezone(_IST)


def _resolve_visit_when(payload: dict[str, Any], now_ist: datetime) -> tuple[str, str] | None:
    """Turn the analyzer's emitted campus-visit date/time into clean, concrete
    display strings, anchored to IST 'now'.

    The model emits `visit_date` as an ISO date (YYYY-MM-DD) computed from the
    CURRENT DATE & TIME (IST) it's given, plus `visit_time` (e.g. "5:00 PM").
    We parse the ISO deterministically (never trust the model's *formatting*),
    correct an obviously-past date (a weekday like "Thursday" mis-resolved to
    last week → roll forward), and return (display_date, display_time).
    Returns None when there's no usable date (caller then skips the task)."""
    raw_date = str(payload.get("visit_date") or "").strip()
    raw_time = str(payload.get("visit_time") or "").strip()
    if not raw_date:
        return None
    try:
        d = date.fromisoformat(raw_date[:10])  # tolerate a datetime slipping in
    except ValueError:
        # Model didn't emit ISO — fall back to its raw strings so the visit isn't
        # lost (email shows whatever it wrote, e.g. "Thursday, 19 June").
        return raw_date, (raw_time or "the agreed time")
    today = now_ist.date()
    if d < today:
        # Almost certainly a weekday resolved to the past week — roll forward.
        d = d + timedelta(days=(((today - d).days // 7) + 1) * 7)
    return d.strftime("%A, %d %B %Y"), (raw_time or "the agreed time")

_SYSTEM_PROMPT = """\
You are a senior admissions-conversation analyst for a university admissions team.
You read ONE conversation transcript between an AI counsellor ("Aisha"/"bot") and a
prospective student ("candidate"/"user"), PLUS the candidate's prior context (known
facts, a running cumulative summary, and a list of materials ALREADY SENT). You
return ONE JSON object that (a) captures THIS conversation, (b) keeps the candidate's
journey up to date, and (c) lists EVERY follow-up action the conversation calls for.

Ground everything strictly in the transcript + provided context. Never invent a fact,
a name, a date, a document, or a commitment. If something wasn't stated, omit it.

OUTPUT — return STRICT JSON with EXACTLY these keys (no extra keys, no prose, no
markdown fences):
{
  "full_name": string|null,   // candidate's own name ONLY if they state it this call; else null. Never guess.
  "session_summary": string,  // 4-6 sentences on THIS call: situation, what they asked, concerns,
                              //   what was pitched, anything promised/agreed, sentiment, how it ended.
  "cumulative_summary": string, // 4-8 sentences: UPDATE the prior cumulative summary by folding in this
                              //   call; never drop earlier details. Written for the next counsellor:
                              //   who they are + academic profile, history across calls, what's been
                              //   sent, current stage, where things stand.
  "interest":   integer,      // 0-100 overall interest — use the INTEREST RUBRIC.
  "confidence": integer,      // 0-100 confidence in your read (low for short/garbled transcripts).
  "sentiment":  string,       // positive | neutral | negative | frustrated
  "status":     string,       // proposed next status — use the STATUS rule + DECISION PLAYBOOK:
                              //   cold | warm | hot | application_started |
                              //   application_completed_payment_pending | application_submitted |
                              //   not_interested | converted | lost
                              //   (the interest TIER cold/warm/hot is derived from your
                              //    `interest` score automatically — you only need to OVERRIDE
                              //    with an application_* stage, or a terminal not_interested/
                              //    converted/lost, when the transcript clearly shows it).
  "open_concerns":     [string],  // worries still unresolved after this call
  "resolved_concerns": [string],  // concerns addressed/closed during this call
  "next_best_action":  string,    // ONE human-readable recommendation for the next contact,
                                  //   aware of what's already been sent (follow up, don't re-send).
  "facts": {                  // include ONLY keys actually stated; drop all others.
     "email": string,          // the candidate's email — ONLY if they SPELL/STATE it this call. Never guess.
     "marks_10th_pct": number, "marks_12th_pct": number, "board": string, "stream": string,
     "entrance_exam": string, "entrance_score": string, "course_interest": string,
     "intake_year": integer, "budget_lakhs": number, "scholarship_needed": boolean,
     "location": string, "parent_involved": boolean, "decision_timeline": string
  },
  "actions": [                // a LIST — emit EVERY applicable action (see CHECKLIST). [] is valid.
     {
       "type": "send_brochure" | "schedule_followup" | "callback" | "escalate_counsellor" | "schedule_campus_visit",
       "payload": {
         "doc": string,        // send_brochure: ONE short, consistent material label (see DOCUMENT RULES)
         "body": string,       // send_brochure: short WhatsApp caption for THIS document
         "in_minutes": integer,// schedule_followup/callback: minutes from the CURRENT DATE & TIME until next contact
         "reason": string,     // escalate_counsellor: 1-2 sentences on WHY a human must take over
         "visit_date": string, // schedule_campus_visit: ISO YYYY-MM-DD resolved from CURRENT DATE & TIME (IST), today or later
         "visit_time": string  // schedule_campus_visit: clock time, e.g. "5:00 PM"
       }
     }
  ]
}

ACTIONS ARE A LIST — CAPTURE ALL OF THEM. A single call can legitimately produce
several actions of different types. Do NOT pick only the "main" one; emit one action
for EACH distinct commitment in the transcript.

NOTHING WAS ACTUALLY DONE DURING THE CALL. The counsellor is on a VOICE call and
CANNOT send a WhatsApp, book a visit, or complete any action while talking. So when the
bot says it "is sending" / "will send" / "is arranging" a document, or "has set" / "is
booking" / "set kar deti hoon" / "bhej rahi hoon" a visit or message, that is a PENDING
PROMISE — NOT something already done — and you MUST emit the matching action
(send_brochure / schedule_campus_visit / ...). The same applies whenever the candidate
ASKS for or AGREES to receive something or to visit. The ONLY things already completed
are those in the ALREADY SENT list; everything the bot merely SAID it would do in THIS
call still needs an action. If the bot promised it and you emit no action, the candidate
gets NOTHING — that is the worst outcome.

COMPLETENESS CHECKLIST — before you finish, re-scan the transcript for EACH of these
and add an action for every one that applies:
  [ ] Did the candidate ask for / agree to receive ANYTHING on WhatsApp — a document,
      the APPLICATION LINK, a FEE / SCHOLARSHIP BREAKDOWN, or a COURSE COMPARISON?
      → one send_brochure PER item (the application link and a comparison ARE sends too)
  [ ] Did they commit to a campus visit on a specific day/time?       → schedule_campus_visit
  [ ] Was a future call/contact time agreed, or are they "not ready"? → schedule_followup (or callback)
  [ ] Did they ask for a human, OR are they HOT and need a human to close? → escalate_counsellor
Missing a real commitment is the worst failure here — when unsure whether something
was agreed, INCLUDE the action rather than drop it (the system de-dupes and re-checks
consent before acting).

DOCUMENT RULES (send_brochure):
- send_brochure covers ANYTHING the bot promised to WhatsApp, not just PDFs — the
  application link, fee/scholarship details, or a programme brochure are all sends.
- "doc" MUST be one of these real, sendable items (reuse the SAME label every time so
  duplicates collapse to one task):
    • "application link"            — how to apply, the application form / apply link
    • "fee and scholarship details" — fees, scholarships, concessions. ONE item — do NOT
                                      split fees and scholarships into two separate sends.
    • "programme brochure"          — overview of programmes, fees, campus life and
                                      placements; use as the CATCH-ALL for a course list,
                                      a course comparison, or anything the two above
                                      don't cover.
  Emit a SEPARATE send_brochure action for each DISTINCT item the candidate asked for or
  agreed to receive (e.g. application link AND a programme brochure = two actions). "body"
  is the candidate-facing WhatsApp caption for that item.
- Do NOT re-send anything in ALREADY SENT — follow up on it via next_best_action instead.
- Only send_brochure when the candidate actually asked for / agreed to receive it.

INTEREST RUBRIC (be honest — counsellor time + lead TEMPERATURE depend on this; the
0-100 score maps to hot >=80 / warm 50-79 / cold 1-49):
- 80-100  HOT: wants to act now (apply / visit / pay / asked next steps), engaged, qualified.
- 50-79   WARM: clearly interested but something is pending (parents, exams, budget, timing).
- 1-49    COLD: curious-but-disengaged through to barely interested — vague, non-committal,
                deflecting, short answers, "maybe later", few/no questions back.
- 0       NONE: explicit "not interested", wrong number, asked not to be contacted
                (also set status "not_interested" / "lost").

DECISION PLAYBOOK — actions COMPOSE; apply EVERY case that matches:
1. HOT and ready to move, no future call agreed (needs a human to close)
   → escalate_counsellor, "reason" = what makes them hot (scores, budget fit, urgency,
     what they asked for).
2. Asks for a human (counsellor/senior/manager/"real person") OR raises something only
   staff can resolve (fee negotiation, admission decision, complaint)
   → escalate_counsellor, "reason" quotes their request. Applies at ANY interest level.
3. Interested but not ready ("call me tomorrow", "after I talk to my parents/exams")
   → schedule_followup with "in_minutes" from the CURRENT DATE & TIME:
       • specific time → minutes until that moment (morning≈10:00, afternoon≈15:00,
         evening≈18:00; never negative; if it lands in the past, use the next day).
       • no specific time → 1440 (next day).
     Use "callback" instead when they want to be called right back / shortly.
4. NOT interested (explicit no / stop calling / wrong person), no follow-up agreed
   → status "not_interested", NO followup/callback. Use "lost" only for definitive dead
     ends (enrolled elsewhere, asked never to contact again).
5. Committed to a campus visit on a specific day/time ("book me Thursday at 5")
   → schedule_campus_visit with visit_date = ISO date resolved against CURRENT DATE &
     TIME (IST) and visit_time = clock time. Only for a REAL commitment, not "maybe".
6. Compose freely. Example — a hot candidate who asked for the fee structure AND the
   scholarship details on WhatsApp, agreed to visit Saturday 11am, and asked for a
   senior counsellor → [send_brochure("fee structure"), send_brochure("scholarship
   details"), schedule_campus_visit(Sat 11:00), escalate_counsellor].

STATUS rule (single value; pick by PRECEDENCE when several apply):
  not_interested / lost  (case 4 — explicit no / dead end)
  > converted            (they accepted a seat)
  > application_submitted | application_completed_payment_pending |
    application_started   (ONLY if the transcript clearly shows the candidate is
                           at that application stage — they say they submitted /
                           paid / started, or are calling about it. This is read
                           as their admissions LIFECYCLE stage, not a call-status.)
  > hot | warm | cold    (the DEFAULT — the interest tier from your `interest`
                          score; leaving status blank lets it be derived. This sets
                          the lead's TEMPERATURE only — the operational call-status
                          and any human escalation are decided from your ACTIONS.)

EDGE CASES:
- Empty / near-empty / unintelligible transcript, voicemail, or no-answer → interest
  and confidence low (which yields cold / no tier), actions []. Add a schedule_followup
  if a retry makes sense.
- Wrong number / not the candidate → status "not_interested" (or "lost" if they ask to
  stop), actions [], facts {}.
- Abusive / asked to never be contacted → status "lost", actions [].

HARD RULES:
- Output ONLY the JSON object. No prose, no markdown, no trailing commentary.
- cumulative_summary UPDATES the prior summary; preserve earlier details.
- "facts" contains ONLY keys actually stated this/earlier; drop the rest.
- At MOST ONE escalate_counsellor. Do not escalate leads below ~60 interest unless they
  explicitly asked for a human (case 2).
- Do NOT pair escalate_counsellor with schedule_followup — once escalated, the human
  owns the next touch. (escalate_counsellor MAY co-exist with send_brochure and
  schedule_campus_visit.)
"""


def _transcript_to_text(transcript: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for t in transcript or []:
        role = (t.get("role") or "").lower()
        who = "Candidate" if role == "user" else ("Aisha" if role == "bot" else role or "system")
        text = (t.get("text") or "").strip()
        if text:
            lines.append(f"{who}: {text}")
    return "\n".join(lines)


def _coerce_analysis(raw: Any) -> dict[str, Any]:
    """Defensive parse of the model's JSON into the shape the store expects."""
    if not isinstance(raw, dict):
        return {}
    facts = raw.get("facts")
    actions = raw.get("actions")
    # Back-compat: accept legacy "summary" as the session summary if the model
    # didn't emit the newer keys.
    session_summary = raw.get("session_summary") or raw.get("summary") or None
    return {
        "full_name": (raw.get("full_name") or None),
        "session_summary": session_summary,
        "cumulative_summary": raw.get("cumulative_summary") or None,
        # keep "summary" populated for any older readers of the dict
        "summary": session_summary,
        "interest": raw.get("interest"),
        "confidence": raw.get("confidence"),
        "sentiment": raw.get("sentiment"),
        "status": raw.get("status"),
        "next_best_action": raw.get("next_best_action") or None,
        "open_concerns": raw.get("open_concerns") if isinstance(raw.get("open_concerns"), list) else [],
        "resolved_concerns": raw.get("resolved_concerns") if isinstance(raw.get("resolved_concerns"), list) else [],
        "facts": facts if isinstance(facts, dict) else {},
        "actions": actions if isinstance(actions, list) else [],
    }


async def _create_adaptive(client: Any, messages: list[dict], *, max_out: int) -> Any:
    """Call chat.completions, adapting to model-specific API quirks.

    Newer OpenAI models (o-series, gpt-5.x) renamed `max_tokens` →
    `max_completion_tokens` and reject a non-default `temperature`. Older models
    (gpt-4o-mini) take `max_tokens` and any temperature. Rather than hardcode for
    one family, we start with the modern params and, on a 400 that names an
    unsupported/renamed param, drop or swap it and retry — so the analyzer works
    no matter which model is configured.
    """
    # Start modern: max_completion_tokens, omit temperature (default=1 is the
    # only value reasoning models accept; 4o accepts it too).
    params: dict[str, Any] = {
        "model": get_settings().analyzer_llm_model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "max_completion_tokens": max_out,
    }
    for _ in range(4):
        try:
            return await client.chat.completions.create(**params)
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            # Renamed token param (model wants the OTHER name than what we sent).
            if "max_completion_tokens" in params and "max_tokens" in msg and "max_completion_tokens" not in msg:
                params["max_tokens"] = params.pop("max_completion_tokens")
                continue
            if "max_tokens" in params and "max_completion_tokens" in msg:
                params["max_completion_tokens"] = params.pop("max_tokens")
                continue
            # Unsupported temperature → drop it and retry with the default.
            if "temperature" in params and "temperature" in msg:
                params.pop("temperature", None)
                continue
            # Unsupported response_format (rare / non-OpenAI endpoints) → drop it;
            # the prompt already instructs JSON-only output.
            if "response_format" in params and "response_format" in msg:
                params.pop("response_format", None)
                continue
            raise
    # Exhausted adaptations.
    return await client.chat.completions.create(**params)


async def _call_llm(transcript_text: str, *, lead_context: dict[str, Any]) -> dict[str, Any] | None:
    s = get_settings()
    if not s.llm_api_key:
        return None
    try:
        from openai import AsyncOpenAI
    except ModuleNotFoundError:
        log.error("openai SDK not installed — analyzer disabled (pip install openai)")
        return None

    client = AsyncOpenAI(api_key=s.llm_api_key, base_url=s.llm_api_url)
    sent = lead_context.get("sent_items") or []
    sent_lines = (
        "\n".join(f"  - {x.get('item')} (via {x.get('channel')}, {x.get('at')})" for x in sent)
        if sent else "  (nothing sent yet)"
    )
    prior = (lead_context.get("previous_summary") or "").strip() or "(no prior conversations)"
    # IST (the candidate's timezone) so the model can resolve "Thursday 5pm" /
    # "tomorrow at 5" into both an in_minutes AND a concrete visit date.
    now_ist = _now_ist()
    now_line = now_ist.strftime("%A, %d %B %Y, %I:%M %p")
    user_msg = (
        f"CURRENT DATE & TIME (IST): {now_line}\n"
        f"(use this to compute any schedule_followup/callback 'in_minutes' AND to\n"
        f" resolve a campus-visit day/time into a concrete visit_date)\n\n"
        f"CANDIDATE NAME: {lead_context.get('full_name') or 'Unknown'}\n\n"
        f"KNOWN FACTS SO FAR (merge/extend, don't lose these):\n"
        f"{json.dumps(lead_context.get('facts') or {}, indent=2)}\n\n"
        f"CUMULATIVE SUMMARY OF PRIOR CONVERSATIONS (update this, don't drop details):\n"
        f"{prior}\n\n"
        f"ALREADY SENT TO THE CANDIDATE (do NOT re-send these — follow up instead):\n"
        f"{sent_lines}\n\n"
        f"TRANSCRIPT OF THE CONVERSATION TO ANALYZE:\n{transcript_text}\n"
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    try:
        resp = await _create_adaptive(client, messages, max_out=s.analyzer_max_tokens)
    except Exception as e:  # noqa: BLE001
        log.warning("analyzer LLM call failed", err=str(e)[:200])
        return None
    finally:
        await client.close()

    if resp is None:
        return None
    content = (resp.choices[0].message.content or "").strip() if resp.choices else ""
    if not content:
        return None
    try:
        return _coerce_analysis(json.loads(content))
    except json.JSONDecodeError:
        log.warning("analyzer LLM returned non-JSON", sample=content[:200])
        return None


async def analyze_session(sess: Session) -> bool:
    """Analyze one session end-to-end. Returns True if analysis was applied.

    Meeting sessions (channel == "meeting") carry a speaker-tagged transcript of
    a HUMAN counsellor + candidate (+ the AI agent) and need a DUAL rubric — the
    candidate half (lead update, as usual) AND a counsellor performance
    evaluation. They route to the meeting analyzer instead of the call rubric.
    The poller calls this for every claimed session regardless of channel, so the
    branch lives here (one place) rather than in the loop.
    """
    if (sess.channel or "").lower() == "meeting":
        from business.services.meeting_analyzer import analyze_meeting

        return await analyze_meeting(sess)

    store = get_store()
    transcript_text = _transcript_to_text(sess.transcript or [])
    if not transcript_text.strip():
        # Nothing to analyze — mark done so it isn't re-claimed forever.
        await store.apply_session_analysis(session_id=sess.session_id, analysis={"summary": None})
        return False

    lead = await store.get_lead(sess.lead_id)
    lead_context = {
        "facts": (lead.facts if lead else {}) or {},
        "full_name": lead.full_name if lead else None,
        "previous_summary": (lead.summary if lead else None),
        "sent_items": (lead.sent_items if lead else []) or [],
    }

    analysis = await _call_llm(transcript_text, lead_context=lead_context)
    if analysis is None:
        log.warning("analysis skipped (no result) — will retry next poll", session_id=sess.session_id)
        return False

    # Enforce the business rules in CODE (defence-in-depth over the prompt): clean
    # the action list (canonical doc labels, ≤1 escalation, escalate ⇒ no auto
    # follow-up) and derive the lead status from the SET of actions, so the fold
    # below is deterministic regardless of task execution order.
    _normalize_actions_and_status(analysis, session_id=sess.session_id)

    updated = await store.apply_session_analysis(session_id=sess.session_id, analysis=analysis)
    await _emit_action_tasks(sess, analysis)

    log.info(
        "session analyzed",
        session_id=sess.session_id,
        lead_id=sess.lead_id,
        interest=analysis.get("interest"),
        status=(updated.status if updated else None),
        # When the next dial is scheduled for. NULL here on a dialable lead means
        # no follow-up time was set by the fold — watch this to confirm whether
        # the redial loop is fixed (a just-called lead should show a future ts,
        # not None). The schedule_followup action may refine it moments later.
        next_action_at=(updated.next_action_at.isoformat() if updated and updated.next_action_at else None),
        actions=len(analysis.get("actions") or []),
        action_types=[a.get("type") for a in (analysis.get("actions") or []) if isinstance(a, dict)],
        facts=list((analysis.get("facts") or {}).keys()),
    )
    return True


# The LLM is asked for these canonical action types, but gpt-class models drift
# (send_whatsapp / send_document / follow_up / call_back …). Map the common
# variants onto the three we execute so a mislabelled action isn't silently lost.
_ACTION_TYPE_ALIASES = {
    "send_brochure": "send_brochure",
    "send_document": "send_brochure",
    "send_doc": "send_brochure",
    "send_docs": "send_brochure",
    "send_whatsapp": "send_brochure",
    "send_whatsapp_message": "send_brochure",
    "send_message": "send_brochure",
    "send_material": "send_brochure",
    "send_materials": "send_brochure",
    "send_details": "send_brochure",
    "send_info": "send_brochure",
    "share_document": "send_brochure",
    "brochure": "send_brochure",
    "whatsapp": "send_brochure",
    "schedule_followup": "schedule_followup",
    "schedule_follow_up": "schedule_followup",
    "followup": "schedule_followup",
    "follow_up": "schedule_followup",
    "callback": "callback",
    "call_back": "callback",
    "call": "callback",
    "escalate_counsellor": "escalate_counsellor",
    "escalate_counselor": "escalate_counsellor",
    "escalate_to_counsellor": "escalate_counsellor",
    "escalate_to_counselor": "escalate_counsellor",
    "escalate": "escalate_counsellor",
    "escalation": "escalate_counsellor",
    "counsellor": "escalate_counsellor",
    "counselor": "escalate_counsellor",
    "human_handoff": "escalate_counsellor",
    "handoff": "escalate_counsellor",
    "transfer_to_human": "escalate_counsellor",
    "talk_to_counsellor": "escalate_counsellor",
    "email_counsellor": "escalate_counsellor",
    "notify_counsellor": "escalate_counsellor",
    "report_lead": "escalate_counsellor",
    "schedule_campus_visit": "schedule_campus_visit",
    "campus_visit": "schedule_campus_visit",
    "schedule_visit": "schedule_campus_visit",
    "book_visit": "schedule_campus_visit",
    "book_campus_visit": "schedule_campus_visit",
    "campus_tour": "schedule_campus_visit",
    "visit": "schedule_campus_visit",
}


def _normalize_action_type(raw: str) -> str:
    key = (raw or "").strip().lower()
    return _ACTION_TYPE_ALIASES.get(key, key)


_CANONICAL_ACTION_TYPES = {
    "send_brochure",
    "schedule_followup",
    "callback",
    "escalate_counsellor",
    "schedule_campus_visit",
}

# A status the model reads as terminal/negative is AUTHORITATIVE — never override
# "converted"/"lost"/"not_interested"/"closed" with an action-derived status.
_AUTHORITATIVE_STATUS = {"converted", "lost", "not_interested", "closed"}

# The model may assert an application-lifecycle stage in `status` (e.g. "I've
# started my application" / "I've paid"). Lifecycle now lives ONLY in
# `funnel_stage`, so we translate such a proposed status to the matching funnel
# stage (forward-only fold happens in the store) and let the call-status derive
# from actions like any other call.
_PROPOSED_STAGE_TO_FUNNEL = {
    "application_started": "application_started",
    "application_completed_payment_pending": "fees_pending",
    "application_submitted": "application_submitted",
}

# Operational call-statuses the model is allowed to propose directly. A proposed
# interest tier (cold/warm/hot) or application stage is NOT here — those route to
# lead_priority / funnel_stage instead, and the call-status defaults to "called".
_OPERATIONAL_STATUSES = {"new", "called", "followup", "scheduled", "delegated"}


def _canon_doc(doc: str) -> str:
    """Stable label for a sendable document so 'Fee Structure', 'fee structure ' and
    'fee  structure' collapse to ONE task (else each makes a distinct task that
    AegisBackend fuzzy-resolves to the SAME doc → duplicate sends). AegisBackend
    matches the words to its catalog at send time, so we keep the words and only
    normalise case/whitespace."""
    return " ".join((doc or "").strip().lower().split())


def _status_from_actions(action_types: set[str], proposed: str | None) -> str | None:
    """Derive the lead's next OPERATIONAL call-status, by precedence:

      1. an explicit terminal/negative read from the model (converted/lost/...),
      2. escalate_counsellor  → delegated (a human owns the next touch),
      3. a scheduled campus visit → scheduled,
      4. an agreed follow-up / callback → followup,
      5. an operational status the model proposed directly (new/called/...),
      6. DEFAULT → called (the lead has been engaged this session; this moves it
         off IN_CALL and keeps it dialable for the next nurture touch).

    Lead TEMPERATURE (cold/warm/hot) is no longer a status — it's derived to
    `lead_priority` from the interest score in the store. The admissions LIFECYCLE
    stage (application_*) is routed to `funnel_stage` by the caller. So a warm
    lead with an agreed callback rests at status FOLLOWUP (dialable) with
    lead_priority WARM — the two axes are independent.
    """
    proposed_l = (proposed or "").strip().lower()
    if proposed_l in _AUTHORITATIVE_STATUS:
        return proposed_l
    if "escalate_counsellor" in action_types:
        return "delegated"
    if "schedule_campus_visit" in action_types:
        return "scheduled"
    if "schedule_followup" in action_types or "callback" in action_types:
        return "followup"
    if proposed_l in _OPERATIONAL_STATUSES:
        return proposed_l
    # A bare interest tier / application stage / blank → the lead was still
    # engaged this session: rest it at CALLED (off IN_CALL, dialable, returning).
    return "called"


def _route_proposed_stage_to_funnel(analysis: dict[str, Any]) -> None:
    """If the model proposed an application-lifecycle stage in `status`, record it
    as the admissions `funnel_stage` instead (lifecycle has its own axis now). The
    store applies it forward-only; the call-status is then derived from actions."""
    proposed = (analysis.get("status") or "").strip().lower()
    funnel = _PROPOSED_STAGE_TO_FUNNEL.get(proposed)
    if funnel:
        analysis["funnel_stage"] = funnel


def _normalize_actions_and_status(analysis: dict[str, Any], *, session_id: str | None = None) -> None:
    """Apply the business rules in CODE (not only the prompt). Mutates `analysis`:
      1. drop non-canonical / malformed actions,
      2. canonicalise send_brochure doc labels + drop empty-doc sends,
      3. de-dupe identical actions within this conversation,
      4. cap escalate_counsellor at ONE; once escalating, drop schedule_followup /
         callback (the human counsellor owns the next touch),
      5. recompute `status` from the resulting action set by precedence.

    Logs the RAW model actions BEFORE filtering + the reason for every drop, so a
    "0 tasks created" outcome is never invisible again — it tells apart a model
    RECALL failure (model emitted nothing) from a FILTER drop (model emitted
    something we discarded).
    """
    raw = analysis.get("actions")
    raw_list = raw if isinstance(raw, list) else []
    log.info(
        "analyzer raw actions (pre-normalize)",
        session_id=session_id,
        raw_count=len(raw_list),
        raw_types=[
            a.get("type") if isinstance(a, dict) else type(a).__name__
            for a in raw_list
        ],
    )
    if not isinstance(raw, list):
        analysis["actions"] = []
        _route_proposed_stage_to_funnel(analysis)
        analysis["status"] = _status_from_actions(set(), analysis.get("status"))
        return

    cleaned: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    escalated = False
    for a in raw:
        if not isinstance(a, dict):
            continue
        atype = _normalize_action_type(a.get("type") or "")
        if atype not in _CANONICAL_ACTION_TYPES:
            log.info(
                "analyzer action dropped (non-canonical type)",
                session_id=session_id, raw_type=a.get("type"),
            )
            continue
        payload = a.get("payload") if isinstance(a.get("payload"), dict) else {}

        if atype == "send_brochure":
            doc = _canon_doc(str(payload.get("doc") or ""))
            if not doc:
                log.info(
                    "analyzer action dropped (send_brochure with empty doc)",
                    session_id=session_id,
                )
                continue
            payload = {**payload, "doc": doc}
            ident = ("send_brochure", doc)  # one task per DISTINCT document
        elif atype == "escalate_counsellor":
            if escalated:
                continue  # at most ONE escalation per conversation
            escalated = True
            ident = ("escalate_counsellor", "")
        else:
            ident = (atype, "")  # one schedule_followup / callback / campus_visit

        if ident in seen:
            continue
        seen.add(ident)
        cleaned.append({"type": atype, "payload": payload})

    # Once escalating, the human owns the next touch — drop any auto follow-ups.
    if escalated:
        cleaned = [a for a in cleaned if a["type"] not in ("schedule_followup", "callback")]

    analysis["actions"] = cleaned
    # A proposed application stage carries the admissions LIFECYCLE, not a
    # call-status → route it to funnel_stage (the store folds it forward-only).
    _route_proposed_stage_to_funnel(analysis)
    analysis["status"] = _status_from_actions(
        {a["type"] for a in cleaned}, analysis.get("status")
    )


async def _emit_action_tasks(sess: Session, analysis: dict[str, Any]) -> None:
    """Turn analyzer-detected actions into idempotent outbox tasks."""
    store = get_store()
    actions = analysis.get("actions") or []
    # Visibility: log the raw action types the LLM produced so a dropped/dedup'd
    # action is never invisible again (the WhatsApp-not-sent black hole).
    if actions:
        log.info(
            "analyzer actions",
            session_id=sess.session_id,
            raw_types=[a.get("type") if isinstance(a, dict) else type(a).__name__ for a in actions],
        )
    for action in actions:
        if not isinstance(action, dict):
            log.warning("action skipped (not an object)", session_id=sess.session_id, action=str(action)[:120])
            continue
        raw_type = (action.get("type") or "").strip()
        atype = _normalize_action_type(raw_type)
        payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
        if atype not in {"send_brochure", "schedule_followup", "callback", "escalate_counsellor", "schedule_campus_visit"}:
            log.warning(
                "action skipped (unrecognized type)",
                session_id=sess.session_id, lead_id=sess.lead_id,
                raw_type=raw_type, payload_keys=list(payload.keys()),
            )
            continue
        if atype != raw_type.lower():
            log.info("action type normalized", session_id=sess.session_id, raw_type=raw_type, mapped=atype)
        if atype == "schedule_campus_visit":
            # Resolve the day/time the model emitted into a concrete, IST-anchored
            # display date. Skip the task if it's unusable (no date) — better no
            # email than a blank/wrong one.
            resolved = _resolve_visit_when(payload, _now_ist())
            if resolved is None:
                log.warning("campus visit skipped (no resolvable date)",
                            session_id=sess.session_id, lead_id=sess.lead_id, payload_keys=list(payload.keys()))
                continue
            display_date, display_time = resolved
            payload = {**payload, "visit_date": display_date, "visit_time": display_time}
        if atype == "escalate_counsellor":
            # Snapshot THIS conversation's read into the task payload so the
            # counsellor email reflects the call that triggered the escalation
            # (lead fields may be overwritten by later sessions before the
            # action worker runs).
            payload = {
                **payload,
                "interest": analysis.get("interest"),
                "confidence": analysis.get("confidence"),
                "sentiment": analysis.get("sentiment"),
                "session_summary": analysis.get("session_summary") or analysis.get("summary"),
                "next_best_action": analysis.get("next_best_action"),
            }
        # Stable dedupe so the same intent never double-fires.
        if atype == "send_brochure":
            suffix = str(payload.get("doc") or "default")
        else:
            suffix = sess.session_id
        dedupe_key = f"{sess.lead_id}:{atype}:{suffix}"
        channel = (
            "whatsapp" if atype == "send_brochure"
            else "email" if atype in ("escalate_counsellor", "schedule_campus_visit")
            else None
        )
        created = await store.enqueue_task(
            lead_id=sess.lead_id,
            type=atype,
            payload=payload,
            dedupe_key=dedupe_key,
            session_id=sess.session_id,
            channel=channel,
            max_attempts=get_settings().actions_max_attempts,
        )
        if created:
            log.info("task enqueued", lead_id=sess.lead_id, type=atype, dedupe_key=dedupe_key)
        else:
            log.info("task NOT enqueued (deduped — already queued/sent)",
                     lead_id=sess.lead_id, type=atype, dedupe_key=dedupe_key)


async def run_analyzer_loop(stop: asyncio.Event) -> None:
    """Poll for ended sessions and analyze them until `stop` is set."""
    s = get_settings()
    warned_no_key = False
    log.info("analyzer loop started", poll_seconds=s.analyzer_poll_seconds, model=s.analyzer_llm_model)
    while not stop.is_set():
        try:
            if not s.llm_api_key:
                if not warned_no_key:
                    log.warning("LLM_API_KEY not set — analyzer idling until configured")
                    warned_no_key = True
                await _sleep_or_stop(stop, s.analyzer_poll_seconds)
                continue

            # Close idle WhatsApp/chat threads (no hangup) + rescue crashed
            # calls, then they flow into analysis below.
            reaped = await get_store().reap_stale_active_sessions(
                older_than_minutes=s.session_idle_close_minutes,
                voice_older_than_minutes=s.voice_session_idle_close_minutes,
            )
            if reaped:
                log.info("closed idle sessions", count=reaped)

            sessions = await get_store().claim_unanalyzed_sessions(limit=5)
            for sess in sessions:
                if stop.is_set():
                    break
                try:
                    await analyze_session(sess)
                except Exception as e:  # noqa: BLE001
                    log.warning("analyze_session crashed", session_id=sess.session_id, err=str(e)[:200])
        except Exception as e:  # noqa: BLE001
            log.warning("analyzer loop iteration failed", err=str(e)[:200])

        await _sleep_or_stop(stop, s.analyzer_poll_seconds)
    log.info("analyzer loop stopped")


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass
