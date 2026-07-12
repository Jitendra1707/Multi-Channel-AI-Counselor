"""Opener variant library — instant, context-aware, deterministically varied.

Why this module exists
----------------------
The previous opener was a single hard-coded f-string:

    f"Hi {first_name}? Aisha here from {short_name} — got a minute?"

Every call sounded identical. The reason it WAS hardcoded: a previous version
generated the opener via an LLM, which took ~2s before the candidate heard
anything — a deal-breaker on PSTN (the "hello? hello?" pattern).

We keep the template-speed advantage (<5ms render, total pickup-to-first-word
~200-500ms) but introduce REAL variety by composing the opener from three
slots — greeting + middle + permission-ask — each drawn from a pool that
depends on (time-of-day, lead status, language). Selection is deterministic
per (lead, calendar-day) — same lead called twice the same day gets the
same opener (reproducible / debuggable); different leads or a re-call on a
new day get different ones.

Production voice agents (Retell, Vapi, Bland) all do template-based first
messages for this exact latency reason. The dynamism comes from variety,
not from per-call LLM generation.

Layer 2 (future)
----------------
This module exposes a `get_prewarmed_opener(lead_id)` hook for a future
pre-warmed-LLM path: the dial endpoint can kick off an LLM call during the
3-8 s of carrier ringing; if the result lands before pickup, agent_bridge
uses it instead of the template. Not implemented yet — just a seam.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from agent_backend.data.leads import Lead, LeadStatus
from agent_backend.infra import get_logger

if TYPE_CHECKING:
    from agent_backend.llm_agent.session import Session

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Bot identity (the name the bot introduces itself with).
# TODO(next): read this from the active persona JSON (`identity_name`) so it
# tracks the persona file. For now mirroring the persona's "Aisha".
# ---------------------------------------------------------------------------
_BOT_NAME = "Aisha"

# India Standard Time offset (UTC+5:30). Leads are India-based so time-of-day
# greeting buckets work off IST regardless of where the server runs.
_IST = timezone(timedelta(hours=5, minutes=30))


# ---------------------------------------------------------------------------
# Greeting pools — picked by time-of-day bucket in IST.
# ---------------------------------------------------------------------------
_GREETINGS_BY_BUCKET: dict[str, list[str]] = {
    "morning":   ["Good morning", "Morning", "Hi"],         # 05:00–11:59 IST
    "afternoon": ["Hi", "Hello", "Hey"],                     # 12:00–16:59 IST
    "evening":   ["Hey", "Hi", "Good evening"],              # 17:00–21:59 IST
    "off_hours": ["Hi"],                                     # 22:00–04:59 IST — we shouldn't call here
}


# ---------------------------------------------------------------------------
# Middle pools — the "who I am, why I'm calling" body. One pool per
# (language, status). Each line uses `{name}` and `{uni}` slots.
#
# Style rules I followed when authoring:
#   - Each variant ≤ 12 words so the full opener stays ≤ 15 words.
#   - Use the candidate's FIRST name only (full name sounds robotic).
#   - Vary punctuation deliberately ("Ramesh?" vs "Ramesh,") — TTS prosody
#     reads them with different intonation, which compounds the variety
#     beyond the literal text difference.
# ---------------------------------------------------------------------------
_EN_NEW: list[str] = [
    "{name}? {bot} here from {uni}",
    "{name}, {bot} calling from {uni} admissions",
    "{name}, this is {bot} from {uni}",
    "{name}? {bot} from {uni} here",
    "{name}, {bot} here from {uni}'s counselling team",
]

_EN_WELCOMED: list[str] = [
    "{name}, {bot} from {uni}, WhatsApp follow-up",
    "{name}? {bot} from {uni}, after our chat",
    "{name}, {bot} here from {uni} counselling",
    "{name}? {bot} from {uni}, circling back",
]

_EN_SCHEDULED: list[str] = [
    "{name}, {bot} from {uni}, about your slot",
    "{name}? {bot} from {uni}, right on time",
    "{name}, {bot} from {uni}, scheduled chat",
    "{name}? {bot} from {uni}, as we agreed",
]

_EN_CALLED: list[str] = [
    "{name}, {bot} again from {uni}, quick follow-up",
    "{name}? {bot} here, calling back from {uni}",
    "{name}, {bot} from {uni}, circling back",
]

_HI_NEW: list[str] = [
    "{name}, mai {bot} bol rahi hoon {uni} se",
    "{name}? {bot} here from {uni}",          # Hinglish hybrid — common
    "{name}, {uni} se {bot} bol rahi hoon",
    "{name}, {bot} hoon mai {uni} ki counselling team se",
]

_HI_WELCOMED: list[str] = [
    "{name}, {bot} bol rahi hoon {uni} se, WhatsApp ke baad",
    "{name}? {bot} from {uni}, WhatsApp follow-up",
]

_HI_SCHEDULED: list[str] = [
    "{name}, {bot} from {uni}, scheduled call ke liye",
    "{name}? {bot} bol rahi hoon {uni} se, scheduled call",
]

# No-name pools — used when we DON'T know the candidate's name yet (unknown
# inbound). Never address them as "Unknown"; just introduce ourselves. The
# brain then asks their name early in the conversation (see LEAD PROFILE).
_EN_NONAME: list[str] = [
    "this is {bot} from {uni} admissions",
    "it's {bot} here from {uni}",
    "this is {bot} from {uni}'s counselling team",
]

_HI_NONAME: list[str] = [
    "mai {bot} bol rahi hoon {uni} se",
    "this is {bot} from {uni}",
]


# Status × language → pool. Falls back to NEW pool if status missing.
_MIDDLES: dict[tuple[str, LeadStatus], list[str]] = {
    ("en", LeadStatus.NEW):        _EN_NEW,
    ("en", LeadStatus.WELCOMED):   _EN_WELCOMED,
    ("en", LeadStatus.SCHEDULING): _EN_WELCOMED,    # SCHEDULING is mid-pick; reuse WELCOMED tone
    ("en", LeadStatus.SCHEDULED):  _EN_SCHEDULED,
    ("en", LeadStatus.CALLED):     _EN_CALLED,
    ("en", LeadStatus.FOLLOWUP):   _EN_CALLED,
    ("hi", LeadStatus.NEW):        _HI_NEW,
    ("hi", LeadStatus.WELCOMED):   _HI_WELCOMED,
    ("hi", LeadStatus.SCHEDULING): _HI_WELCOMED,
    ("hi", LeadStatus.SCHEDULED):  _HI_SCHEDULED,
    ("hi", LeadStatus.CALLED):     _HI_NEW,         # no dedicated Hindi follow-up pool yet
    ("hi", LeadStatus.FOLLOWUP):   _HI_NEW,
}


# ---------------------------------------------------------------------------
# Permission-ask pools — "got a minute?" variants. Composed at the end.
# ---------------------------------------------------------------------------
_ASKS_EN: list[str] = [
    "got a minute?",
    "do you have a sec?",
    "is now a good time?",
    "got a couple of minutes?",
    "have a moment?",
]

_ASKS_HI: list[str] = [
    "ek minute hai?",
    "abhi baat ho sakti hai?",
    "do minute hain?",
    "ek minute time hai aapke paas?",
]


# ---------------------------------------------------------------------------
# INBOUND opener pools — the candidate called US, so we NEVER ask "got a
# minute?". We warmly thank them for calling and ask how we can help, then let
# them lead. `{name}` filled when known; the no-name variants introduce the
# university instead. `{uni}` = short university name, `{bot}` = bot name.
# ---------------------------------------------------------------------------
_INBOUND_EN_NAMED: list[str] = [
    "Thanks for calling, {name} — how can I help you today?",
    "Hi {name}, thanks for reaching out — what can I help you with?",
    "Hello {name}, thanks for calling {uni} admissions — how can I help?",
    "{name}, lovely to hear from you — how can I help today?",
]

# No-name inbound: ask the caller's name AND invite their query in ONE line, so
# they naturally open with "I'm <name>, I wanted to ask about…". This gets the
# name into the conversation up front (the agent then uses it throughout and it's
# persisted after the call) WITHOUT a separate "what's your name?" turn.
_INBOUND_EN_NONAME: list[str] = [
    "Thank you for calling {uni} admissions — may I know who I'm speaking with, and how can I help you today?",
    "Hi, you've reached {uni} admissions — this is {bot}. May I have your name, and what can I help you with?",
    "Thanks for calling {uni} — I'm {bot}. Who do I have the pleasure of speaking with, and how can I help?"
]

_INBOUND_HI_NAMED: list[str] = [
    "Call karne ke liye shukriya, {name} — mai aapki kaise madad kar sakti hoon?",
    "Hello {name}, {uni} admissions me call karne ke liye dhanyavaad — kaise help karun?",
]

_INBOUND_HI_NONAME: list[str] = [
    "{uni} admissions me call karne ke liye shukriya — mai aapka naam jaan sakti hoon, aur kaise madad karun?",
    "Namaste, aap {uni} admissions par pahunche hain — aapka naam kya hai, aur mai kaise help kar sakti hoon?",
    "{uni} admissions me call karne ke liye dhanyavaad — pehle aapka naam bata dijiye, phir kaise madad karun?",
]


# ---------------------------------------------------------------------------
# Layer 2 hook — pre-warmed LLM opener cache (not used yet).
# ---------------------------------------------------------------------------
_PREWARMED_OPENERS: dict[str, str] = {}


def stash_prewarmed_opener(lead_id: str, opener: str) -> None:
    """Layer 2: dial endpoint stores an LLM-generated opener here while
    the carrier is still ringing. agent_bridge consumes it if it arrives
    before pickup; otherwise falls back to the template.
    Not used yet — seam reserved for the next iteration.
    """
    if lead_id and opener:
        _PREWARMED_OPENERS[lead_id] = opener


def get_prewarmed_opener(lead_id: str) -> str | None:
    """One-shot retrieve + clear of a pre-warmed opener for `lead_id`."""
    return _PREWARMED_OPENERS.pop(lead_id, None) if lead_id else None


# ---------------------------------------------------------------------------
# Public — the one function agent_bridge calls.
# ---------------------------------------------------------------------------
def render_opener(session: "Session") -> str:
    """Return the opener line for this session.

    Order of precedence:
      0. A persona-defined opener (`opener.spoken` in the active persona JSON)
         for non-counsellor personas — e.g. the avatar_video director presenter
         introduces its capabilities and hands control to the user. Channel-aware
         so the counsellor template path below is untouched.
      1. A pre-warmed LLM opener stashed for `session.lead_id` (Layer 2)
      2. A template assembled from greeting + middle + ask, picked
         deterministically per (lead, calendar-day-IST).
    """
    # Layer 0 — persona-driven opener (capability intro, no "got a minute?").
    persona_opener = _persona_opener(session)
    if persona_opener:
        log.info("[opener] using persona opener", session=session.short(), text=persona_opener)
        return persona_opener

    # Layer 2 path — empty today, ready for later.
    if session.lead_id:
        prewarmed = get_prewarmed_opener(session.lead_id)
        if prewarmed:
            log.info(
                "[opener] using pre-warmed LLM opener",
                session=session.short(),
                text=prewarmed,
            )
            return prewarmed

    # Layer 1 — template assembly.
    lead = _load_lead(session.lead_id)
    name = _first_name(lead)
    uni = _university_short_name()
    lang = (lead.language_preference if lead else "en") or "en"
    if lang not in ("en", "hi"):
        # Tamil, Telugu, etc. fall back to English pools today. We can add
        # dedicated pools per language later as we collect them.
        lang = "en"
    status = lead.status if lead else LeadStatus.NEW

    bucket = _time_bucket_ist()
    salt = _salt_for(lead)

    # INBOUND — the candidate called US. Never ask "got a minute?"; thank them for
    # calling and ask how we can help. Composed from a single pool (greeting + ask
    # are baked into each line), then return early — the outbound 3-slot assembly
    # below is skipped entirely.
    if session.direction == "inbound":
        if name:
            pool = _INBOUND_HI_NAMED if lang == "hi" else _INBOUND_EN_NAMED
        else:
            pool = _INBOUND_HI_NONAME if lang == "hi" else _INBOUND_EN_NONAME
        inbound = _deterministic_pick(pool, salt + ":in").format(
            name=name, uni=uni, bot=_BOT_NAME,
        )
        log.info(
            "[opener] rendered (inbound)",
            session=session.short(), lang=lang, text=inbound,
        )
        return inbound

    greetings = _GREETINGS_BY_BUCKET.get(bucket, _GREETINGS_BY_BUCKET["afternoon"])
    # No name yet → introduce ourselves without a name (never "Unknown").
    if name:
        middles = _MIDDLES.get((lang, status), _EN_NEW)
    else:
        middles = _HI_NONAME if lang == "hi" else _EN_NONAME
    asks      = _ASKS_HI if lang == "hi" else _ASKS_EN

    # Use DIFFERENT salts per slot so we don't get "always the 1st greeting +
    # always the 1st middle" syndrome — each slot independently spans its pool.
    greeting = _deterministic_pick(greetings, salt + ":g")
    middle   = _deterministic_pick(middles,   salt + ":m").format(
        name=name, uni=uni, bot=_BOT_NAME,
    )
    ask      = _deterministic_pick(asks,      salt + ":a")

    opener = f"{greeting} {middle} — {ask}"

    log.info(
        "[opener] rendered (template)",
        session=session.short(),
        bucket=bucket,
        lang=lang,
        status=status.value if status else None,
        text=opener,
    )
    return opener


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _persona_opener(session: "Session") -> str | None:
    """A ready-to-speak opener from the active persona's JSON (`opener.spoken`),
    if one is defined. Used by personas that introduce their capabilities and
    let the user drive (e.g. the avatar_video director presenter) rather than
    the counsellor's "got a minute?" template. Channel-aware via the same
    channel→persona mapping the prompt uses, so the counsellor path is unchanged
    (the counsellor persona simply has no `opener.spoken`)."""
    try:
        from agent_backend.llm_agent.agent import _persona_name_for_channel
        from agent_backend.llm_agent.identity import get_identity

        identity = get_identity(_persona_name_for_channel(session.channel))
        op = identity.get("opener")
        if isinstance(op, dict):
            spoken = op.get("spoken")
            if isinstance(spoken, str) and spoken.strip():
                return spoken.strip()
    except Exception as e:  # noqa: BLE001
        log.debug("[opener] persona opener lookup failed", err=str(e)[:160])
    return None


def _load_lead(lead_id: str | None) -> Lead | None:
    if not lead_id:
        return None
    from agent_backend.data.leads import LeadRepo
    try:
        return LeadRepo.get().get_by_id(lead_id)
    except Exception:  # noqa: BLE001
        return None


def _first_name(lead: Lead | None) -> str:
    """The candidate's first name, scrubbed of trailing punctuation. Fallback
    to a neutral "there" when we don't know the name yet."""
    if lead and lead.full_name:
        head = lead.full_name.split()[0].strip("?!.,'\"")
        if head and head.lower() != "unknown":
            return head
    return ""  # unknown → render_opener uses a no-name opener (never "Unknown")


def _university_short_name() -> str:
    """Short name of the university spoken in the opener.

    Sourced from the `university_short_name` setting — the KB/RAG is the source
    of truth for university FACTS; this is just the TTS-friendly spoken name for
    the bot-speaks-first opener (there is no university.json anymore). Strips any
    parenthetical alias, e.g. 'RGUKT Basar (IIIT Basar)' → 'RGUKT Basar'.
    """
    from agent_backend.config import get_settings

    short = get_settings().university_short_name or "the university"
    return re.sub(r"\s*\([^)]*\)\s*", "", short).strip()


def _time_bucket_ist() -> str:
    """Current time-of-day bucket in IST."""
    hour = datetime.now(timezone.utc).astimezone(_IST).hour
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "off_hours"


def _salt_for(lead: Lead | None) -> str:
    """Salt for the deterministic picker. Combines lead identity + the IST
    calendar day, so the SAME lead gets the SAME opener if called multiple
    times the SAME day (consistency), but a re-call on a different day
    likely picks a different variant (variety over time)."""
    today = datetime.now(timezone.utc).astimezone(_IST).date().toordinal()
    lead_part = lead.lead_id if lead else "anon"
    return f"{lead_part}:{today}"


def _deterministic_pick(pool: list[str], salt: str) -> str:
    """Pick one element from `pool` keyed by hash(salt).

    Why hash-based instead of random.choice?
      - Reproducible: same salt → same pick. Helps debugging ("why did this
        lead get that opener?").
      - Operations-friendly: replaying a call's logs gives the same opener.
      - No PRNG state to seed / share across processes.
    """
    if not pool:
        return ""
    h = int(hashlib.sha256(salt.encode("utf-8")).hexdigest(), 16)
    return pool[h % len(pool)]


__all__ = [
    "render_opener",
    "stash_prewarmed_opener",
    "get_prewarmed_opener",
]
