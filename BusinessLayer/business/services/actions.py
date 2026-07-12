"""Action / outbox worker — executes side-effects the analyzer queued.

Drains the `tasks` table and dispatches by type:
  send_brochure     → AegisBackend WhatsApp send
  schedule_followup → set the lead's next_action_at (the dialer re-picks it)
  callback          → schedule an immediate re-dial

Idempotent by construction: the unique `dedupe_key` means a brochure can never
be queued twice, and consent is re-checked at execution time (not just enqueue).
"""

from __future__ import annotations

import asyncio

from business.clients import get_aegis_client
from business.config import get_settings
from business.logging import get_logger
from business.models import Task
from business.store import get_store

log = get_logger(__name__)


async def execute_task(task: Task) -> tuple[bool, str | None]:
    """Run one task. Returns (success, error). A 'deliberate skip' (e.g. no
    consent) returns success=True so it isn't retried forever."""
    store = get_store()
    lead = await store.get_lead(task.lead_id)
    if lead is None:
        return False, "lead not found"

    if task.type == "send_brochure":
        if not lead.consent_whatsapp:
            log.info("brochure skipped — no whatsapp consent", lead_id=lead.lead_id)
            return True, "skipped: no whatsapp consent"
        payload = task.payload or {}
        doc = payload.get("doc") or "the details we discussed"
        body = payload.get("body") or f"Hi {lead.full_name.split()[0] if lead.full_name and lead.full_name != 'Unknown' else 'there'}, sharing {doc} as promised. Happy to help with any questions!"
        media_url = payload.get("media_url")
        # Pass `doc` as doc_key so AegisBackend resolves the document from its
        # catalog and picks free-form vs approved template by the 24h window.
        res = await get_aegis_client().send_whatsapp(
            to_phone=lead.phone_e164 or None,
            lead_id=lead.lead_id,
            body=body,
            media_url=media_url,
            doc_key=doc,
        )
        if res.ok:
            # Close the loop: record what was actually delivered so the next
            # conversation follows up on it instead of re-offering to send it.
            await get_store().record_delivery(
                lead_id=lead.lead_id, item=doc, channel="whatsapp"
            )
        return res.ok, res.error

    if task.type == "send_admission_next_steps":
        # Application complete + paid → send the course-appropriate next-steps
        # message: entrance-exam phases + slot booking, OR the token-amount link
        # (Module F). Branch on whether the lead's course has an entrance exam.
        if not lead.consent_whatsapp:
            log.info("admission next-steps skipped — no whatsapp consent", lead_id=lead.lead_id)
            return True, "skipped: no whatsapp consent"
        from business.domain.admissions import next_steps_message

        s = get_settings()
        kind, template_key, body = next_steps_message(
            course_interest=lead.course_interest or (lead.facts or {}).get("course_interest"),
            exam_url=s.admission_exam_slot_url,
            token_url=s.admission_token_payment_url,
        )
        first = (
            lead.full_name.split()[0]
            if lead.full_name and lead.full_name != "Unknown"
            else "there"
        )
        # Approved template out-of-window; free-form `body` is the in-window
        # fallback AegisBackend uses if the template name isn't approved yet.
        res = await get_aegis_client().send_whatsapp(
            to_phone=lead.phone_e164 or None,
            lead_id=lead.lead_id,
            body=f"Hi {first}, {body}",
            template_key=template_key,
            template_params={"first_name": first, "message": body},
        )
        if res.ok:
            await get_store().record_delivery(
                lead_id=lead.lead_id,
                item=f"admission next steps ({kind})",
                channel="whatsapp",
            )
            log.info("admission next-steps sent", lead_id=lead.lead_id, kind=kind)
        return res.ok, res.error

    if task.type == "schedule_followup":
        in_minutes = int((task.payload or {}).get("in_minutes") or 1440)  # default next day
        await store.schedule_followup(lead_id=lead.lead_id, in_minutes=in_minutes)
        log.info("followup scheduled", lead_id=lead.lead_id, in_minutes=in_minutes)
        return True, None

    if task.type == "callback":
        in_minutes = int((task.payload or {}).get("in_minutes") or 60)
        await store.schedule_followup(lead_id=lead.lead_id, in_minutes=in_minutes)
        log.info("callback scheduled", lead_id=lead.lead_id, in_minutes=in_minutes)
        return True, None

    if task.type == "schedule_campus_visit":
        # A campus visit the brain booked on a live call. Send the candidate the
        # confirmation email (AegisBackend owns the HTML template) and record the
        # delivery so the next conversation follows up instead of re-offering.
        payload = task.payload or {}
        to_email = lead.email or payload.get("email")
        if not to_email:
            log.info("campus visit email skipped — no email on file", lead_id=lead.lead_id)
            return True, "skipped: no email on file"  # deliberate skip — don't retry forever
        visit_date = payload.get("visit_date") or ""
        visit_time = payload.get("visit_time") or ""
        res = await get_aegis_client().send_campus_visit_email(
            {
                "to": to_email,
                "lead_id": lead.lead_id,
                "candidate_name": lead.full_name,
                "visit_date": visit_date,
                "visit_time": visit_time,
                "notes": payload.get("notes"),
            }
        )
        if res.ok:
            await get_store().record_delivery(
                lead_id=lead.lead_id,
                item=f"Campus visit confirmation ({visit_date} {visit_time})".strip(),
                channel="email",
            )
            log.info("campus visit confirmed + emailed", lead_id=lead.lead_id, when=f"{visit_date} {visit_time}")
        return res.ok, res.error

    if task.type == "escalate_counsellor":
        # Email a full lead report to the human counsellors (composed + sent by
        # AegisBackend's email channel), then mark the lead DELEGATED so the
        # dialer leaves it alone — the counsellor owns the next touch.
        payload = task.payload or {}
        res = await get_aegis_client().send_counsellor_email(
            {
                "lead_id": lead.lead_id,
                "full_name": lead.full_name,
                "phone": lead.phone_e164,
                "email": lead.email,
                "status": lead.status,
                # Prefer the triggering session's snapshot (stamped at enqueue);
                # fall back to the lead rollup.
                "interest": payload.get("interest", lead.interest),
                "confidence": payload.get("confidence", lead.confidence),
                "sentiment": payload.get("sentiment"),
                "course_interest": lead.course_interest
                or (lead.facts or {}).get("course_interest"),
                "reason": payload.get("reason"),
                "session_summary": payload.get("session_summary"),
                "journey_summary": lead.summary,
                "next_best_action": payload.get("next_best_action"),
                "facts": lead.facts or {},
                "open_concerns": lead.open_concerns or [],
                "sent_items": lead.sent_items or [],
            }
        )
        if res.ok:
            await store.mark_delegated(lead.lead_id)
            log.info("lead delegated to counsellor", lead_id=lead.lead_id)
        return res.ok, res.error

    return False, f"unknown task type: {task.type}"


async def run_actions_loop(stop: asyncio.Event) -> None:
    s = get_settings()
    log.info("actions loop started", poll_seconds=s.actions_poll_seconds)
    while not stop.is_set():
        try:
            # Rescue tasks stranded IN_PROGRESS by a worker that died/reloaded
            # mid-execution (claim commits IN_PROGRESS before running) → back to
            # PENDING so they retry instead of stalling forever.
            reaped = await get_store().reap_stuck_in_progress_tasks(older_than_minutes=5)
            if reaped:
                log.info("reclaimed stuck in_progress tasks", count=reaped)

            tasks = await get_store().claim_pending_tasks(limit=10)
            for task in tasks:
                if stop.is_set():
                    break
                try:
                    ok, err = await execute_task(task)
                except Exception as e:  # noqa: BLE001
                    ok, err = False, f"{type(e).__name__}: {e}"[:200]
                if ok:
                    await get_store().complete_task(task.id)
                else:
                    await get_store().fail_task(task.id, error=err or "unknown error")
                    log.warning("task failed", task_id=task.id, type=task.type, attempt=task.attempts, err=err)
        except Exception as e:  # noqa: BLE001
            log.warning("actions loop iteration failed", err=str(e)[:200])
        await _sleep_or_stop(stop, s.actions_poll_seconds)
    log.info("actions loop stopped")


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass
