"""Dialer — the outbound campaign loop (OFF by default).

Picks leads due for a call, respects a parallel-call cap (also your telephony
provider's concurrency ceiling) and a pacing delay between dials, then asks
AegisBackend to place each call. Post-call status is set by the analyzer once
the call's session is closed and analyzed — the dialer only starts calls.
"""

from __future__ import annotations

import asyncio

from business.clients import get_aegis_client
from business.config import get_settings
from business.logging import get_logger
from business.models import Lead
from business.store import get_store

log = get_logger(__name__)


async def _dial_one(lead: Lead, sem: asyncio.Semaphore) -> None:
    s = get_settings()
    store = get_store()
    async with sem:
        res = await get_aegis_client().dial(
            lead_id=lead.lead_id,
            to_e164=lead.phone_e164 or None,
            full_name=lead.full_name,
            language=lead.language_preference,
        )
        if res.ok:
            log.info("dial started", lead_id=lead.lead_id, to=_redact(lead.phone_e164))
            # Leave the lead IN_CALL; analyzer sets the post-call status.
        else:
            log.warning("dial failed — scheduling retry", lead_id=lead.lead_id, err=res.error)
            await store.mark_dial_result(
                lead.lead_id, success=False, backoff_minutes=s.dialer_call_backoff_minutes
            )
        # Pace the NEXT dial start (smooths carrier load + provider concurrency).
        if s.dialer_pacing_seconds > 0:
            await asyncio.sleep(s.dialer_pacing_seconds)


async def run_dialer_loop(stop: asyncio.Event) -> None:
    s = get_settings()
    sem = asyncio.Semaphore(s.dialer_max_parallel_calls)
    log.info(
        "dialer loop started",
        poll_seconds=s.dialer_poll_seconds,
        max_parallel=s.dialer_max_parallel_calls,
        max_attempts=s.dialer_call_max_attempts,
    )
    while not stop.is_set():
        try:
            # Recover leads stranded IN_CALL (dial accepted, call never landed).
            reaped = await get_store().reap_stuck_in_call_leads(older_than_minutes=15)
            if reaped:
                log.info("reclaimed stuck IN_CALL leads", count=reaped)

            # Bound LIVE calls, not just dial requests: the dial API returns as
            # soon as the call is queued, so the semaphore alone would let a
            # whole batch ring simultaneously. A claimed lead stays IN_CALL
            # until its session is analyzed (answered) or the missed-call /
            # reaper path reverts it (unanswered) — so "IN_CALL count" ≈ live
            # calls, and we only claim up to the remaining headroom.
            in_call = await get_store().count_in_call_leads()
            headroom = s.dialer_max_parallel_calls - in_call
            if headroom <= 0:
                log.info("dialer at capacity — waiting", in_call=in_call,
                         max_parallel=s.dialer_max_parallel_calls)
                await _sleep_or_stop(stop, s.dialer_poll_seconds)
                continue

            leads = await get_store().claim_due_for_call(
                limit=min(s.dialer_batch_size, headroom),
                max_attempts=s.dialer_call_max_attempts,
            )
            if leads:
                log.info("dialer claimed leads", count=len(leads),
                         lead_ids=[x.lead_id for x in leads])
                # Fire dials concurrently, capped by the semaphore + pacing.
                await asyncio.gather(*(_dial_one(lead, sem) for lead in leads))

            # Visibility: due leads whose dial-attempt budget is exhausted are
            # never claimed — surface them so they don't rot invisibly.
            # (Attempts reset automatically whenever a call CONNECTS, so this
            # only flags leads that were unreachable max_attempts times in a row.)
            exhausted = await get_store().count_exhausted_due_leads(
                max_attempts=s.dialer_call_max_attempts
            )
            if exhausted:
                log.warning(
                    "due leads skipped — dial attempts exhausted",
                    count=exhausted, max_attempts=s.dialer_call_max_attempts,
                )
        except Exception as e:  # noqa: BLE001
            log.warning("dialer loop iteration failed", err=str(e)[:200])
        await _sleep_or_stop(stop, s.dialer_poll_seconds)
    log.info("dialer loop stopped")


def _redact(phone: str) -> str:
    if not phone or len(phone) <= 6:
        return "***"
    return f"{phone[:3]}***{phone[-3:]}"


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass
