"""Lead extraction service — orchestrates the web_extractor end to end.

Flow:
  1. Start a Playwright BrowserManager (headless from .env).
  2. Run NavigatorAgent against the configured URL + goal — the LLM drives the
     browser and returns a free-text summary of everything it found.
  3. Ask the LLM (same OpenAI client/config) to turn that raw text into a clean
     JSON array of lead objects matching our leads schema.
  4. Upsert-append those leads into the configured JSON file
     (business/data/leads.json by default), de-duping by phone/email.

DB insertion into the `leads` table is intentionally deferred — for now the
extracted leads land in the JSON file alongside the seed data.

Everything runs off `business.config` settings; nothing here touches
AegisBackend or the existing workers, so it can't affect current behaviour.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from business.config import get_settings
from business.logging import get_logger
from business.web_extractor import BrowserManager, NavigatorAgent, NavSession

log = get_logger(__name__)

# Lead fields we keep when parsing — mirrors business/data/leads.json shape so
# the appended records look exactly like the seed entries.
_LEAD_FIELDS = (
    "full_name",
    "email",
    "phone_e164",
    "source",
    "language_preference",
    "course_interest",
    "intake_year",
    "city",
    "parent_name",
    "parent_phone_e164",
    "consent_call",
    "consent_whatsapp",
)

_PARSE_SYSTEM_PROMPT = (
    "You convert messy extracted web-page text into structured lead records. "
    "Return ONLY a JSON array (no markdown, no prose). Each element is an object "
    "with these keys when the information is present: full_name, email, "
    "phone_e164 (E.164 like +9198..., best effort), source, city, "
    "course_interest, intake_year (integer), parent_name, parent_phone_e164. "
    "Omit keys you cannot determine. If the text contains no leads, return []."
)


async def run_extraction() -> dict[str, Any]:
    """Run the full extract → parse → append pipeline. Returns a summary dict."""
    s = get_settings()

    if not s.extractor_url:
        raise ValueError("EXTRACTOR_URL is not set in .env — nothing to extract.")
    if not s.llm_api_key:
        raise ValueError("LLM_API_KEY is not set in .env — the navigator/parser need it.")

    credentials: dict[str, str] = {}
    if s.extractor_username:
        credentials["username"] = s.extractor_username
    if s.extractor_password:
        credentials["password"] = s.extractor_password

    conv_id = f"extract-{uuid.uuid4().hex[:8]}"
    browser = BrowserManager()
    await browser.start(headless=s.extractor_headless)
    try:
        agent = NavigatorAgent(
            session=NavSession(conversation_id=conv_id),
            browser=browser,
            max_steps=s.extractor_max_steps,
            credentials=credentials or None,
        )
        raw_summary = await agent.run(url=s.extractor_url, goal=s.extractor_goal)
    finally:
        await browser.stop()

    leads = await _parse_leads(raw_summary)
    written = _append_leads(leads, Path(s.extractor_output_path))

    log.info(
        "[extractor] run complete",
        url=s.extractor_url,
        parsed=len(leads),
        appended=written["appended"],
        updated=written["updated"],
    )
    return {
        "ok": True,
        "url": s.extractor_url,
        "headless": s.extractor_headless,
        "parsed_count": len(leads),
        "appended": written["appended"],
        "updated": written["updated"],
        "output_path": str(s.extractor_output_path),
        "summary_preview": raw_summary[:500],
    }


async def _parse_leads(raw_text: str) -> list[dict[str, Any]]:
    """Use the LLM to turn the navigator's free-text summary into lead dicts."""
    from openai import AsyncOpenAI

    s = get_settings()
    client = AsyncOpenAI(
        api_key=s.llm_api_key,
        base_url=str(s.llm_api_url) if s.llm_api_url else None,
    )
    try:
        resp = await client.chat.completions.create(
            model=s.extractor_llm_model,
            messages=[
                {"role": "system", "content": _PARSE_SYSTEM_PROMPT},
                {"role": "user", "content": f"Extracted web content:\n\n{raw_text[:12000]}"},
            ],
            temperature=0,
            max_tokens=s.analyzer_max_tokens,
        )
        body = (resp.choices[0].message.content or "").strip()
    except Exception as e:  # noqa: BLE001
        log.warning("[extractor] parse LLM call failed", err=str(e))
        return []

    data = _coerce_json_array(body)
    cleaned: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        lead = {k: item[k] for k in _LEAD_FIELDS if k in item and item[k] not in (None, "")}
        # Need at least one identifier to be a usable lead.
        if not (lead.get("phone_e164") or lead.get("email") or lead.get("full_name")):
            continue
        cleaned.append(lead)
    return cleaned


def _coerce_json_array(body: str) -> list[Any]:
    """Best-effort parse of an LLM response into a JSON list (tolerates fences)."""
    body = body.strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", body).strip()
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        # Try to grab the first [...] block.
        m = re.search(r"\[.*\]", body, re.DOTALL)
        if not m:
            log.warning("[extractor] parser returned non-JSON", preview=body[:200])
            return []
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            log.warning("[extractor] parser JSON block invalid", preview=body[:200])
            return []
    if isinstance(parsed, dict):
        # Some models wrap the array, e.g. {"leads": [...]}.
        for v in parsed.values():
            if isinstance(v, list):
                return v
        return [parsed]
    return parsed if isinstance(parsed, list) else []


def _norm_phone(p: str) -> str:
    """Canonicalise to '+<digits>' E.164 (mirrors the store's _norm_phone)."""
    p = (p or "").strip().replace(" ", "").replace("-", "").lstrip("+")
    return ("+" + p) if p.isdigit() else (p or "")


def _append_leads(leads: list[dict[str, Any]], path: Path) -> dict[str, int]:
    """Upsert leads into the JSON array at `path`, de-duping by phone then email.

    Existing records are updated in place (non-empty new fields win); new
    records are appended with a generated lead_id. Returns counts.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: list[dict[str, Any]] = []
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                existing = [x for x in loaded if isinstance(x, dict)]
        except Exception as e:  # noqa: BLE001
            log.warning("[extractor] could not read existing leads file — starting fresh", err=str(e))

    by_phone = {_norm_phone(x.get("phone_e164", "")): x for x in existing if x.get("phone_e164")}
    by_email = {str(x.get("email", "")).lower(): x for x in existing if x.get("email")}

    appended = 0
    updated = 0
    for lead in leads:
        phone = _norm_phone(lead.get("phone_e164", ""))
        email = str(lead.get("email", "")).lower()
        if phone:
            lead["phone_e164"] = phone

        match = (by_phone.get(phone) if phone else None) or (by_email.get(email) if email else None)
        if match is not None:
            # Update only with non-empty new values; never clobber an existing id.
            for k, v in lead.items():
                if v not in (None, ""):
                    match[k] = v
            updated += 1
        else:
            record = {"lead_id": f"web-{uuid.uuid4().hex[:8]}", **lead}
            record.setdefault("source", "web_extractor")
            record.setdefault("language_preference", "en")
            existing.append(record)
            if phone:
                by_phone[phone] = record
            if email:
                by_email[email] = record
            appended += 1

    path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"appended": appended, "updated": updated}
