"""Campus-visit pass QR code — generate the entry-pass shown to security.

A self-contained, best-effort helper. The campus-visit confirmation email
embeds a QR "campus pass" that the visitor shows at the gate; security scans it
to read a human-verifiable pass: who is visiting, when, and a short pass id tied
to the lead. It carries NO secrets — it's a verification convenience, not an
auth token — so it's safe to render in an email.

`qrcode[pil]` is lazy-imported here so the email channel keeps mounting even if
the package isn't installed: `build_campus_pass_qr` simply returns None and the
email is sent without the pass (the rest of the confirmation is unaffected).
"""
from __future__ import annotations

import hashlib

from agent_backend.infra import get_logger

log = get_logger(__name__)


def _pass_id(*, lead_id: str | None, candidate_name: str | None, visit_date: str) -> str:
    """A short, stable, human-quotable pass id. Deterministic for the same
    (lead, name, date) so a re-sent confirmation shows the SAME pass. Derived,
    not secret — security uses it to look the visitor up, not to authenticate."""
    seed = f"{(lead_id or '').strip()}|{(candidate_name or '').strip().lower()}|{(visit_date or '').strip()}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8].upper()
    return f"SNU-{digest}"


def build_campus_pass_payload(
    *,
    candidate_name: str | None,
    visit_date: str,
    visit_time: str,
    campus_name: str,
    lead_id: str | None = None,
) -> tuple[str, str]:
    """Return (pass_id, qr_text). `qr_text` is the multi-line, plain-text
    payload encoded into the QR — readable by any scanner, no app required."""
    pass_id = _pass_id(lead_id=lead_id, candidate_name=candidate_name, visit_date=visit_date)
    name = (candidate_name or "Guest").strip() or "Guest"
    lines = [
        "CAMPUS VISIT PASS",
        f"Pass: {pass_id}",
        f"Visitor: {name}",
        f"Campus: {campus_name}",
        f"When: {visit_date} {visit_time}".strip(),
    ]
    if lead_id:
        lines.append(f"Ref: {lead_id}")
    lines.append("Show this pass to security at the gate.")
    return pass_id, "\n".join(lines)


def build_campus_pass_qr(
    *,
    candidate_name: str | None,
    visit_date: str,
    visit_time: str,
    campus_name: str,
    lead_id: str | None = None,
) -> tuple[str, str, bytes] | None:
    """Generate the campus-pass QR.

    Returns (pass_id, qr_text, png_bytes), or None if the QR can't be produced
    (e.g. `qrcode` not installed) — callers treat None as "no pass", never an
    error. The PNG is a black-on-white QR with a quiet border, sized for crisp
    rendering in an email and on a phone screen at the gate.
    """
    pass_id, qr_text = build_campus_pass_payload(
        candidate_name=candidate_name,
        visit_date=visit_date,
        visit_time=visit_time,
        campus_name=campus_name,
        lead_id=lead_id,
    )
    try:
        import io

        import qrcode
        from qrcode.constants import ERROR_CORRECT_M

        qr = qrcode.QRCode(
            version=None,  # auto-fit to payload
            error_correction=ERROR_CORRECT_M,
            box_size=8,
            border=3,
        )
        qr.add_data(qr_text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#0f172a", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return pass_id, qr_text, buf.getvalue()
    except Exception as e:  # noqa: BLE001 — never break the email over a missing QR
        log.warning("[campus-visit] QR generation skipped", err=str(e)[:200])
        return None
