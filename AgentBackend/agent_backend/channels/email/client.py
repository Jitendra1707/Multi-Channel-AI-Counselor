"""Email channel — outbound SMTP send.

Additive, OUTBOUND-ONLY channel with two jobs:
  - email candidates (brochures, follow-ups, confirmations)
  - email human counsellors a report on a good/best lead

Provider-agnostic: plain SMTP via the stdlib `smtplib` wrapped in a worker
thread so it never blocks the event loop (no new dependency). The SAME code
works with Gmail, Microsoft 365 / Outlook, Amazon SES, etc. — only the
`EMAIL_SMTP_*` config changes. Best-effort: with `EMAIL_SMTP_HOST` unset, sends
are disabled and logged (never a 500), mirroring the WhatsApp/voice outbound
paths so the channel mounts cleanly even before it's configured.

Provider quick-reference (set in .env):
  Gmail:         EMAIL_SMTP_HOST=smtp.gmail.com  EMAIL_SMTP_PORT=587
                 EMAIL_SMTP_SECURITY=starttls  EMAIL_SMTP_USERNAME=<you@gmail.com>
                 EMAIL_SMTP_PASSWORD=<16-char App Password>   (App Password required if 2FA)
  Microsoft 365: EMAIL_SMTP_HOST=smtp.office365.com  EMAIL_SMTP_PORT=587
                 EMAIL_SMTP_SECURITY=starttls   (SMTP AUTH must be ENABLED on the
                 mailbox — Microsoft disables basic auth by default; prefer Graph/ACS)
  Outlook.com:   EMAIL_SMTP_HOST=smtp-mail.outlook.com  EMAIL_SMTP_PORT=587  starttls
"""
from __future__ import annotations

import asyncio
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Iterable

from agent_backend.config import get_settings
from agent_backend.infra import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Provider profiles — EMAIL_PROVIDER switches the active SMTP identity without
# rewriting the generic EMAIL_SMTP_* block. "custom" preserves the original
# behaviour exactly, so existing configs keep working untouched.
# ---------------------------------------------------------------------------
_PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "gmail": {"host": "smtp.gmail.com", "port": 587, "security": "starttls"},
    "microsoft": {"host": "smtp.office365.com", "port": 587, "security": "starttls"},
}


@dataclass(frozen=True)
class SmtpProfile:
    provider: str
    host: str
    port: int
    security: str  # starttls | ssl | none
    username: str
    password: str
    sender: str    # From header


def resolve_smtp_profile() -> SmtpProfile:
    """Resolve the ACTIVE SMTP profile from settings.

    - custom    → the generic EMAIL_SMTP_* fields, verbatim (legacy behaviour).
    - gmail     → preset host/port/security; EMAIL_GMAIL_* credentials, falling
                  back to the generic EMAIL_SMTP_USERNAME/PASSWORD.
    - microsoft → preset host/port/security; EMAIL_MICROSOFT_* credentials,
                  same fallback.

    For preset providers the From defaults to that provider's username (NOT the
    global EMAIL_FROM) unless an explicit per-provider From is set — most relays
    reject a From that doesn't match the authenticated mailbox's domain.
    """
    s = get_settings()
    provider = (s.email_provider or "custom").strip().lower()

    if provider in _PROVIDER_PRESETS:
        preset = _PROVIDER_PRESETS[provider]
        if provider == "gmail":
            username = s.email_gmail_username or s.email_smtp_username
            password = s.email_gmail_password or s.email_smtp_password
            sender = s.email_gmail_from or username
        else:  # microsoft
            username = s.email_microsoft_username or s.email_smtp_username
            password = s.email_microsoft_password or s.email_smtp_password
            sender = s.email_microsoft_from or username
        return SmtpProfile(
            provider=provider,
            host=preset["host"], port=preset["port"], security=preset["security"],
            username=username, password=password, sender=sender,
        )

    # custom — exactly the original generic config.
    return SmtpProfile(
        provider="custom",
        host=s.email_smtp_host, port=s.email_smtp_port, security=s.email_smtp_security,
        username=s.email_smtp_username, password=s.email_smtp_password,
        sender=s.email_from or s.email_smtp_username,
    )


def _enabled() -> bool:
    return bool(resolve_smtp_profile().host)


def parse_recipients(value: str | Iterable[str] | None) -> list[str]:
    """Accept a single address, a comma/semicolon-separated string, or a list →
    a clean list of trimmed addresses (empties dropped)."""
    if value is None:
        return []
    if isinstance(value, str):
        parts: Iterable[str] = value.replace(";", ",").split(",")
    else:
        parts = value
    return [str(p).strip() for p in parts if p and str(p).strip()]


def _redact(addr: str) -> str:
    """Mask the local-part of an email — addresses are PII."""
    if "@" not in addr:
        return "***"
    local, _, domain = addr.partition("@")
    head = local[:2] if len(local) > 2 else local[:1]
    return f"{head}***@{domain}"


async def send_email(
    *,
    to: str | Iterable[str],
    subject: str,
    body_text: str,
    body_html: str | None = None,
    cc: str | Iterable[str] | None = None,
    reply_to: str | None = None,
    inline_images: dict[str, bytes] | None = None,
) -> dict[str, Any] | None:
    """Send an email via the configured SMTP server. Best-effort.

    `to` / `cc` accept a single address, a comma-separated string, or a list.
    `inline_images` maps a Content-ID (the `cid` referenced in the HTML, e.g.
    "campus-qr" for `<img src="cid:campus-qr">`) to raw PNG bytes; each is
    attached to the HTML part as an inline image so it renders in clients that
    strip remote/data-URI images (Gmail, Outlook). Ignored when there's no HTML
    part. Returns a small dict on success, None on failure / when disabled
    (logged). Never raises into the caller.
    """
    s = get_settings()
    profile = resolve_smtp_profile()
    if not profile.host:
        log.warning(
            "[email] outbound disabled — no SMTP host for active provider",
            provider=profile.provider,
        )
        return None

    to_list = parse_recipients(to)
    cc_list = parse_recipients(cc)
    if not to_list and not cc_list:
        log.warning("[email] no recipients — send skipped")
        return None

    if not profile.sender:
        log.warning(
            "[email] no From/username for active provider — send skipped",
            provider=profile.provider,
        )
        return None

    effective_reply_to = reply_to or (s.email_reply_to or None)

    def _send() -> dict[str, Any]:
        msg = EmailMessage()
        msg["From"] = profile.sender
        msg["To"] = ", ".join(to_list)
        if cc_list:
            msg["Cc"] = ", ".join(cc_list)
        if effective_reply_to:
            msg["Reply-To"] = effective_reply_to
        msg["Subject"] = subject
        msg.set_content(body_text or "")
        if body_html:
            msg.add_alternative(body_html, subtype="html")
            # Attach inline images to the HTML part so `<img src="cid:...">`
            # resolves in clients that strip remote/data-URI images. The HTML
            # alternative is the last sub-part added above.
            if inline_images:
                html_part = msg.get_payload()[-1]
                for cid, data in inline_images.items():
                    if not data:
                        continue
                    html_part.add_related(
                        data, maintype="image", subtype="png", cid=f"<{cid}>"
                    )

        recipients = to_list + cc_list
        host, port = profile.host, profile.port
        ctx = ssl.create_default_context()

        if profile.security == "ssl":
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=s.email_timeout_s) as smtp:
                if profile.username:
                    smtp.login(profile.username, profile.password)
                smtp.send_message(msg, to_addrs=recipients)
        else:
            with smtplib.SMTP(host, port, timeout=s.email_timeout_s) as smtp:
                smtp.ehlo()
                if profile.security == "starttls":
                    smtp.starttls(context=ctx)
                    smtp.ehlo()
                if profile.username:
                    smtp.login(profile.username, profile.password)
                smtp.send_message(msg, to_addrs=recipients)
        return {"to": to_list, "cc": cc_list, "subject": subject}

    try:
        result = await asyncio.to_thread(_send)
    except Exception as e:  # noqa: BLE001
        # SMTPAuthenticationError on M365 usually means SMTP AUTH is disabled on
        # the mailbox; on Gmail it usually means a real password was used instead
        # of an App Password. Surfaced as a logged failure, never a crash.
        log.warning(
            "[email] send failed",
            provider=profile.provider,
            host=profile.host,
            to=[_redact(a) for a in to_list],
            err=str(e)[:200],
        )
        return None

    log.info(
        "[email] sent",
        provider=profile.provider,
        to=[_redact(a) for a in to_list],
        cc=len(cc_list),
        subject=subject[:80],
    )
    return result
