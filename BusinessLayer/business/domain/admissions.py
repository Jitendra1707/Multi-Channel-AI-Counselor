"""Admissions next-steps policy — does a course require an entrance exam?

When an application is COMPLETE and the fee is PAID, the candidate gets one of two
WhatsApp messages (SNU Counsellor Handbook + the admissions requirement):

  1. Course HAS an entrance exam  → message the exam phases + slot-booking link.
  2. Course has NO entrance exam   → message the token-amount link to confirm the
                                     admission booking.

The mapping below is deliberately tiny and editable — it keys off a normalised
substring of the lead's `course_interest`. Anything not matched falls back to
`DEFAULT_REQUIRES_ENTRANCE_EXAM`. Keep it here (one place) so the action worker
just asks `requires_entrance_exam(course)` and the message text comes from
`next_steps_message(...)`.
"""

from __future__ import annotations

# Substrings (lowercased) of course_interest → requires an entrance exam?
# Programs in scope per the handbook are B.Tech and BBA. Tune freely.
_ENTRANCE_EXAM_BY_COURSE: dict[str, bool] = {
    # B.Tech / engineering streams → entrance exam (e.g. SNUEEE).
    "b.tech": True,
    "btech": True,
    "b tech": True,
    "engineering": True,
    "cse": True,
    "ece": True,
    "mechanical": True,
    "civil": True,
    # BBA / management → no entrance exam → token-amount booking.
    "bba": False,
    "management": False,
    "business": False,
    "b.com": False,
    "bcom": False,
}

#: Used when the course is unknown / unmatched. Defaulting to True (exam) is the
#: safer side — it routes to the slot-booking message rather than asking for money
#: when we're unsure.
DEFAULT_REQUIRES_ENTRANCE_EXAM = True

# Editable copy for the two messages. {url} is filled from settings (or left as a
# placeholder line if no link is configured yet, so the message still sends).
_ENTRANCE_EXAM_BODY = (
    "Congratulations on completing your application and payment! 🎉 "
    "The next step is the entrance examination. It runs in phases — and you can "
    "book your preferred exam slot here: {url} "
    "Reply here if you'd like help choosing a slot."
)
_TOKEN_PAYMENT_BODY = (
    "Congratulations on completing your application and payment! 🎉 "
    "Your programme has no entrance exam — to confirm and lock your admission "
    "booking, just pay the token amount here: {url} "
    "Reply here if you have any questions."
)

_NO_LINK_PLACEHOLDER = "(our team will share the link with you shortly)"


def requires_entrance_exam(course_interest: str | None) -> bool:
    """True if the course needs an entrance exam. Substring match on the lead's
    course_interest; unknown/blank → DEFAULT_REQUIRES_ENTRANCE_EXAM."""
    c = (course_interest or "").strip().lower()
    if not c:
        return DEFAULT_REQUIRES_ENTRANCE_EXAM
    for key, needs in _ENTRANCE_EXAM_BY_COURSE.items():
        if key in c:
            return needs
    return DEFAULT_REQUIRES_ENTRANCE_EXAM


def next_steps_message(
    *, course_interest: str | None, exam_url: str | None, token_url: str | None
) -> tuple[str, str, str]:
    """Return (kind, template_key, body) for the post-payment next-steps message.

    kind          "entrance_exam" | "token_payment" — which branch fired.
    template_key  the approved-template key to try out-of-window.
    body          the in-window free-form text (also the template {{2}} value).
    """
    if requires_entrance_exam(course_interest):
        url = (exam_url or "").strip() or _NO_LINK_PLACEHOLDER
        return "entrance_exam", "admission_entrance_exam", _ENTRANCE_EXAM_BODY.format(url=url)
    url = (token_url or "").strip() or _NO_LINK_PLACEHOLDER
    return "token_payment", "admission_token_payment", _TOKEN_PAYMENT_BODY.format(url=url)
