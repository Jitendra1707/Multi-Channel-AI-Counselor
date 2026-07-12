"""HTTP client → AegisBackend (the conversation engine).

BusinessLayer drives AegisBackend through its existing/added endpoints:
  - POST /api/voice/dial        start an outbound call (existing endpoint)
  - POST /api/whatsapp/send     send a WhatsApp message (added, additive)

Every call is best-effort and surfaces a structured result rather than raising,
so a worker loop can record the outcome and move on.
"""

from __future__ import annotations

from typing import Any

import httpx

from business.config import get_settings
from business.logging import get_logger

log = get_logger(__name__)


class AegisResult:
    def __init__(self, ok: bool, status: int, data: Any = None, error: str | None = None) -> None:
        self.ok = ok
        self.status = status
        self.data = data
        self.error = error

    def __repr__(self) -> str:
        return f"AegisResult(ok={self.ok}, status={self.status}, error={self.error!r})"


class AegisClient:
    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        s = get_settings()
        self._base = (base_url or s.aegis_base_url).rstrip("/")
        self._timeout = timeout or s.aegis_timeout_s
        self._client: httpx.AsyncClient | None = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._base, timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _post(self, path: str, json: dict) -> AegisResult:
        try:
            client = await self._http()
            resp = await client.post(path, json=json)
        except Exception as e:  # noqa: BLE001
            log.warning("aegis request failed", path=path, err=str(e)[:200])
            return AegisResult(ok=False, status=0, error=f"{type(e).__name__}: {e}"[:200])
        ok = 200 <= resp.status_code < 300
        data: Any = None
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = resp.text
        if not ok:
            log.warning("aegis non-2xx", path=path, status=resp.status_code, body=str(data)[:200])
        return AegisResult(ok=ok, status=resp.status_code, data=data,
                           error=None if ok else str(data)[:200])

    # --- commands -------------------------------------------------------
    async def dial(
        self,
        *,
        lead_id: str,
        to_e164: str | None = None,
        full_name: str | None = None,
        language: str | None = None,
    ) -> AegisResult:
        """Start an outbound call. We pass the candidate's number + identity from
        the BusinessLayer (the system of record) so AegisBackend doesn't depend
        on leads.json; `lead_id` correlates the call and keys memory hydration."""
        payload: dict[str, Any] = {"lead_id": lead_id}
        if to_e164:
            payload["to_e164"] = to_e164
        if full_name:
            payload["full_name"] = full_name
        if language:
            payload["language"] = language
        return await self._post("/api/voice/dial", payload)

    async def send_whatsapp(
        self,
        *,
        to_phone: str | None = None,
        lead_id: str | None = None,
        body: str | None = None,
        media_url: str | None = None,
        doc_key: str | None = None,
        template_key: str | None = None,
        template_params: dict[str, str] | None = None,
    ) -> AegisResult:
        """Ask AegisBackend to deliver a WhatsApp message. Passing `doc_key`
        (a catalog key or free text like 'fee details') lets AegisBackend resolve
        the document URL + pick free-form vs approved template by the 24h window —
        all WhatsApp policy stays on that side.

        `template_key` (+ `template_params`) sends a registered approved template
        standalone (no document) — used for the post-payment admission next-steps
        message. AegisBackend tries the template; if its name isn't approved yet,
        the caller's free-form `body` (in-window) is the fallback path."""
        payload: dict[str, Any] = {}
        if body:
            payload["body"] = body
        if to_phone:
            payload["to_phone"] = to_phone
        if lead_id:
            payload["lead_id"] = lead_id
        if media_url:
            payload["media_url"] = media_url
        if doc_key:
            payload["doc_key"] = doc_key
        if template_key:
            payload["template_key"] = template_key
        if template_params:
            payload["template_params"] = template_params
        return await self._post("/api/whatsapp/send", payload)

    async def send_counsellor_email(self, payload: dict[str, Any]) -> AegisResult:
        """Ask AegisBackend to email a lead-escalation report to the human
        counsellors (recipients configured on that side via
        EMAIL_COUNSELLOR_RECIPIENTS). The payload carries the full picture —
        identity, scores, summaries, facts, concerns, escalation reason — so
        AegisBackend composes the report purely from it (no stale local state)."""
        return await self._post("/api/email/escalation", payload)

    async def send_campus_visit_email(self, payload: dict[str, Any]) -> AegisResult:
        """Ask AegisBackend to send the candidate the campus-visit confirmation
        email. Payload: {to, candidate_name, visit_date, visit_time, notes?}.
        AegisBackend owns the (beautiful HTML) template."""
        return await self._post("/api/email/campus-visit", payload)


_client: AegisClient | None = None


def get_aegis_client() -> AegisClient:
    global _client
    if _client is None:
        _client = AegisClient()
    return _client
