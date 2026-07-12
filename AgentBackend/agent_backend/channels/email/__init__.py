"""Email channel — outbound SMTP (candidate emails + counsellor lead reports).

Outbound-only, additive channel — it does NOT touch the brain or any existing
channel. See `client.py` (provider-agnostic SMTP send) and `routes.py`:
  - POST /api/email/send         — generic send (candidate emails, follow-ups)
  - POST /api/email/report-lead  — email a good/best-lead report to counsellors

Works with any SMTP server (Gmail, Microsoft 365 / Outlook, Amazon SES, …) —
only the `EMAIL_SMTP_*` settings change. Mounted in `main.py`; with
`EMAIL_SMTP_HOST` unset the routes exist but sends are disabled (logged).
"""

from agent_backend.channels.email.routes import email_router as router

__all__ = ["router"]
