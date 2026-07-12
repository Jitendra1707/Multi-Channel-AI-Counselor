"""NavigatorAgent — LLM-driven autonomous web navigation (BusinessLayer copy).

Takes a URL and a plain-English goal, then calls the browser tools in a
loop, asking the LLM on each step which action to take next, until the goal
is achieved or the step limit is reached. This is the engine that, given a
website URL, drives Playwright (with LLM-generated actions) to extract lead
data from the page.

Supports interactive credential injection and dynamic MFA/OTP prompts so
login flows (basic form, SSO, authenticator codes) work transparently.

Usage::

    browser = BrowserManager()
    await browser.start(headless=True)
    agent = NavigatorAgent(
        session=NavSession(conversation_id="run-1"),
        browser=browser,
        credentials={"username": "x", "password": "y"},
    )
    summary = await agent.run(url="https://example.com", goal="Sign in and extract leads")
    print(summary)
    await browser.stop()

The system prompt, decision rules, and LLM call below are kept exactly as in
the original web_extractor — only the imports, logger, and settings source
were repointed at BusinessLayer. The LLM client is built lazily from
`get_settings()` on first `run()` so importing this module has no side-effects.
"""

from __future__ import annotations

import json
import re

from business.logging import get_logger
from business.web_extractor._base import NavSession, ToolContext
from business.web_extractor.manager import BrowserManager
from business.web_extractor.tools import build_tools

log = get_logger(__name__)

_SYSTEM_PROMPT = """\
You are a web navigation agent. Your job is to achieve a user goal by \
browsing web pages one step at a time.

On each step you receive the current goal, visible page content, \
interactive elements, steps taken so far, and a list of actions that \
already failed.

Respond with ONLY a JSON object (no markdown, no explanation). Valid actions:

  {"action": "click",       "target": "<exact visible text of the element>"}
  {"action": "fill",        "label": "<field label/placeholder>", "value": "<text or {{key}}>"}
  {"action": "select",      "label": "<dropdown label>", "option": "<option text>"}
  {"action": "check",       "label": "<checkbox or radio label>"}
  {"action": "fill_date",   "label": "<date field label>", "date": "<YYYY-MM-DD>"}
  {"action": "read_form_data"}
  {"action": "read_table_data"}
  {"action": "press_key",   "key": "<Enter|Tab|Escape|ArrowDown|...>"}
  {"action": "scroll",      "direction": "up" or "down"}
  {"action": "back"}
  {"action": "read"}
  {"action": "ask_user",    "question": "<question to show the user>", "answer_key": "<key>"}
  {"action": "done",        "summary": "<concise description of what you found or accomplished>"}

Rules:
- Respond with "done" as soon as the goal is achieved or clearly cannot be achieved.
- NEVER retry any action listed under ACTIONS THAT ALREADY FAILED.
- Prefer "click" over "scroll" when a relevant link or button is already visible.
- If clicking keeps failing, try open_web_page with a direct URL.
- If you have already successfully filled a field and the page URL has not
  changed, do NOT fill the same field again. Instead try a different submission
  method: use press_key with "Enter" (focused on the last filled field), click
  a different button, or use list_page_elements to find another approach.
- If a fill action returns ERROR saying a field does not exist, it likely means
  the page changed (e.g. login succeeded, form submitted). Immediately call
  "read" to understand the current page before taking any other action.
- After every scroll action, ALWAYS call "read" next to see the newly visible
  content. Do not scroll multiple times without reading in between.
- When the goal is to find a specific field value (a name, date, ID, etc.)
  and "read" does not show it, use "read_form_data" to extract form field values.
- When the goal involves grades, CGPA, SGPA, marks, scores, exam results, or
  any tabular data, use "read_table_data" to extract all visible table contents.
- Use ask_user ONLY for information you must fill INTO the page — a CAPTCHA
  text, a one-time code, a specific value you cannot determine yourself.
  NEVER use ask_user to ask the user what to do next, whether to retry, or
  for general instructions — make those decisions yourself based on the page.
  Choose a descriptive answer_key (e.g. "otp", "captcha", "search_term").
  After the user responds, the answer is available as {{answer_key}} in any
  subsequent fill or select action.

For login / authentication:
- Fill email or username field first, then password, then click the login button.
- For SSO (Sign in with Google, Microsoft, etc.), click the provider button and
  continue filling credentials on the provider page in the same way.
- Use {{username}} and {{password}} as values when filling login fields if
  credentials are listed under AVAILABLE CREDENTIALS.
- For OTP, SMS code, or authenticator number: use ask_user with answer_key "otp".
- After clicking login or pressing Enter to submit a form, ALWAYS call
  "read" as the very next action. Do not click anything else first.
- NEVER click "Forgot password?", "Reset Password", or any password-recovery
  link. These are off-task. Stay focused on the login form.
- If read shows an error message (invalid credentials, captcha required,
  etc.), report it clearly to the user via ask_user before retrying.
"""


class NavigatorAgent:
    """Autonomous web navigation driven by an LLM decision loop.

    Args:
        session: Active session — keys the per-session browser page.
        browser: A started BrowserManager instance whose pages this agent drives.
        max_steps: Navigation steps before giving up (default 10).
        credentials: Optional dict of named credentials available for
            substitution in fill actions (e.g. {"username": "...", "password": "..."}).
            Keys are referenced as {{key}} in fill values; never sent to the LLM
            in plain text — only masked hints are shown.
        auto_answers: Optional pre-supplied answers for `ask_user` prompts, keyed
            by answer_key. Lets the navigator run unattended (no stdin) on the
            server — when the LLM asks for e.g. an "otp", it's pulled from here.
    """

    def __init__(
        self,
        session: NavSession,
        browser: BrowserManager,
        max_steps: int = 10,
        credentials: dict[str, str] | None = None,
        auto_answers: dict[str, str] | None = None,
    ) -> None:
        self._session = session
        self._browser = browser
        self._max_steps = max_steps
        self._credentials: dict[str, str] = dict(credentials or {})
        self._auto_answers: dict[str, str] = dict(auto_answers or {})
        ctx = ToolContext(session=session)
        self._tools = {t.name: t for t in build_tools(ctx, browser)}

    def _substitute(self, value: str) -> str:
        """Replace {{key}} placeholders with stored credential values."""
        for key, val in self._credentials.items():
            value = value.replace(f"{{{{{key}}}}}", val)
        return value

    async def run(self, url: str, goal: str) -> str:
        """Navigate from `url` toward `goal` autonomously.

        Returns a plain-text summary of what was found or accomplished.
        """
        from openai import AsyncOpenAI

        from business.config import get_settings

        s = get_settings()
        client = AsyncOpenAI(
            api_key=s.llm_api_key,
            base_url=str(s.llm_api_url) if s.llm_api_url else None,
        )
        model = s.extractor_llm_model

        log.info("[navigator] starting", url=url, goal=goal, model=model)

        open_result = await self._tools["open_web_page"].ainvoke({"url": url})
        log.info("[navigator] page opened", preview=open_result[:120])

        history: list[str] = [f"Opened {url}"]
        failed_actions: set[str] = set()
        blocked_fill_labels: set[str] = set()
        _last_form_data: str = ""
        _last_scroll_read: str = ""
        _last_click_form_data: dict[str, str] = {}
        _current_url: str = url

        for step in range(1, self._max_steps + 1):
            content = await self._tools["read_web_page_content"].ainvoke({})
            elements = await self._tools["list_page_elements"].ainvoke({})

            action = await self._decide(
                client=client,
                model=model,
                goal=goal,
                page_content=content[:4000],
                elements=elements,
                history=history,
                failed_actions=failed_actions,
                blocked_fill_labels=blocked_fill_labels,
            )

            action_type = action.get("action", "read")
            log.info("[navigator] step", step=step, action=action_type, detail=self._mask_action(action))

            if action_type == "done":
                summary = action.get("summary", "Goal reached.")
                log.info("[navigator] done", summary=summary)
                try:
                    page_content = await self._tools["read_web_page_content"].ainvoke({})
                    return f"{summary}\n\n--- Page content at completion ---\n{page_content}"
                except Exception:  # noqa: BLE001
                    return summary

            # ask_user: supply input the agent needs. On the server there is no
            # interactive stdin, so we answer from pre-supplied `auto_answers`
            # (keyed by answer_key); if none is available we record that and let
            # the loop's failed-action guard move the agent on.
            if action_type == "ask_user":
                question = action.get("question", "Please provide input")
                answer_key = action.get("answer_key") or "user_input"
                user_answer = self._auto_answers.get(answer_key, "")
                if user_answer:
                    self._credentials[answer_key] = user_answer
                    history.append(
                        f"Step {step}: Asked — '{question}'. "
                        f"Answer stored as {{{{{answer_key}}}}}."
                    )
                else:
                    log.warning("[navigator] ask_user with no auto-answer", question=question, key=answer_key)
                    history.append(
                        f"Step {step}: Asked — '{question}' but no answer is available "
                        f"(key '{answer_key}'). Proceed without it."
                    )
                    failed_actions.add(json.dumps(action, sort_keys=True))
                continue

            # Hard-enforce failed_actions — block execution, not just prompt hint.
            # Also inject list_page_elements so the LLM knows what to try instead.
            action_key = json.dumps(action, sort_keys=True)
            if action_key in failed_actions:
                elements = await self._tools["list_page_elements"].ainvoke({})
                history.append(
                    f"Step {step}: BLOCKED — {self._mask_action(action)}. "
                    f"Available elements to try: {elements[:400]}"
                )
                continue

            result = await self._execute(action, blocked_fill_labels)
            log.info("[navigator] result", step=step, result=result[:160])
            history.append(f"Step {step}: {self._mask_action(action)} → {result[:100]}")

            # After any click that changed the page (interaction) OR where
            # a CSS-only dropdown may have appeared (no-nav), inject both
            # form data and element list so the LLM sees what's now available.
            if action_type == "click" and (
                result.startswith("OK (interaction):") or
                result.startswith("OK (no navigation):")
            ):
                form_data = await self._tools["read_form_data"].ainvoke({})
                new_elements = await self._tools["list_page_elements"].ainvoke({})
                history.append(f"Auto-read after interaction: {form_data[:400]}")
                history.append(f"New elements after interaction: {new_elements[:300]}")
                # Block repeated interaction clicks that produce the same form data —
                # the element is toggling without revealing useful new information.
                target = action.get("target", "")
                norm = re.sub(r'\[.*?\d{1,2}:\d{2}.*?\]\n?', '', form_data)
                if target and target in _last_click_form_data and _last_click_form_data[target] == norm:
                    failed_actions.add(json.dumps(action, sort_keys=True))
                if target:
                    _last_click_form_data[target] = norm

            # After pressing Enter (form submission), force an immediate read so
            # the LLM sees the real page state before deciding the next action.
            if action_type == "press_key" and action.get("key", "").lower() == "enter":
                read_result = await self._tools["read_web_page_content"].ainvoke({})
                history.append(f"Auto-read after Enter: {read_result[:300]}")

            # After scrolling, force a read so the LLM sees the newly visible
            # content before deciding whether to scroll again.
            # If the content is unchanged from the last scroll, block this scroll
            # direction — scrolling is not revealing new content on this page.
            if action_type == "scroll":
                read_result = await self._tools["read_web_page_content"].ainvoke({})
                history.append(f"Auto-read after scroll: {read_result[:300]}")
                # Normalize away timestamps before comparing
                norm = re.sub(r'\b[A-Z]{3}\s+\d{1,2},\s+\d{4}.*?(?=\n|$)', '', read_result)
                if norm == _last_scroll_read:
                    failed_actions.add(json.dumps(action, sort_keys=True))
                _last_scroll_read = norm

            # Only true failures and genuine no-ops go into failed_actions.
            # "OK (interaction):" means a modal/panel opened — keep it retryable.
            if result.startswith("ERROR:") or result.startswith("OK (no navigation):"):
                failed_actions.add(json.dumps(action, sort_keys=True))
                if action.get("action") == "fill" and result.startswith("ERROR:"):
                    blocked_fill_labels.add(action.get("label", ""))

            # Block read_form_data if it returns the same data twice.
            # Normalize before comparing — section headers may include live
            # timestamps like "JUN 01, 2026 4:39:39 PM" that make each result
            # look unique even when the underlying field data is identical.
            if action_type == "read_form_data":
                normalized = re.sub(r'\[.*?\d{1,2}:\d{2}.*?\]\n?', '', result)
                normalized_last = re.sub(r'\[.*?\d{1,2}:\d{2}.*?\]\n?', '', _last_form_data)
                if normalized == normalized_last:
                    failed_actions.add('{"action": "read_form_data"}')
                _last_form_data = result

            # Unblock read_form_data, scroll dedup, and click dedup after navigation.
            if action_type in ("click", "press_key") and result.startswith("OK:"):
                failed_actions.discard('{"action": "read_form_data"}')
                _last_form_data = ""
                _last_scroll_read = ""
                _last_click_form_data = {}
                # Clear click-based blocks — links blocked on a previous page
                # may work correctly now that we've navigated somewhere new.
                failed_actions = {a for a in failed_actions
                                  if not a.startswith('{"action": "click"')}
                # Also unblock scroll actions blocked on the old page.
                for scroll_dir in ("down", "up"):
                    failed_actions.discard(
                        json.dumps({"action": "scroll", "direction": scroll_dir}, sort_keys=True)
                    )

            # After a URL-changing navigation, auto-inject read_form_data so
            # the LLM immediately sees available dropdowns/options on the new
            # page without having to call it manually first.
            if action_type == "click" and result.startswith("OK:") and "Now on:" in result:
                form_data = await self._tools["read_form_data"].ainvoke({})
                history.append(f"Form data on new page: {form_data[:500]}")

        return "Reached the step limit without completing the goal."

    async def _decide(
        self,
        *,
        client: object,
        model: str,
        goal: str,
        page_content: str,
        elements: str,
        history: list[str],
        failed_actions: set[str],
        blocked_fill_labels: set[str],
    ) -> dict:
        from openai import AsyncOpenAI

        c: AsyncOpenAI = client  # type: ignore[assignment]
        history_text = "\n".join(history) if history else "(none)"
        failed_text = "\n".join(failed_actions) if failed_actions else "(none)"
        blocked_fills_text = "\n".join(blocked_fill_labels) if blocked_fill_labels else "(none)"

        cred_section = ""
        if self._credentials:
            masked = "\n".join(
                f"  {k} = {'••••••••' if k in ('password', 'otp', 'code') else v}"
                for k, v in self._credentials.items()
            )
            cred_section = f"\n\nAVAILABLE CREDENTIALS (use {{{{key}}}} in fill values):\n{masked}"

        user_msg = (
            f"GOAL: {goal}\n\n"
            f"PAGE CONTENT:\n{page_content}\n\n"
            f"INTERACTIVE ELEMENTS:\n{elements}\n\n"
            f"STEPS TAKEN SO FAR:\n{history_text}\n\n"
            f"ACTIONS THAT ALREADY FAILED (do not retry):\n{failed_text}\n\n"
            f"FILL FIELDS THAT NO LONGER EXIST (do not fill these, even with different values):\n{blocked_fills_text}"
            f"{cred_section}"
        )

        try:
            resp = await c.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0,
                max_tokens=200,
            )
            raw = (resp.choices[0].message.content or "").strip()
            return json.loads(raw)
        except json.JSONDecodeError:
            log.warning("[navigator] LLM returned non-JSON — defaulting to read")
            return {"action": "read"}
        except Exception as e:  # noqa: BLE001
            log.warning("[navigator] LLM call failed", err=str(e))
            return {"action": "done", "summary": f"Navigation stopped due to LLM error: {e}"}

    async def _execute(self, action: dict, blocked_fill_labels: set[str] | None = None) -> str:
        action_type = action.get("action", "read")

        if action_type == "fill":
            label = action.get("label", "")
            if blocked_fill_labels and label in blocked_fill_labels:
                return (
                    f"BLOCKED: field '{label}' no longer exists on this page. "
                    "Call read to understand the current page state."
                )

        if action_type == "click":
            return await self._tools["click_web_element"].ainvoke(
                {"element_text": action.get("target", "")}
            )
        if action_type == "fill":
            return await self._tools["fill_web_input"].ainvoke({
                "label": action.get("label", ""),
                "value": self._substitute(action.get("value", "")),
            })
        if action_type == "select":
            return await self._tools["select_dropdown_option"].ainvoke({
                "label": action.get("label", ""),
                "option": action.get("option", ""),
            })
        if action_type == "check":
            return await self._tools["check_option"].ainvoke(
                {"label": action.get("label", "")}
            )
        if action_type == "fill_date":
            return await self._tools["fill_date"].ainvoke({
                "label": action.get("label", ""),
                "date": action.get("date", ""),
            })
        if action_type == "read_form_data":
            return await self._tools["read_form_data"].ainvoke({})
        if action_type == "read_table_data":
            return await self._tools["read_table_data"].ainvoke({})
        if action_type == "press_key":
            return await self._tools["press_key"].ainvoke(
                {"key": action.get("key", "Enter")}
            )
        if action_type == "scroll":
            return await self._tools["scroll_web_page"].ainvoke(
                {"direction": action.get("direction", "down")}
            )
        if action_type == "back":
            return await self._tools["go_web_back"].ainvoke({})
        if action_type == "read":
            return await self._tools["read_web_page_content"].ainvoke({})
        return f"Unknown action type: {action_type}"

    def _mask_action(self, action: dict) -> dict:
        """Return a copy of the action with credential values masked for display."""
        masked = dict(action)
        if masked.get("action") == "fill":
            raw_value = masked.get("value", "")
            # Mask if it contains a credential placeholder or looks like a password
            for key in self._credentials:
                if f"{{{{{key}}}}}" in raw_value or (
                    key in ("password", "otp", "code") and raw_value == self._credentials.get(key)
                ):
                    masked["value"] = "••••••••"
                    break
        return masked
