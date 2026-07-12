"""Campus-visit confirmation email — compose + send.

A self-contained, reusable helper so BOTH the HTTP endpoint
(`POST /api/email/campus-visit`) and the live agent tool
(`schedule_campus_visit`) produce the SAME beautiful confirmation without
duplicating the template.

The HTML is intentionally email-client-safe: table layout, inline styles only,
solid-colour fallbacks before gradients, no external images/assets — so it
renders correctly in Gmail, Outlook, and mobile clients. A plain-text part is
always sent alongside for clients that strip HTML.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

from agent_backend.channels.email.client import parse_recipients, send_email
from agent_backend.channels.email.qr import build_campus_pass_qr
from agent_backend.config import get_settings
from agent_backend.infra import get_logger

log = get_logger(__name__)

# Content-ID for the inline campus-pass QR image (referenced as cid:<this> in
# the HTML and attached to the HTML part by send_email).
_QR_CID = "campus-pass-qr"


def _esc(v: object) -> str:
    return (
        str(v)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _first_name(name: str | None) -> str:
    n = (name or "").strip()
    return n.split()[0] if n and n.lower() != "unknown" else "there"


def _directions_url(address: str) -> str:
    """Google Maps DIRECTIONS deep-link to the campus. Opening it starts
    turn-by-turn navigation from the user's current location (the Maps
    app/site supplies the origin). Cross-platform: resolves to the native
    Maps app on Android/iOS and maps.google.com on desktop."""
    return (
        "https://www.google.com/maps/dir/?api=1"
        f"&destination={quote_plus(address)}&travelmode=driving"
    )


def _map_view_url(address: str, override: str | None = None) -> str:
    """Google Maps SEARCH link — just shows the campus pinned on the map
    (no navigation). Honours an explicit `map_url`/CAMPUS_MAP_URL override so
    a deployment can point at a precise place-ID/coords link if it has one."""
    if override and override.strip():
        return override.strip()
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(address)}"


# What a visitor can expect — rendered as a checklist. Static, brand-safe copy
# (no facts/figures, so nothing to keep in sync with the KB).
_EXPECT = [
    ("🎓", "A guided campus walkthrough — academic blocks, labs and library"),
    ("🏠", "A look at the hostels and student facilities"),
    ("💬", "A one-on-one with an admissions counsellor on courses, fees & scholarships"),
    ("🚀", "A peek at placements and the industry-partner ecosystem"),
]


def compose_campus_visit_email(
    *,
    candidate_name: str | None,
    visit_date: str,
    visit_time: str,
    campus_name: str | None = None,
    campus_address: str | None = None,
    contact_phone: str | None = None,
    map_url: str | None = None,
    notes: str | None = None,
    pass_id: str | None = None,
    has_qr: bool = False,
) -> tuple[str, str, str]:
    """Return (subject, plain_text, html) for a campus-visit confirmation.

    When `has_qr` is True the HTML renders an inline campus-pass image
    (`<img src="cid:{_QR_CID}">`) — the caller is responsible for attaching the
    matching QR PNG via `send_email(inline_images=...)`. `pass_id` is the short
    human-quotable id printed alongside the pass (and in the plain-text part)."""
    s = get_settings()
    uni = (campus_name or s.university_short_name).strip()
    address = (campus_address or s.campus_address).strip()
    contact = (contact_phone or s.campus_visit_contact).strip()
    # Two destinations: start turn-by-turn navigation, or just view the pin.
    # `map_url`/CAMPUS_MAP_URL (if set) overrides the "view on map" link only.
    nav_url = _directions_url(address)
    view_url = _map_view_url(address, override=map_url or s.campus_map_url)
    first = _first_name(candidate_name)
    date_s = (visit_date or "").strip()
    time_s = (visit_time or "").strip()
    note_s = (notes or "").strip()
    pass_s = (pass_id or "").strip()
    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")

    subject = f"🎓 Your campus visit to {uni} is confirmed — {date_s}"

    # ----------------------------- plain text --------------------------------
    expect_txt = "\n".join(f"  {e[0]} {e[1]}" for e in _EXPECT)
    pass_txt = ""
    if has_qr:
        pass_txt = (
            f"\nYOUR CAMPUS PASS{(' — ' + pass_s) if pass_s else ''}\n"
            f"  Show the QR code in this email to security at the gate for entry.\n"
            f"  (Can't see the code? Quote your pass id at the desk.)\n"
        )
    text = (
        f"Hi {first},\n\n"
        f"Your campus visit to {uni} is confirmed. We can't wait to show you around!\n\n"
        f"WHEN:  {date_s} at {time_s}\n"
        f"WHERE: {address}\n"
        f"{('NOTE:  ' + note_s + chr(10)) if note_s else ''}"
        f"{pass_txt}"
        f"\nWhat to expect:\n{expect_txt}\n\n"
        f"Getting here:\n"
        f"  Start navigation: {nav_url}\n"
        f"  View on map:      {view_url}\n\n"
        f"Questions before you come? Call us at {contact}.\n\n"
        f"See you soon,\n"
        f"Aisha — Admissions, {uni}\n\n"
        f"(Sent {now_str})\n"
    )

    # -------------------------------- HTML -----------------------------------
    expect_rows = "".join(
        f"<tr><td style='padding:7px 0;font-size:14px;color:#334155;line-height:1.5;' valign='top'>"
        f"<span style='display:inline-block;width:26px;'>{e[0]}</span>{_esc(e[1])}</td></tr>"
        for e in _EXPECT
    )
    note_block = (
        f"<tr><td style='padding:18px 32px 0;'>"
        f"<div style='background:#fff7ed;border-left:4px solid #f59e0b;border-radius:0 10px 10px 0;"
        f"padding:12px 16px;font-size:13px;color:#7c2d12;line-height:1.55;'>"
        f"📌&nbsp; {_esc(note_s)}</div></td></tr>"
        if note_s else ""
    )

    # Campus-pass QR — the visitor shows this at the gate; security scans it for
    # entry. Rendered as an inline (cid) image so it survives Gmail/Outlook
    # image stripping. Only shown when the caller attached a QR.
    pass_id_html = (
        f"<p style='margin:12px 0 0;font-size:12px;color:#e0e7ff;'>Pass ID&nbsp;·&nbsp;"
        f"<span style='display:inline-block;font-family:Consolas,monospace;font-weight:700;"
        f"color:#ffffff;background:rgba(255,255,255,.16);padding:3px 10px;border-radius:6px;'>"
        f"{_esc(pass_s)}</span></p>"
        if pass_s else ""
    )
    qr_block = (
        f"<tr><td style='padding:24px 32px 0;'>"
        f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
        f"style='background:#312e81;background:linear-gradient(135deg,#312e81 0%,#6d28d9 100%);"
        f"border-radius:14px;border-collapse:separate;overflow:hidden;'>"
        f"<tr><td align='center' style='padding:24px 22px 22px;'>"
        f"<p style='margin:0 0 4px;font-size:11px;font-weight:700;letter-spacing:1.5px;"
        f"text-transform:uppercase;color:#c7d2fe;'>🎫 Your campus entry pass</p>"
        f"<p style='margin:0 0 16px;font-size:13px;color:#e0e7ff;line-height:1.5;'>"
        f"Show this code to security at the gate</p>"
        f"<div style='display:inline-block;background:#ffffff;padding:12px;border-radius:14px;'>"
        f"<img src='cid:{_QR_CID}' width='180' height='180' alt='Campus visit QR pass' "
        f"style='display:block;width:180px;height:180px;border:0;border-radius:6px;'></div>"
        f"<p style='margin:14px 0 0;font-size:12px;color:#c7d2fe;line-height:1.5;'>"
        f"Keep this email handy on your phone — no printout needed.</p>"
        f"{pass_id_html}"
        f"</td></tr></table></td></tr>"
        if has_qr else ""
    )

    html = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#eef2f7;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef2f7;">
<tr><td align="center" style="padding:28px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0"
       style="width:600px;max-width:100%;background:#ffffff;border-radius:16px;overflow:hidden;
              font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
              box-shadow:0 4px 24px rgba(15,23,42,.08);">

  <!-- Header -->
  <tr><td style="background:#312e81;background:linear-gradient(135deg,#312e81 0%,#6d28d9 100%);padding:32px 32px 28px;">
    <p style="margin:0 0 14px;font-size:11px;font-weight:700;letter-spacing:1.5px;color:#c7d2fe;text-transform:uppercase;">
      {_esc(uni)} &nbsp;·&nbsp; Admissions</p>
    <p style="margin:0 0 6px;font-size:13px;color:#e0e7ff;">✅ Your campus visit is confirmed</p>
    <h1 style="margin:0;font-size:27px;line-height:1.25;color:#ffffff;">See you on campus, {_esc(first)}!</h1>
  </td></tr>

  <!-- Date / time ticket -->
  <tr><td style="padding:24px 32px 0;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="border:1px solid #e2e8f0;border-radius:14px;border-collapse:separate;overflow:hidden;">
      <tr>
        <td width="50%" style="background:#f8fafc;padding:18px 22px;border-right:1px dashed #cbd5e1;" valign="top">
          <p style="margin:0 0 6px;font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#94a3b8;">📅 &nbsp;Date</p>
          <p style="margin:0;font-size:18px;font-weight:800;color:#0f172a;line-height:1.3;">{_esc(date_s)}</p>
        </td>
        <td width="50%" style="background:#f8fafc;padding:18px 22px;" valign="top">
          <p style="margin:0 0 6px;font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#94a3b8;">🕒 &nbsp;Time</p>
          <p style="margin:0;font-size:18px;font-weight:800;color:#0f172a;line-height:1.3;">{_esc(time_s)}</p>
        </td>
      </tr>
    </table>
  </td></tr>

  {note_block}

  {qr_block}

  <!-- What to expect -->
  <tr><td style="padding:24px 32px 0;">
    <p style="margin:0 0 6px;font-size:11px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#94a3b8;">
      What to expect on your visit</p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{expect_rows}</table>
  </td></tr>

  <!-- Location -->
  <tr><td style="padding:22px 32px 0;">
    <div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:12px;padding:16px 18px;">
      <p style="margin:0 0 6px;font-size:11px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#4338ca;">
        📍 Getting here</p>
      <p style="margin:0 0 14px;font-size:14px;line-height:1.6;color:#1e293b;">{_esc(address)}</p>
      <a href="{_esc(nav_url)}" style="display:inline-block;margin:0 10px 8px 0;padding:11px 24px;background:#4f46e5;color:#ffffff;
         text-decoration:none;border-radius:10px;font-size:14px;font-weight:700;">🧭&nbsp; Start navigation</a>
      <a href="{_esc(view_url)}" style="display:inline-block;margin:0 0 8px;padding:11px 24px;background:#ffffff;color:#4338ca;
         border:1px solid #c7d2fe;text-decoration:none;border-radius:10px;font-size:14px;font-weight:700;">📍&nbsp; View on map</a>
    </div>
  </td></tr>

  <!-- Contact -->
  <tr><td style="padding:20px 32px 0;">
    <p style="margin:0;font-size:14px;line-height:1.6;color:#475569;">
      Anything you'd like to ask before you come? Call us at
      <a href="tel:{_esc(contact)}" style="color:#4f46e5;font-weight:700;text-decoration:none;">{_esc(contact)}</a> —
      we're happy to help.</p>
  </td></tr>

  <!-- Footer -->
  <tr><td style="padding:24px 32px 28px;">
    <p style="margin:0 0 2px;font-size:14px;color:#0f172a;">Warmly,</p>
    <p style="margin:0;font-size:14px;font-weight:700;color:#0f172a;">Aisha</p>
    <p style="margin:0;font-size:13px;color:#94a3b8;">Admissions · {_esc(uni)}</p>
  </td></tr>

</table>
<p style="margin:16px 0 0;font-size:11px;color:#94a3b8;">Sent {_esc(now_str)}</p>
</td></tr></table>
</body></html>"""

    return subject, text, html


async def send_campus_visit_email(
    *,
    to: str | list[str],
    candidate_name: str | None,
    visit_date: str,
    visit_time: str,
    campus_name: str | None = None,
    campus_address: str | None = None,
    contact_phone: str | None = None,
    map_url: str | None = None,
    notes: str | None = None,
    cc: str | list[str] | None = None,
    lead_id: str | None = None,
    include_pass: bool | None = None,
) -> dict[str, Any] | None:
    """Compose + send the campus-visit confirmation. Returns the send result
    (None on failure) — best-effort, never raises into the caller.

    When the campus pass is enabled (`include_pass`, defaulting to the
    `CAMPUS_VISIT_QR_PASS` setting) a per-visitor QR "entry pass" is generated
    and embedded inline; the visitor shows it to security at the gate. QR
    generation is itself best-effort — if it fails (e.g. `qrcode` not
    installed) the confirmation is still sent, just without the pass."""
    recipients = parse_recipients(to)
    if not recipients:
        log.warning("[campus-visit] no valid recipient — email skipped")
        return None

    s = get_settings()
    uni = (campus_name or s.university_short_name).strip()
    want_pass = s.campus_visit_qr_pass if include_pass is None else include_pass

    pass_id: str | None = None
    inline_images: dict[str, bytes] | None = None
    if want_pass:
        qr = build_campus_pass_qr(
            candidate_name=candidate_name,
            visit_date=visit_date,
            visit_time=visit_time,
            campus_name=uni,
            lead_id=lead_id,
        )
        if qr is not None:
            pass_id, _qr_text, png = qr
            inline_images = {_QR_CID: png}

    subject, text, html = compose_campus_visit_email(
        candidate_name=candidate_name,
        visit_date=visit_date,
        visit_time=visit_time,
        campus_name=campus_name,
        campus_address=campus_address,
        contact_phone=contact_phone,
        map_url=map_url,
        notes=notes,
        pass_id=pass_id,
        has_qr=inline_images is not None,
    )
    try:
        return await send_email(
            to=recipients,
            subject=subject,
            body_text=text,
            body_html=html,
            cc=cc,
            inline_images=inline_images,
        )
    except Exception as e:  # noqa: BLE001 — never break the caller (live call / endpoint)
        log.warning("[campus-visit] email send raised", err=str(e)[:200])
        return None
