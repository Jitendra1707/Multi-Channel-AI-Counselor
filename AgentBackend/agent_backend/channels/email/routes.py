"""Email channel — outbound HTTP surface (POST /api/email/...).

Additive, non-breaking. Three endpoints:
  - POST /api/email/send         generic send (candidate emails, follow-ups)
  - POST /api/email/report-lead  email a good/best-lead report to counsellors
  - POST /api/email/escalation   rich HTML lead-escalation report (called by the
                                 BusinessLayer action worker when the post-call
                                 analyzer emits `escalate_counsellor`)

Mirrors the WhatsApp send route (`channels/whatsapp/send_routes.py`): decoupled
from any inbound webhook, mounted under /api/email in main.py. The BusinessLayer
action worker, the agent (via a future tool), or an operator can call these.
All sends are best-effort via `channels/email/client.py` — a misconfigured /
unreachable SMTP server surfaces as a 502 for the caller to retry, never a crash.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent_backend.channels.email.client import parse_recipients, send_email
from agent_backend.config import get_settings
from agent_backend.data import LeadRepo
from agent_backend.infra import get_logger

log = get_logger(__name__)

email_router = APIRouter(prefix="/api/email", tags=["email"])


# ---------------------------------------------------------------------------
# Generic send — candidate emails, follow-ups, confirmations.
# ---------------------------------------------------------------------------
class SendEmailRequest(BaseModel):
    to: str | list[str] = Field(..., description="Recipient(s): address, comma-separated string, or list.")
    subject: str
    body: str = Field(..., description="Plain-text body (always sent).")
    html: str | None = Field(default=None, description="Optional HTML alternative part.")
    cc: str | list[str] | None = None
    reply_to: str | None = None


class CampusVisitEmailRequest(BaseModel):
    visit_date: str = Field(..., description="Human-readable date, e.g. 'Saturday, 21 June 2025'.")
    visit_time: str = Field(..., description="Human-readable time, e.g. '11:00 AM'.")
    to: str | list[str] | None = Field(default=None, description="Recipient email; or pass lead_id.")
    lead_id: str | None = Field(default=None, description="Resolve recipient + name from this lead if `to` is absent.")
    candidate_name: str | None = None
    campus_name: str | None = None
    campus_address: str | None = None
    contact_phone: str | None = None
    map_url: str | None = None
    notes: str | None = Field(default=None, description="Optional one-liner, e.g. 'Ask for the Admissions desk'.")
    cc: str | list[str] | None = None


@email_router.post("/campus-visit")
async def campus_visit(req: CampusVisitEmailRequest) -> dict:
    """Send the beautifully-designed campus-visit confirmation email. Resolves
    the recipient from `to`, or from the lead's email when `lead_id` is given."""
    from agent_backend.channels.email.campus_visit import send_campus_visit_email

    to = req.to
    name = req.candidate_name
    if not to and req.lead_id:
        lead = LeadRepo.get().get_by_id(req.lead_id)
        if lead is not None:
            to = lead.email
            name = name or lead.full_name
    if not parse_recipients(to):
        raise HTTPException(400, "no recipient email (pass `to` or a `lead_id` with an email on file)")

    result = await send_campus_visit_email(
        to=to,
        candidate_name=name,
        visit_date=req.visit_date,
        visit_time=req.visit_time,
        campus_name=req.campus_name,
        campus_address=req.campus_address,
        contact_phone=req.contact_phone,
        map_url=req.map_url,
        notes=req.notes,
        cc=req.cc,
        lead_id=req.lead_id,
    )
    if result is None:
        raise HTTPException(502, "email send failed (check EMAIL_SMTP_* config / SMTP auth)")
    log.info("[email] campus-visit confirmation sent", lead_id=req.lead_id, when=f"{req.visit_date} {req.visit_time}")
    return {"ok": True, **result}


@email_router.post("/send")
async def send(req: SendEmailRequest) -> dict:
    result = await send_email(
        to=req.to,
        subject=req.subject,
        body_text=req.body,
        body_html=req.html,
        cc=req.cc,
        reply_to=req.reply_to,
    )
    if result is None:
        raise HTTPException(502, "email send failed (check EMAIL_SMTP_* config / SMTP auth)")
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# Lead report — email a good/best lead to the human counsellor(s).
# The CALLER decides a lead is "good" (e.g. BusinessLayer when interest/score is
# high, or an operator); this endpoint just composes + sends the report.
# ---------------------------------------------------------------------------
class ReportLeadRequest(BaseModel):
    lead_id: str
    to: str | list[str] | None = Field(
        default=None,
        description="Override recipients; defaults to EMAIL_COUNSELLOR_RECIPIENTS.",
    )
    note: str | None = Field(default=None, description="Optional free-text note from the agent/operator.")


@email_router.post("/report-lead")
async def report_lead(req: ReportLeadRequest) -> dict:
    """Compose a report on a (good/best) lead and email it to human counsellors."""
    lead = LeadRepo.get().get_by_id(req.lead_id)
    if lead is None:
        raise HTTPException(404, f"unknown lead_id={req.lead_id!r}")

    recipients = parse_recipients(req.to) or parse_recipients(
        get_settings().email_counsellor_recipients
    )
    if not recipients:
        raise HTTPException(
            400, "no counsellor recipients (set EMAIL_COUNSELLOR_RECIPIENTS or pass `to`)"
        )

    subject, text, html = _compose_lead_report(lead, note=req.note)
    result = await send_email(to=recipients, subject=subject, body_text=text, body_html=html)
    if result is None:
        raise HTTPException(502, "email send failed (check EMAIL_SMTP_* config / SMTP auth)")
    log.info("[email] lead report sent", lead_id=lead.lead_id, recipients=len(recipients))
    return {"ok": True, "lead_id": lead.lead_id, "recipients": len(recipients)}


# ---------------------------------------------------------------------------
# Report composition.
# ---------------------------------------------------------------------------
def _esc(v: object) -> str:
    return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _compose_lead_report(lead, *, note: str | None = None) -> tuple[str, str, str]:
    """Build (subject, plain-text, html) for a counsellor lead report from a Lead."""
    name = lead.full_name or "Unknown"

    # Lead temperature (hot/warm/cold) from the BusinessLayer analyzer, overlaid
    # onto the in-memory lead. Blank until first analyzed.
    priority = (getattr(lead, "lead_priority", "") or "").strip()
    score_str = f" · {priority}" if priority else ""

    subject = f"[Lead] {name} — {lead.status.value}{score_str}"

    facts = lead.facts or {}
    concerns = lead.open_concerns or []
    summary = lead.last_session_summary or "(no summary yet)"

    facts_txt = "\n".join(f"  - {k}: {v}" for k, v in facts.items()) or "  (none captured yet)"
    concerns_txt = "\n".join(f"  - {c}" for c in concerns) or "  (none)"
    text = (
        "Lead report\n===========\n"
        f"Name:    {name}\n"
        f"Lead ID: {lead.lead_id}\n"
        f"Status:  {lead.status.value}{score_str}\n"
        f"Phone:   {lead.phone_e164 or '-'}\n"
        f"Email:   {lead.email or '-'}\n"
        f"Course:  {lead.course_interest or '-'}\n"
        f"Source:  {lead.source}\n\n"
        f"Summary:\n  {summary}\n\n"
        f"Extracted facts:\n{facts_txt}\n\n"
        f"Open concerns:\n{concerns_txt}\n"
    )
    if note:
        text += f"\nNote:\n  {note}\n"

    facts_html = "".join(f"<li>{_esc(k)}: {_esc(v)}</li>" for k, v in facts.items()) or "<li>(none captured yet)</li>"
    concerns_html = "".join(f"<li>{_esc(c)}</li>" for c in concerns) or "<li>(none)</li>"
    html = (
        "<h2>Lead report</h2>"
        "<table cellpadding='4' style='border-collapse:collapse'>"
        f"<tr><td><b>Name</b></td><td>{_esc(name)}</td></tr>"
        f"<tr><td><b>Lead ID</b></td><td>{_esc(lead.lead_id)}</td></tr>"
        f"<tr><td><b>Status</b></td><td>{_esc(lead.status.value)}{_esc(score_str)}</td></tr>"
        f"<tr><td><b>Phone</b></td><td>{_esc(lead.phone_e164 or '-')}</td></tr>"
        f"<tr><td><b>Email</b></td><td>{_esc(lead.email or '-')}</td></tr>"
        f"<tr><td><b>Course</b></td><td>{_esc(lead.course_interest or '-')}</td></tr>"
        f"<tr><td><b>Source</b></td><td>{_esc(lead.source)}</td></tr>"
        "</table>"
        f"<p><b>Summary:</b><br>{_esc(summary)}</p>"
        f"<p><b>Extracted facts:</b></p><ul>{facts_html}</ul>"
        f"<p><b>Open concerns:</b></p><ul>{concerns_html}</ul>"
    )
    if note:
        html += f"<p><b>Note:</b><br>{_esc(note)}</p>"

    return subject, text, html


# ---------------------------------------------------------------------------
# Lead ESCALATION — rich report mailed to human counsellors when the post-call
# analyzer hands a lead over (hot lead, or the candidate asked for a human).
# The BusinessLayer action worker supplies the FULL picture in the request so
# this endpoint composes purely from authoritative data (no stale local state).
# ---------------------------------------------------------------------------
class EscalationEmailRequest(BaseModel):
    lead_id: str
    full_name: str | None = None
    phone: str | None = None
    email: str | None = None
    status: str | None = None
    interest: int | None = None
    confidence: int | None = None
    sentiment: str | None = None
    course_interest: str | None = None
    reason: str | None = Field(default=None, description="Why the analyzer escalated.")
    session_summary: str | None = Field(default=None, description="What happened on the triggering call.")
    journey_summary: str | None = Field(default=None, description="Cumulative story across all conversations.")
    next_best_action: str | None = None
    facts: dict[str, Any] = Field(default_factory=dict)
    open_concerns: list[str] = Field(default_factory=list)
    sent_items: list[dict[str, Any]] = Field(default_factory=list)
    to: str | list[str] | None = Field(
        default=None, description="Override recipients; defaults to EMAIL_COUNSELLOR_RECIPIENTS."
    )


@email_router.post("/escalation")
async def escalation(req: EscalationEmailRequest) -> dict:
    recipients = parse_recipients(req.to) or parse_recipients(
        get_settings().email_counsellor_recipients
    )
    if not recipients:
        raise HTTPException(
            400, "no counsellor recipients (set EMAIL_COUNSELLOR_RECIPIENTS or pass `to`)"
        )
    subject, text, html = _compose_escalation(req)
    result = await send_email(to=recipients, subject=subject, body_text=text, body_html=html)
    if result is None:
        raise HTTPException(502, "email send failed (check EMAIL_SMTP_* config / SMTP auth)")
    log.info("[email] escalation sent", lead_id=req.lead_id, recipients=len(recipients))
    return {"ok": True, "lead_id": req.lead_id, "recipients": len(recipients)}


def _humanize(key: str) -> str:
    return key.replace("_", " ").strip().capitalize()


def _interest_color(score: int) -> str:
    if score >= 80:
        return "#16a34a"  # green — hot
    if score >= 60:
        return "#d97706"  # amber — warm
    return "#dc2626"      # red — cool

_SENTIMENT_BADGES = {
    "positive": ("😊", "#ecfdf5", "#047857"),
    "neutral": ("😐", "#f1f5f9", "#475569"),
    "negative": ("🙁", "#fef2f2", "#b91c1c"),
    "frustrated": ("😤", "#fef2f2", "#b91c1c"),
}


def _compose_escalation(req: EscalationEmailRequest) -> tuple[str, str, str]:
    """Build (subject, plain-text, html) for a counsellor escalation email.

    HTML is intentionally email-client-safe: table layout, inline styles only,
    solid-colour fallbacks before gradients, no external assets."""
    name = (req.full_name or "Unknown").strip() or "Unknown"
    interest = max(0, min(100, int(req.interest or 0)))
    confidence = max(0, min(100, int(req.confidence or 0)))
    sentiment = (req.sentiment or "").strip().lower()
    course = req.course_interest or (req.facts or {}).get("course_interest") or ""
    phone = (req.phone or "").strip()
    digits = phone.lstrip("+").replace(" ", "").replace("-", "")
    reason = (req.reason or "").strip() or "The AI counsellor flagged this lead for human attention."
    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")

    hot = interest >= 80
    subject = (
        f"{'🔥 Hot lead' if hot else '🎓 Lead escalation'}: {name}"
        f" — {interest}/100 interest{f' · {course}' if course else ''}"
    )

    # ---------------- plain text (always sent alongside HTML) ----------------
    facts_txt = "\n".join(f"  - {_humanize(k)}: {v}" for k, v in (req.facts or {}).items()) or "  (none captured yet)"
    concerns_txt = "\n".join(f"  - {c}" for c in (req.open_concerns or [])) or "  (none)"
    sent_txt = "\n".join(
        f"  - {x.get('item')} (via {x.get('channel')}, {x.get('at')})" for x in (req.sent_items or [])
    ) or "  (nothing sent yet)"
    text = (
        f"LEAD ESCALATED TO YOU — {name}\n"
        f"{'=' * 50}\n"
        f"Why: {reason}\n\n"
        f"Interest: {interest}/100 · Confidence: {confidence}/100 · Sentiment: {sentiment or '-'}\n"
        f"Phone: {phone or '-'} · Email: {req.email or '-'} · Course: {course or '-'}\n"
        f"Lead ID: {req.lead_id}\n\n"
        f"Facts:\n{facts_txt}\n\n"
        f"Open concerns:\n{concerns_txt}\n\n"
        f"This call:\n  {req.session_summary or '(no summary)'}\n\n"
        f"Already sent to the candidate:\n{sent_txt}\n\n"
        f"Recommended next step:\n  {req.next_best_action or '(use your judgement)'}\n\n"
        f"Generated by Aisha (AI counsellor) · {now_str}\n"
    )

    # -------------------------------- HTML -----------------------------------
    bar_color = _interest_color(interest)
    s_emoji, s_bg, s_fg = _SENTIMENT_BADGES.get(sentiment, ("💬", "#f1f5f9", "#475569"))

    def section(title: str, body_html: str) -> str:
        return (
            f"<tr><td style='padding:20px 32px 0;'>"
            f"<p style='margin:0 0 6px;font-size:11px;font-weight:700;letter-spacing:1.2px;"
            f"text-transform:uppercase;color:#94a3b8;'>{_esc(title)}</p>"
            f"<div style='font-size:14px;line-height:1.65;color:#334155;'>{body_html}</div>"
            f"</td></tr>"
        )

    facts_rows = "".join(
        f"<tr style='background:{'#f8fafc' if i % 2 else '#ffffff'};'>"
        f"<td style='padding:8px 14px;font-size:13px;color:#64748b;white-space:nowrap;'>{_esc(_humanize(k))}</td>"
        f"<td style='padding:8px 14px;font-size:13px;color:#0f172a;font-weight:600;'>{_esc(v)}</td></tr>"
        for i, (k, v) in enumerate((req.facts or {}).items())
    )
    facts_block = (
        f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
        f"style='border:1px solid #e2e8f0;border-radius:10px;border-collapse:separate;overflow:hidden;'>{facts_rows}</table>"
        if facts_rows else "<p style='margin:0;color:#94a3b8;'>None captured yet.</p>"
    )

    concerns_block = "".join(
        f"<div style='margin:0 0 8px;padding:10px 14px;background:#fef9c3;border-radius:8px;"
        f"font-size:13px;color:#713f12;'>⚠️&nbsp; {_esc(c)}</div>"
        for c in (req.open_concerns or [])
    ) or "<p style='margin:0;color:#94a3b8;'>None — nothing is blocking this candidate.</p>"

    sent_block = "".join(
        f"<div style='margin:0 0 6px;font-size:13px;color:#475569;'>"
        f"✅&nbsp; {_esc(x.get('item'))} <span style='color:#94a3b8;'>(via {_esc(x.get('channel'))})</span></div>"
        for x in (req.sent_items or [])
    ) or "<p style='margin:0;color:#94a3b8;'>Nothing sent yet.</p>"

    buttons = ""
    if phone:
        buttons += (
            f"<a href='tel:{_esc(phone)}' style='display:inline-block;margin:0 10px 8px 0;padding:12px 26px;"
            f"background:#312e81;color:#ffffff;text-decoration:none;border-radius:10px;"
            f"font-size:14px;font-weight:700;'>📞&nbsp; Call {_esc(name.split()[0])}</a>"
        )
        buttons += (
            f"<a href='https://wa.me/{_esc(digits)}' style='display:inline-block;margin:0 10px 8px 0;padding:12px 26px;"
            f"background:#16a34a;color:#ffffff;text-decoration:none;border-radius:10px;"
            f"font-size:14px;font-weight:700;'>💬&nbsp; WhatsApp</a>"
        )
    if req.email:
        buttons += (
            f"<a href='mailto:{_esc(req.email)}' style='display:inline-block;margin:0 0 8px;padding:12px 26px;"
            f"background:#475569;color:#ffffff;text-decoration:none;border-radius:10px;"
            f"font-size:14px;font-weight:700;'>✉️&nbsp; Email</a>"
        )

    html = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#eef2f7;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef2f7;">
<tr><td align="center" style="padding:28px 12px;">
<table role="presentation" width="640" cellpadding="0" cellspacing="0"
       style="width:640px;max-width:100%;background:#ffffff;border-radius:16px;overflow:hidden;
              font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
              box-shadow:0 4px 24px rgba(15,23,42,.08);">

  <!-- Header -->
  <tr><td style="background:#312e81;background:linear-gradient(135deg,#312e81 0%,#6d28d9 100%);padding:30px 32px;">
    <p style="margin:0 0 14px;font-size:11px;font-weight:700;letter-spacing:1.5px;color:#c7d2fe;text-transform:uppercase;">
      Sreenidhi University &nbsp;·&nbsp; Aisha AI Counsellor</p>
    <p style="margin:0 0 6px;font-size:13px;color:#e0e7ff;">{'🔥 Hot lead — ready for a human touch' if hot else '🎓 A candidate needs your attention'}</p>
    <h1 style="margin:0;font-size:28px;line-height:1.2;color:#ffffff;">{_esc(name)}</h1>
    <p style="margin:10px 0 0;">
      <span style="display:inline-block;padding:5px 14px;background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.35);
                   border-radius:999px;color:#ffffff;font-size:12px;font-weight:700;">
        {_esc((req.status or 'escalated').replace('_', ' ').upper())}</span>
      {f"<span style='display:inline-block;margin-left:8px;padding:5px 14px;background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.35);border-radius:999px;color:#ffffff;font-size:12px;font-weight:700;'>{_esc(course)}</span>" if course else ""}
    </p>
  </td></tr>

  <!-- Why escalated -->
  <tr><td style="padding:24px 32px 0;">
    <div style="background:#fff7ed;border-left:4px solid #f59e0b;border-radius:0 10px 10px 0;padding:14px 18px;">
      <p style="margin:0 0 4px;font-size:11px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#b45309;">
        Why this lead needs you</p>
      <p style="margin:0;font-size:14px;line-height:1.6;color:#7c2d12;">{_esc(reason)}</p>
    </div>
  </td></tr>

  <!-- Scoreboard -->
  <tr><td style="padding:22px 32px 0;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td width="42%" style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:16px 18px;" valign="top">
        <p style="margin:0 0 6px;font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#94a3b8;">Interest</p>
        <p style="margin:0 0 10px;font-size:26px;font-weight:800;color:{bar_color};">{interest}<span style="font-size:13px;color:#94a3b8;font-weight:600;">/100</span></p>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
          <tr><td style="background:{bar_color};height:8px;border-radius:6px 0 0 6px;" width="{interest}%"></td>
              <td style="background:#e2e8f0;height:8px;border-radius:0 6px 6px 0;" width="{100 - interest}%"></td></tr>
        </table>
      </td>
      <td width="4%"></td>
      <td width="26%" style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:16px 18px;" valign="top">
        <p style="margin:0 0 6px;font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#94a3b8;">Confidence</p>
        <p style="margin:0;font-size:26px;font-weight:800;color:#0f172a;">{confidence}<span style="font-size:13px;color:#94a3b8;font-weight:600;">/100</span></p>
      </td>
      <td width="4%"></td>
      <td width="24%" style="background:{s_bg};border:1px solid #e2e8f0;border-radius:12px;padding:16px 18px;" valign="top">
        <p style="margin:0 0 6px;font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#94a3b8;">Sentiment</p>
        <p style="margin:0;font-size:17px;font-weight:800;color:{s_fg};">{s_emoji} {_esc((sentiment or '—').capitalize())}</p>
      </td>
    </tr></table>
  </td></tr>

  
  {section("Candidate profile", facts_block)}
  {section("Open concerns to address", concerns_block)}
  {section("What happened on this call", f"<p style='margin:0;'>{_esc(req.session_summary or '(no summary available)')}</p>")}
  {section("Already shared with the candidate", sent_block)}

  <!-- Recommended action -->
  <tr><td style="padding:22px 32px 0;">
    <div style="background:#ecfdf5;border:1px solid #a7f3d0;border-radius:12px;padding:16px 18px;">
      <p style="margin:0 0 4px;font-size:11px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#047857;">
        💡 Recommended next step</p>
      <p style="margin:0;font-size:14px;line-height:1.6;color:#064e3b;">{_esc(req.next_best_action or 'Reach out while the conversation is still fresh.')}</p>
    </div>
  </td></tr>

  <!-- Contact buttons -->
  <tr><td style="padding:24px 32px 6px;">{buttons}</td></tr>

  <!-- Footer -->
  <tr><td style="padding:18px 32px 26px;border-top:1px solid #f1f5f9;">
    <p style="margin:14px 0 0;font-size:12px;color:#94a3b8;">
      Lead ID <span style="font-family:Consolas,monospace;color:#64748b;">{_esc(req.lead_id)}</span>
      &nbsp;·&nbsp; {_esc(phone or '-')} &nbsp;·&nbsp; {_esc(req.email or '-')}<br>
      Escalated automatically by <b>Aisha</b>, the AI admissions counsellor · {_esc(now_str)}</p>
  </td></tr>

</table>
</td></tr></table>
</body></html>"""

    return subject, text, html
