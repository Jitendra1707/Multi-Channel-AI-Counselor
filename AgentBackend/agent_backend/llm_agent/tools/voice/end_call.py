"""end_call — politely hang up when the conversation has reached a natural
conclusion, AFTER the bot's closing line has actually finished playing.

Human-like wiring (event-driven, NOT a blind timer):
  - The LLM delivers a warm, context-relevant closing line in the SAME turn,
    THEN invokes this tool (the tool description + system directive enforce this).
  - The tool returns IMMEDIATELY to the model (so it stops generating).
  - A background task watches the per-call event bus and waits for the goodbye
    to be SPOKEN to the user: it waits for the bot to START speaking the closing
    line, then for it to STOP (BotSpeakingEvent speaking=False = audio finished
    playing on the candidate's device), adds a short tail so the last word isn't
    clipped, and ONLY THEN tears down the call/leg. A hard safety cap guarantees
    we never hang the line open if the speaking events don't arrive.
  - This makes the hangup feel human: the candidate hears the full sign-off, and
    the line drops the moment it's done — never mid-word, never an awkward gap.

When the LLM should call this (codified in the system prompt's playbook):
  - Candidate said goodbye / "thanks bye" / "talk later"
  - Candidate declined the CTA and clearly indicated end-of-call
  - The next step has been committed AND wrapped up
  - Candidate disengaged (multiple one-word replies in a row)

When it should NOT:
  - Mid-discovery or mid-pitch
  - Candidate is asking questions
  - CTA hasn't been offered yet
"""
from __future__ import annotations

import asyncio

from langchain_core.tools import BaseTool, tool

from agent_backend.infra import get_logger
from agent_backend.llm_agent.tools._base import ToolContext

log = get_logger(__name__)


# Backstop guard — ALLOW-BY-DEFAULT. The model is trusted to end the call; this
# only refuses the ONE narrow, repeatedly-observed mistake: hanging up the moment
# the candidate AGREES to a next-step ("yes, send it on WhatsApp") and the model
# mistakes that acceptance for a goodbye. Accepting a send is mid-conversation,
# not the end.
#
# CRITICAL: this gate must NEVER block a real end. An explicit stop request
# ("please stop the call", "I have to go", "hang up"), a goodbye, a decline, or
# disengagement ALL pass straight through. We block ONLY when the reason reads as
# a bare next-step acceptance AND carries no end intent — and we resolve any doubt
# in favour of ALLOWING the hangup. We can't see the chat history at the tool
# boundary, so we gate purely on the model-stated `reason`.

# An acceptance-shaped reason: "candidate agreed to a WhatsApp follow-up", etc.
_ACCEPTANCE_HINTS = (
    "agreed to", "accepted", "said yes", "wants the", "asked me to send",
    "asked to send", "requested whatsapp", "requested the", "send it",
    "send the", "send them", "sending", "share the", "wants details sent",
    "wants info sent", "wants the document", "to whatsapp", "on whatsapp",
    "via whatsapp", "book the visit", "booked the visit",
)
# Any genuine end intent. If ANY of these appear, the hangup is allowed even if
# an acceptance phrase is also present. Kept broad on purpose — better to let a
# borderline end through than to trap the user on a call they asked to leave.
_END_SIGNAL_HINTS = (
    "bye", "goodbye", "stop the call", "stop call", "end the call", "end call",
    "hang up", "hangup", "wants to end", "asked to end", "wants to stop",
    "asked to stop", "wants to leave", "has to go", "needs to go", "have to go",
    "got to go", "busy", "in a hurry", "no time", "later", "call back",
    "ring tomorrow", "call tomorrow", "no thanks", "no thank", "nothing else",
    "nothing more", "no more", "nothing further", "no other", "no further",
    "that's all", "thats all", "satisfied", "happy with", "declined",
    "not interested", "no interest", "said no", "disengaged", "one-word",
    "one word", "all set", "all good", "done for now", "finished", "wrap",
    "anything else", "no questions", "ready to hang up",
)


def _is_premature_end(reason: str) -> bool:
    """True ONLY for the narrow bad case: the reason reads like a bare next-step
    ACCEPTANCE (e.g. "candidate agreed to a WhatsApp follow-up") with NO end
    intent anywhere in it. Everything else — explicit stop requests, goodbyes,
    declines, disengagement, or an unrecognised phrasing — returns False and the
    hangup proceeds.

    Allow-by-default: a single end-signal keyword clears the gate, so a genuine
    end is never blocked. We only catch the "they said yes to WhatsApp → hang up"
    jump that the prompt alone doesn't reliably prevent."""
    r = (reason or "").lower()
    if any(sig in r for sig in _END_SIGNAL_HINTS):
        return False  # genuine end intent present → allow the hangup
    return any(hint in r for hint in _ACCEPTANCE_HINTS)


# Short tail AFTER the bot-stopped-speaking signal — covers playout/jitter buffer
# so the very last word lands on the candidate's device before the line drops.
_TAIL_S = 0.8
_AVATAR_TAIL_S = 1.5            # Simli lip-sync drains a beat slower than PSTN.

# Hard ceiling on the whole wait (start + speak + tail). The line ALWAYS drops by
# this point even if the speaking events never arrive (e.g. bus detached).
_MAX_WAIT_S = 20.0
# Fallback fixed grace used only when there's no event bus to observe.
_FALLBACK_GRACE_S = 4.0
_AVATAR_FALLBACK_GRACE_S = 8.0


async def _wait_for_goodbye_finished(conversation_id: str, *, is_avatar: bool) -> str:
    """Block until the bot's closing line has finished playing to the user, using
    the per-call event bus. Returns a short reason string for logging.

    Sequence: wait for speaking=True (goodbye started) → speaking=False (finished)
    → short tail. Falls back to a fixed grace if the bus / events are unavailable,
    and is bounded by _MAX_WAIT_S so it can never block teardown indefinitely.
    """
    BotSpeakingEvent, get_or_create_bus = _bus_api(is_avatar)
    if BotSpeakingEvent is None or get_or_create_bus is None:
        await asyncio.sleep(_AVATAR_FALLBACK_GRACE_S if is_avatar else _FALLBACK_GRACE_S)
        return "fixed-grace (no bus api)"

    tail = _AVATAR_TAIL_S if is_avatar else _TAIL_S
    try:
        bus = await get_or_create_bus(conversation_id)
    except Exception:  # noqa: BLE001
        await asyncio.sleep(_AVATAR_FALLBACK_GRACE_S if is_avatar else _FALLBACK_GRACE_S)
        return "fixed-grace (bus unavailable)"

    import time as _time

    async def _watch() -> str:
        started = False
        # RACE GUARD: the goodbye TTS can START before this subscriber is
        # attached (tool returns → stream ends → TTS plays, all fast). If we
        # only honoured speaking=False AFTER a seen start, a missed start would
        # make us wait to the cap. So: a speaking=False also ends the wait once a
        # brief floor has elapsed (enough for a one-line goodbye to be playing),
        # covering the "subscribed mid/just-after speech" case. A start we DO see
        # short-circuits the floor.
        t0 = _time.monotonic()
        floor_s = 1.5
        async for ev in bus.subscribe(types=(BotSpeakingEvent,)):
            speaking = bool(getattr(ev, "speaking", False))
            if speaking:
                started = True
            else:
                if started or (_time.monotonic() - t0) >= floor_s:
                    await asyncio.sleep(tail)
                    return "goodbye finished" if started else "goodbye finished (stop after floor)"
                # else: a stale stop right after subscribing → keep waiting for
                # the real goodbye to start.
        return "bus closed before goodbye finished"

    try:
        return await asyncio.wait_for(_watch(), timeout=_MAX_WAIT_S)
    except asyncio.TimeoutError:
        return f"max-wait {_MAX_WAIT_S:.0f}s hit"


def _bus_api(is_avatar: bool):
    """(BotSpeakingEvent, get_or_create_bus) for the right channel, or (None,None)."""
    try:
        if is_avatar:
            from agent_backend.channels.avatar_video.events import (
                BotSpeakingEvent,
                get_or_create_bus,
            )
        else:
            from agent_backend.channels.voice.events import (
                BotSpeakingEvent,
                get_or_create_bus,
            )
        return BotSpeakingEvent, get_or_create_bus
    except Exception:  # noqa: BLE001
        return None, None


# Per-call "hangup already scheduled" guard. Both the end_call TOOL and the
# agent-bridge GOODBYE SAFETY NET (which fires when the bot speaks a sign-off but
# the model forgot to invoke the tool) funnel through schedule_graceful_hangup();
# this set makes a second call for the same conversation a no-op so we never
# double-tear-down a leg.
_HANGUP_SCHEDULED: set[str] = set()


def hangup_pending(conversation_id: str) -> bool:
    """True if a graceful hangup is already scheduled for this conversation —
    i.e. the call is in its closing window, waiting for the goodbye to finish
    playing before the line drops.

    Used by the silence-responder to STOP injecting re-engagement turns once the
    call is ending: otherwise an 8s silence (the gap while the goodbye plays /
    the model forgot to speak one) fires a 'still there?' nudge that talks over
    the teardown — the user already said they were done."""
    return conversation_id in _HANGUP_SCHEDULED


def schedule_graceful_hangup(
    *, conversation_id: str, call_id: str | None, is_avatar: bool, reason: str,
) -> bool:
    """Schedule the graceful, goodbye-aware teardown of a call leg.

    Waits (in a background task) for the bot's closing line to finish playing,
    THEN drops the line — identical behaviour the end_call tool relied on, now
    shared so the goodbye safety net can reuse it. Idempotent per conversation:
    returns True if it scheduled the hangup, False if one was already scheduled
    or there's no call_id to hang up.
    """
    if conversation_id in _HANGUP_SCHEDULED:
        return False
    if not call_id:
        return False
    _HANGUP_SCHEDULED.add(conversation_id)

    async def _delayed_hangup() -> None:
        # WAIT for the closing line to actually be spoken to + finish playing for
        # the user (event-driven), THEN drop the line — so the goodbye is never
        # cut off mid-word and the line never lingers afterward.
        wait_reason = await _wait_for_goodbye_finished(
            conversation_id, is_avatar=is_avatar
        )
        log.info(
            "[end_call] goodbye done — tearing down",
            conversation=conversation_id[:8], call_id=call_id,
            wait=wait_reason, reason=reason,
        )
        try:
            if is_avatar:
                # Avatar has no telephony leg — tear down the WebRTC/Simli
                # session via the avatar manager. call_id holds the pc_id.
                from agent_backend.channels.avatar_video.runner import (
                    get_avatar_manager,
                )
                await get_avatar_manager().end_session(call_id)
            else:
                from agent_backend.channels.voice.providers import get_voice_provider
                provider = get_voice_provider()
                await provider.hangup(call_id)
                # Note: provider.hangup() treats ACS error 8522 ("Call not
                # found") as success — the call is already gone, which is the
                # desired end state.
        except Exception as e:  # noqa: BLE001
            log.warning("[end_call] hangup failed: %s", e)
        finally:
            # Allow a fresh call reusing the same id (unlikely) to schedule again.
            _HANGUP_SCHEDULED.discard(conversation_id)

    asyncio.create_task(_delayed_hangup(), name=f"hangup-{conversation_id[:8]}")
    return True


def build_tools(ctx: ToolContext) -> list[BaseTool]:
    session = ctx.session
    # Only a real call can be "ended". end_call terminates a telephony leg via
    # the voice provider, so it only makes sense on phone-like channels — NOT on
    # WhatsApp/chat (which share the counselor tool group but have no call to
    # hang up). Offering it there just invites the model to call a no-op tool.
    if session.channel not in {"voice", "avatar_video"}:
        return []
    # Once-per-call flag so the LLM can't accidentally schedule N hangups
    # (the brain occasionally re-invokes end_call on subsequent user turns
    # if the carrier line is still up — e.g. ACS hadn't yet processed our
    # previous terminate request). Closed over by the tool below.
    fired = {"v": False}

    @tool
    async def end_call(reason: str) -> str:
        """Hang up the call — but ONLY after you have SPOKEN a proper goodbye.

        HARD REQUIREMENT — in the SAME reply, BEFORE calling this tool, you MUST
        say a warm, natural, context-relevant closing line out loud to the person,
        like a real human ending a call would. Acknowledge how the conversation
        went, restate any agreed next step, and sign off — e.g.
          • "Lovely talking to you today — all the best with your application,
             and take care!"
          • "No problem at all — I'll check on that and call you back tomorrow.
             Have a lovely evening!"
          • "Thanks so much for your time today — take care, bye for now!"
        NEVER call end_call with no spoken goodbye, with just "okay", or in a turn
        where you said nothing. The system waits for your closing line to finish
        playing to the person, THEN drops the line — so the goodbye is the last
        thing they hear. After invoking this tool, say nothing further.

        DON'T call this just because the person AGREED to a next step — "yes,
        send it on WhatsApp", "sure, book the visit", "ok". Accepting a next step
        is mid-conversation, NOT the end: confirm you'll do it and keep talking.
        Only end the call once they are genuinely finished (said bye / nothing
        more to ask / disengaged).

        Args:
            reason: Why the call is ending. Free text for the operator's
                audit log. Examples:
                  "candidate said goodbye"
                  "no interest, declined CTA"
                  "next step scheduled, wrapped up"
                  "candidate disengaged — multiple one-word replies"
        """
        if fired["v"]:
            log.info(
                "[end_call] already fired this call — no-op",
                session=session.short(),
                reason=reason,
            )
            return (
                "Call termination already in progress. Do not say anything "
                "else; just wait for the carrier to drop the line."
            )

        # BACKSTOP: only hang up when the candidate has given a genuine end
        # signal (said bye / nothing else / declined further help) OR the bot
        # already asked "anything else?" and they said no. A bare acceptance
        # ("yes, send it on WhatsApp") or a self-declared "wrapped" is NOT an
        # end. Don't set `fired`; this turn did not end the call, and a later
        # genuine end must still work.
        if _is_premature_end(reason):
            log.info(
                "[end_call] refused — no genuine end signal in reason; ask "
                "'anything else?' first and keep the call open",
                session=session.short(),
                reason=reason,
            )
            return (
                "DON'T end the call yet. Nothing in the conversation shows the "
                "candidate is actually finished — agreeing to a next step (e.g. "
                "sending details to WhatsApp) or things feeling 'wrapped' is NOT "
                "the end. First, in your NEXT turn with NO goodbye words, warmly "
                "confirm you'll take care of any agreed step (on voice the "
                "WhatsApp follow-up goes out automatically after the call), THEN "
                "ask if there's anything else you can help them with (fees, "
                "scholarships, campus, etc.). Only call end_call AFTER they "
                "clearly say there's nothing else / say bye / disengage — and "
                "state that in the reason (e.g. 'asked if anything else, candidate "
                "said no')."
            )

        fired["v"] = True

        call_id = session.call_id
        if not call_id:
            log.warning(
                "[end_call] no call_id on session — cannot hang up from here",
                session=session.short(),
                reason=reason,
            )
            return (
                "Could not end the call from the server (call_id missing). "
                "Deliver your closing line and let the candidate hang up."
            )

        log.info(
            "[end_call] scheduling hangup (after goodbye finishes)",
            session=session.short(),
            call_id=call_id,
            reason=reason,
        )

        schedule_graceful_hangup(
            conversation_id=session.conversation_id,
            call_id=call_id,
            is_avatar=session.channel == "avatar_video",
            reason=reason,
        )
        return (
            f"Reason: {reason}. Your closing line is being spoken to the person "
            "now; the call will end automatically once it finishes. Do NOT say "
            "anything else."
        )

    return [end_call]
