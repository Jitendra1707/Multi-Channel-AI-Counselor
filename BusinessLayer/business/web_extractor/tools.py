"""Web navigation tools — Playwright-backed browser control (BusinessLayer copy).

Exposes 13 LangGraph tools that let the navigator autonomously browse web pages:
load a URL, read content, list interactive elements, click, fill text inputs,
select dropdowns, check checkboxes/radio buttons, fill date fields, read form
data, read table data, press keys, scroll, and go back. Each tool operates on
the per-session Playwright Page managed by the BrowserManager passed into
`build_tools`, so state (cookies, navigation history) persists across tool
calls within the same run.

All tools return plain strings ("OK: ..." or "ERROR: ...").

This copy carries no relation to AegisBackend — logger, context, and browser
manager all resolve within BusinessLayer's web_extractor package. The tool
bodies themselves are unchanged.
"""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import BaseTool, tool

from business.logging import get_logger
from business.web_extractor._base import ToolContext
from business.web_extractor.manager import BrowserManager

log = get_logger(__name__)

# JavaScript that collects visible interactive elements and returns a list
# of human-readable strings like "[button] Submit" or "[input:email] Email".
_JS_LIST_ELEMENTS = """
() => Array.from(document.querySelectorAll('a, button, input, select, textarea'))
  .filter(el => el.offsetWidth > 0 && el.offsetHeight > 0)
  .map(el => {
    const tag = el.tagName.toLowerCase();
    const type = el.getAttribute('type') || '';
    const raw = (
      el.innerText ||
      el.value ||
      el.placeholder ||
      el.getAttribute('aria-label') ||
      el.getAttribute('title') ||
      el.getAttribute('name') ||
      ''
    ).trim().slice(0, 120);
    return raw ? `[${tag}${type ? ':' + type : ''}] ${raw}` : null;
  })
  .filter(Boolean)
  .slice(0, 50)
"""

_NAV_TIMEOUT_MS = 15_000  # 15 s — long enough for most pages


def build_tools(ctx: ToolContext, browser: BrowserManager) -> list[BaseTool]:  # noqa: C901
    conv_id = ctx.session.conversation_id

    @tool
    async def open_web_page(url: str) -> str:
        """Open a URL in the browser and return the page title and content preview.

        Use this as the first step whenever the user mentions a URL or you need
        to visit a web page. Returns the page title and up to 2000 characters of
        visible body text so you can decide what to do next.

        Args:
            url: Full URL to navigate to (must start with http:// or https://).
        """
        try:
            page = await browser.get_page(conv_id)
            await page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
            title = await page.title()
            body_text = await page.inner_text("body")
            excerpt = body_text.strip()[:2000]
            log.info("[browser] opened page", cid=conv_id[:8], title=title, url=url)
            return f"OK: opened '{title}'.\n\nContent preview:\n{excerpt}"
        except RuntimeError as e:
            return f"ERROR: browser not available — {e}"
        except Exception as e:  # noqa: BLE001
            log.warning("[browser] open_web_page failed", url=url, err=str(e))
            return f"ERROR: could not open '{url}' — {e}"

    @tool
    async def read_web_page_content() -> str:
        """Read the full visible text content of the current browser page.

        Use this after opening a page or clicking a link to get the complete
        text (up to 6000 characters). Strips all HTML — returns clean readable
        text only. Also reports the current URL so you can track navigation.
        """
        try:
            page = await browser.get_page(conv_id)
            title = await page.title()
            current_url = page.url
            # Try main content selectors first to skip nav menus / sidebars.
            # Fall back to full body if no main content container found.
            body_text = ""
            for selector in ("main", "[role='main']", "#content", ".content", "#main", ".main"):
                try:
                    el = page.locator(selector).first
                    if await el.count() and await el.is_visible():
                        body_text = await el.inner_text()
                        break
                except Exception:  # noqa: BLE001
                    continue
            if not body_text.strip():
                body_text = await page.inner_text("body")
            content = body_text.strip()[:8000]
            return f"Page: {title}\nURL: {current_url}\n\n{content}"
        except RuntimeError as e:
            return f"ERROR: browser not available — {e}"
        except Exception as e:  # noqa: BLE001
            log.warning("[browser] read_web_page_content failed", err=str(e))
            return f"ERROR: could not read page content — {e}"

    @tool
    async def list_page_elements() -> str:
        """List all visible interactive elements on the current page.

        Returns links, buttons, and form fields with their visible labels so
        you can identify what to click or fill. Use this before click_web_element
        or fill_web_input if you are unsure of the exact element text.
        """
        try:
            page = await browser.get_page(conv_id)
            elements: list[str] = await page.evaluate(_JS_LIST_ELEMENTS)
            if not elements:
                return "OK: no interactive elements found on this page."
            return "Interactive elements on current page:\n" + "\n".join(elements)
        except RuntimeError as e:
            return f"ERROR: browser not available — {e}"
        except Exception as e:  # noqa: BLE001
            log.warning("[browser] list_page_elements failed", err=str(e))
            return f"ERROR: could not list elements — {e}"

    @tool
    async def click_web_element(element_text: str) -> str:
        """Click a visible element on the current page by its text label.

        Finds the first VISIBLE element whose text contains `element_text`
        (case-insensitive, partial match). After clicking, waits for the page
        to settle and returns the new page title.

        Use list_page_elements first if you are unsure of the exact text.

        Args:
            element_text: Visible text of the element to click (e.g. "Sign in",
                "More information", "Submit").
        """
        try:
            page = await browser.get_page(conv_id)

            url_before = page.url
            dom_count_before = await page.locator("*").count()
            # Also count visible interactive elements — CSS-only dropdowns
            # change this without changing dom_count.
            _JS_VISIBLE_COUNT = (
                "() => Array.from(document.querySelectorAll("
                "'a, button, [role=\"button\"], [role=\"menuitem\"], [role=\"link\"]'"
                ")).filter(el => el.offsetParent !== null).length"
            )
            visible_count_before = await page.evaluate(_JS_VISIBLE_COUNT)

            def _classify_result(text: str, via: str) -> str:
                """Return the right OK string based on what changed after the click."""
                if page.url != url_before:
                    log.info("[browser] clicked element", cid=conv_id[:8], text=text, via=via)
                    return f"OK: clicked '{text}'. Now on: '{page.title()}' ({page.url})."
                return None  # caller will check DOM change

            async def _post_click_result(text: str, via: str) -> str:
                await page.wait_for_load_state("domcontentloaded", timeout=_NAV_TIMEOUT_MS)
                title = await page.title()
                if page.url != url_before:
                    log.info("[browser] clicked element", cid=conv_id[:8], text=text, via=via, title=title)
                    return f"OK: clicked '{text}'. Now on: '{title}' ({page.url})."
                # URL unchanged — wait for all network activity to settle so
                # AJAX handlers (e.g. login requests) complete before we check
                # whether anything changed.
                try:
                    await page.wait_for_load_state("networkidle", timeout=5_000)
                except Exception:  # noqa: BLE001
                    pass  # networkidle timeout is non-fatal — check state anyway
                # Re-check URL — a delayed navigation may have started.
                if page.url != url_before:
                    title = await page.title()
                    log.info("[browser] clicked element (delayed nav)", cid=conv_id[:8], text=text, title=title)
                    return f"OK: clicked '{text}'. Now on: '{title}' ({page.url})."
                dom_count_after = await page.locator("*").count()
                visible_count_after = await page.evaluate(_JS_VISIBLE_COUNT)
                if dom_count_after != dom_count_before or visible_count_after != visible_count_before:
                    # Wait for AJAX data loads triggered by this click to finish.
                    # networkidle catches most XHR/fetch calls; the extra fixed
                    # wait handles lazy loaders that fire after networkidle.
                    try:
                        await page.wait_for_load_state("networkidle", timeout=3_000)
                    except Exception:  # noqa: BLE001
                        pass
                    await page.wait_for_timeout(1_500)
                    log.info("[browser] click interaction", cid=conv_id[:8], text=text, via=via)
                    return (
                        f"OK (interaction): clicked '{text}'. "
                        f"Page updated (DOM changed) — use list_page_elements to see new elements."
                    )
                log.info("[browser] click no-nav", cid=conv_id[:8], text=text, via=via)
                return (
                    f"OK (no navigation): clicked '{text}'. "
                    f"URL and DOM unchanged — this element had no effect."
                )

            # Pass 1: iterate ALL Playwright text matches, trying each visible one.
            # Don't stop at the first visible match — a heading may be visible but
            # inert; the actual submit button is the next visible match.
            locators = page.get_by_text(element_text, exact=False)
            count = await locators.count()
            for i in range(count):
                loc = locators.nth(i)
                if await loc.is_visible():
                    await loc.click(timeout=_NAV_TIMEOUT_MS)
                    result = await _post_click_result(element_text, "playwright")
                    # If this click caused a real change, return it.
                    # If it had no effect, reset baseline and try the next match.
                    if not result.startswith("OK (no navigation):"):
                        return result
                    dom_count_before = await page.locator("*").count()

            # Pass 2: JS fallback — find any visible a/button/role element whose
            # textContent includes the target and is attached to the layout.
            safe_text = element_text.replace("'", "\\'")
            clicked = await page.evaluate(f"""
                () => {{
                    const els = document.querySelectorAll('a, button, [role="button"], [role="link"]');
                    for (const el of els) {{
                        if (
                            el.textContent.includes('{safe_text}') &&
                            el.offsetParent !== null
                        ) {{
                            el.click();
                            return true;
                        }}
                    }}
                    return false;
                }}
            """)
            if clicked:
                return await _post_click_result(element_text, "js")

            return (
                f"ERROR: no visible element found with text '{element_text}'. "
                "Use list_page_elements to see available elements and pick an exact label."
            )
        except RuntimeError as e:
            return f"ERROR: browser not available — {e}"
        except Exception as e:  # noqa: BLE001
            log.warning("[browser] click_web_element failed", text=element_text, err=str(e))
            return (
                f"ERROR: could not click '{element_text}' — {e}. "
                "Try list_page_elements to see available elements."
            )

    @tool
    async def fill_web_input(label: str, value: str) -> str:
        """Type text into a form input field identified by its label or placeholder.

        Tries to find the input by label text first, then by placeholder, then
        by aria-label. Clears any existing value before typing.

        Args:
            label: Label, placeholder, or aria-label of the input field.
            value: Text to type into the field.
        """
        try:
            page = await browser.get_page(conv_id)
            # Try label → placeholder → aria-label in order.
            for locator in [
                page.get_by_label(label, exact=False),
                page.get_by_placeholder(label, exact=False),
                page.locator(f'[aria-label*="{label}" i]'),
            ]:
                try:
                    await locator.first.fill(value, timeout=5_000)
                    log.info("[browser] filled input", cid=conv_id[:8], label=label)
                    return f"OK: filled '{label}'."
                except Exception:  # noqa: BLE001
                    continue
            return (
                f"ERROR: could not find input '{label}'. "
                "Try list_page_elements to see available form fields."
            )
        except RuntimeError as e:
            return f"ERROR: browser not available — {e}"
        except Exception as e:  # noqa: BLE001
            log.warning("[browser] fill_web_input failed", label=label, err=str(e))
            return f"ERROR: could not fill '{label}' — {e}"

    @tool
    async def select_dropdown_option(label: str, option: str) -> str:
        """Select an option from a <select> dropdown by its label and option text.

        Finds the dropdown by its label, name, or aria-label, then selects
        the option whose visible text matches `option`.

        Args:
            label: Label, name, or aria-label of the <select> element.
            option: Visible text of the option to select.
        """
        try:
            page = await browser.get_page(conv_id)
            for locator in [
                page.get_by_label(label, exact=False),
                page.locator(f'select[name*="{label}" i]'),
                page.locator(f'select[aria-label*="{label}" i]'),
            ]:
                try:
                    await locator.first.select_option(label=option, timeout=5_000)
                    log.info("[browser] selected option", cid=conv_id[:8], label=label, option=option)
                    return f"OK: selected '{option}' from '{label}'."
                except Exception:  # noqa: BLE001
                    continue
            # Fallback 1: find any visible native <select> with the option.
            try:
                for sel in await page.locator("select").all():
                    if not await sel.is_visible():
                        continue
                    try:
                        await sel.select_option(label=option, timeout=5_000)
                        log.info("[browser] selected option (native fallback)", cid=conv_id[:8], option=option)
                        return f"OK: selected '{option}'."
                    except Exception:  # noqa: BLE001
                        continue
            except Exception:  # noqa: BLE001
                pass

            # Fallback 2: custom JS dropdown (select2/chosen/etc.).
            # Strategy: open the dropdown trigger, then click the option element.
            try:
                url_before = page.url
                # Step A: open the dropdown by clicking its visible trigger.
                # select2 renders a <span class="select2-container"> wrapper.
                # Clicking any of these opens the options list.
                for trigger_sel in [
                    "span.select2-selection",
                    "div.select2-selection",
                    ".select2-container",
                    ".chosen-container",
                    "[class*='select2']",
                ]:
                    try:
                        triggers = page.locator(trigger_sel)
                        count = await triggers.count()
                        for i in range(count):
                            t = triggers.nth(i)
                            if await t.is_visible():
                                await t.click(timeout=3_000)
                                await page.wait_for_timeout(600)
                                break
                        else:
                            continue
                        break
                    except Exception:  # noqa: BLE001
                        continue

                # Step B: find the best-matching option using fuzzy word scoring.
                safe_option = option.replace("'", "\\'").lower()
                clicked_text: str = await page.evaluate(f"""
                    () => {{
                        const query = '{safe_option}';
                        const words = query.split(/\\s+/).filter(w => w.length > 1);
                        let best = null, bestScore = -1;
                        const candidates = document.querySelectorAll(
                            '[role="option"], li.select2-results__option, '
                            + 'li.chosen-result, .select2-results li, '
                            + 'ul.select2-results__options li'
                        );
                        for (const el of candidates) {{
                            if (!el.offsetParent) continue;
                            const txt = el.innerText.trim().toLowerCase();
                            // Exact match wins immediately
                            if (txt === query) {{ el.click(); return el.innerText.trim(); }}
                            // Score by matching words
                            const score = words.filter(w => txt.includes(w)).length;
                            if (score > bestScore) {{ bestScore = score; best = el; }}
                        }}
                        if (best && bestScore > 0) {{
                            best.click();
                            return best.innerText.trim();
                        }}
                        return '';
                    }}
                """)
                if clicked_text:
                    await page.wait_for_load_state("domcontentloaded", timeout=_NAV_TIMEOUT_MS)
                    log.info("[browser] selected option (custom dropdown)", cid=conv_id[:8], option=clicked_text)
                    nav_msg = (f" Now on: '{await page.title()}' ({page.url})."
                               if page.url != url_before else "")
                    return f"OK: selected '{clicked_text}'.{nav_msg}"
            except Exception:  # noqa: BLE001
                pass

            # Fallback 3: JS direct value injection — bypasses the UI entirely.
            # Works for select2/chosen/custom widgets backed by a hidden <select>.
            # Finds the best-matching option across ALL <select> elements (including
            # hidden ones), sets its value, and fires the change events that select2
            # listens to so the widget display updates correctly.
            safe_option = option.replace("'", "\\'").lower()
            safe_words = [w.replace("'", "\\'") for w in option.lower().split() if len(w) > 1]
            words_js = "[" + ", ".join(f"'{w}'" for w in safe_words) + "]"
            selected_text: str = await page.evaluate(f"""
                () => {{
                    const query = '{safe_option}';
                    const words = {words_js};
                    const threshold = Math.ceil(words.length * 0.6) || 1;
                    for (const sel of document.querySelectorAll('select')) {{
                        let bestOpt = null, bestScore = -1;
                        for (const opt of sel.options) {{
                            if (!opt.value) continue;
                            const txt = opt.text.trim().toLowerCase();
                            if (txt === query) {{ bestOpt = opt; bestScore = words.length + 1; break; }}
                            const score = words.filter(w => txt.includes(w)).length;
                            if (score > bestScore) {{ bestScore = score; bestOpt = opt; }}
                        }}
                        if (bestOpt && bestScore >= threshold) {{
                            sel.value = bestOpt.value;
                            sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            if (typeof $ !== 'undefined') $(sel).trigger('change');
                            return bestOpt.text.trim();
                        }}
                    }}
                    return '';
                }}
            """)
            if selected_text:
                await page.wait_for_timeout(1_000)  # let the page react to the change
                log.info("[browser] selected option (JS injection)", cid=conv_id[:8], option=selected_text)
                return f"OK: selected '{selected_text}'."

            return (
                f"ERROR: could not find dropdown '{label}' or option '{option}'. "
                "Use read_form_data to see available select options."
            )
        except RuntimeError as e:
            return f"ERROR: browser not available — {e}"
        except Exception as e:  # noqa: BLE001
            log.warning("[browser] select_dropdown_option failed", label=label, option=option, err=str(e))
            return f"ERROR: could not select '{option}' from '{label}' — {e}"

    @tool
    async def check_option(label: str) -> str:
        """Check a checkbox or select a radio button by its label.

        Finds the input by its associated label text or aria-label and
        checks it. For checkboxes, toggles the current state. For radio
        buttons, always selects.

        Args:
            label: Visible label text or aria-label of the checkbox / radio button.
        """
        try:
            page = await browser.get_page(conv_id)
            for locator in [
                page.get_by_label(label, exact=False),
                page.locator(f'[aria-label*="{label}" i]'),
            ]:
                try:
                    el = locator.first
                    input_type = await el.get_attribute("type") or ""
                    if input_type == "radio":
                        await el.check(timeout=5_000)
                        log.info("[browser] selected radio", cid=conv_id[:8], label=label)
                        return f"OK: selected radio '{label}'."
                    is_checked = await el.is_checked()
                    if is_checked:
                        await el.uncheck(timeout=5_000)
                        return f"OK: unchecked '{label}'."
                    await el.check(timeout=5_000)
                    log.info("[browser] checked", cid=conv_id[:8], label=label)
                    return f"OK: checked '{label}'."
                except Exception:  # noqa: BLE001
                    continue
            # JS fallback: find by associated label text
            safe_label = label.replace("'", "\\'")
            toggled = await page.evaluate(f"""
                () => {{
                    for (const lbl of document.querySelectorAll('label')) {{
                        if (lbl.textContent.includes('{safe_label}') && lbl.offsetParent !== null) {{
                            const inp = lbl.control ||
                                document.querySelector('#' + CSS.escape(lbl.getAttribute('for') || ''));
                            if (inp) {{ inp.click(); return true; }}
                        }}
                    }}
                    return false;
                }}
            """)
            if toggled:
                return f"OK: toggled '{label}'."
            return (
                f"ERROR: could not find checkbox/radio '{label}'. "
                "Try list_page_elements to see available inputs."
            )
        except RuntimeError as e:
            return f"ERROR: browser not available — {e}"
        except Exception as e:  # noqa: BLE001
            log.warning("[browser] check_option failed", label=label, err=str(e))
            return f"ERROR: could not check '{label}' — {e}"

    @tool
    async def fill_date(label: str, date: str) -> str:
        """Fill a date input field with a date value.

        Handles both standard HTML5 date inputs (YYYY-MM-DD format) and
        custom JS date picker widgets (click to open, type date, press Tab).

        Args:
            label: Label, placeholder, or aria-label of the date field.
            date: Date string. Use ISO format YYYY-MM-DD for HTML5 inputs,
                or the format the site expects for custom pickers.
        """
        try:
            page = await browser.get_page(conv_id)
            # Pass 1: try standard fill (works for <input type="date">)
            for locator in [
                page.get_by_label(label, exact=False),
                page.get_by_placeholder(label, exact=False),
                page.locator(f'input[type="date"][name*="{label}" i]'),
                page.locator(f'[aria-label*="{label}" i]'),
            ]:
                try:
                    await locator.first.fill(date, timeout=5_000)
                    log.info("[browser] filled date", cid=conv_id[:8], label=label, date=date)
                    return f"OK: filled date field '{label}' with '{date}'."
                except Exception:  # noqa: BLE001
                    continue
            # Pass 2: click to open custom picker, then type and confirm
            for locator in [
                page.get_by_label(label, exact=False),
                page.locator(f'[aria-label*="{label}" i]'),
            ]:
                try:
                    el = locator.first
                    await el.click(timeout=5_000)
                    await el.type(date, timeout=5_000)
                    await page.keyboard.press("Tab")
                    log.info("[browser] typed date", cid=conv_id[:8], label=label, date=date)
                    return f"OK: typed date '{date}' into '{label}'."
                except Exception:  # noqa: BLE001
                    continue
            return (
                f"ERROR: could not find date field '{label}'. "
                "Try list_page_elements to see available inputs."
            )
        except RuntimeError as e:
            return f"ERROR: browser not available — {e}"
        except Exception as e:  # noqa: BLE001
            log.warning("[browser] fill_date failed", label=label, date=date, err=str(e))
            return f"ERROR: could not fill date '{label}' — {e}"

    @tool
    async def scroll_web_page(direction: Literal["up", "down"]) -> str:
        """Scroll the current page up or down to reveal more content.

        Scrolls by roughly one viewport height. Use this when content is cut
        off or you need to see more of the page before reading or clicking.

        Args:
            direction: "up" or "down".
        """
        try:
            page = await browser.get_page(conv_id)
            delta = 600 if direction == "down" else -600
            await page.evaluate(f"window.scrollBy(0, {delta})")
            return f"OK: scrolled {direction}."
        except RuntimeError as e:
            return f"ERROR: browser not available — {e}"
        except Exception as e:  # noqa: BLE001
            log.warning("[browser] scroll_web_page failed", direction=direction, err=str(e))
            return f"ERROR: could not scroll — {e}"

    @tool
    async def go_web_back() -> str:
        """Navigate the browser back to the previous page.

        Use this to undo a click and return to the previous page.
        Returns the title of the page you land on.
        """
        try:
            page = await browser.get_page(conv_id)
            await page.go_back(wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
            title = await page.title()
            log.info("[browser] went back", cid=conv_id[:8], title=title)
            return f"OK: went back. Now on: '{title}'."
        except RuntimeError as e:
            return f"ERROR: browser not available — {e}"
        except Exception as e:  # noqa: BLE001
            log.warning("[browser] go_web_back failed", err=str(e))
            return f"ERROR: could not go back — {e}"

    @tool
    async def read_form_data() -> str:
        """Read all visible form field labels and their current values.

        Use this when the goal involves finding a specific value (name, date,
        ID, etc.) and read_web_page_content doesn't show it. Extracts data
        directly from input/select/textarea elements, bypassing nav menus.

        Returns structured output like:
            Father's Name: Neela Venkateswara Rao
            Occupation: Engineer
            Mobile: 9876543210
        """
        try:
            page = await browser.get_page(conv_id)
            output: str = await page.evaluate("""
                () => {
                    function isGoodHeader(text) {
                        // Reject timestamps (HH:MM AM/PM) and overly long strings
                        // (page titles). We want short accordion/tab labels only.
                        if (!text || text.length > 60) return false;
                        if (/\\d{1,2}:\\d{2}/.test(text)) return false;
                        return true;
                    }
                    function getSectionHeader(el) {
                        let node = el.parentElement;
                        let depth = 0;
                        while (node && node !== document.body && depth < 4) {
                            // Look for a SHORT preceding sibling that could be a label
                            const kids = Array.from(node.children);
                            for (const kid of kids) {
                                if (kid.contains(el)) break;
                                const txt = (kid.innerText || '').trim().split('\\n')[0].trim();
                                if (isGoodHeader(txt)) return txt;
                            }
                            // Check the parent element's own direct text node
                            const direct = Array.from(node.childNodes)
                                .filter(n => n.nodeType === 3)
                                .map(n => n.textContent.trim())
                                .find(t => t.length > 0) || '';
                            if (isGoodHeader(direct)) return direct;
                            node = node.parentElement;
                            depth++;
                        }
                        return null;
                    }

                    const sections = {};
                    const sectionOrder = [];
                    const inputs = document.querySelectorAll('input, select, textarea');

                    for (const el of inputs) {
                        // Include hidden <select> elements — they often back custom
                        // JS widgets (select2) and still hold the options list.
                        const isVisible = el.offsetParent !== null;
                        if (!isVisible && el.tagName !== 'SELECT') continue;
                        // Determine section
                        const section = getSectionHeader(el) || 'General';
                        if (!sections[section]) {
                            sections[section] = [];
                            sectionOrder.push(section);
                        }
                        // Determine label
                        let label = '';
                        if (el.labels && el.labels.length > 0) {
                            label = el.labels[0].innerText.trim();
                        }
                        if (!label) {
                            const prev = el.previousElementSibling;
                            if (prev && ['LABEL','SPAN','TD','TH','P'].includes(prev.tagName)) {
                                label = prev.innerText.trim();
                            }
                        }
                        if (!label) {
                            label = (el.getAttribute('aria-label') ||
                                     el.getAttribute('placeholder') ||
                                     el.getAttribute('name') || '').trim();
                        }
                        if (!label) continue;
                        // Determine value
                        let value = '';
                        if (el.tagName === 'SELECT') {
                            const currentText = el.options[el.selectedIndex]
                                ? el.options[el.selectedIndex].text.trim() : '';
                            const opts = Array.from(el.options)
                                .map(o => o.text.trim()).filter(t => t)
                                .slice(0, 10).join(' | ');
                            value = currentText
                                ? currentText + (opts ? ' (options: ' + opts + ')' : '')
                                : opts ? '(choose one of: ' + opts + ')' : '(empty)';
                        } else {
                            value = el.value.trim();
                        }
                        sections[section].push(`  ${label}: ${value || '(empty)'}`);
                    }

                    if (sectionOrder.length === 0) return '';
                    return sectionOrder
                        .map(s => `[${s}]\\n` + sections[s].join('\\n'))
                        .join('\\n\\n');
                }
            """)
            if not output or not output.strip():
                return "OK: no form fields found on this page."
            return "Form field values on current page:\n" + output
        except RuntimeError as e:
            return f"ERROR: browser not available — {e}"
        except Exception as e:  # noqa: BLE001
            log.warning("[browser] read_form_data failed", err=str(e))
            return f"ERROR: could not read form data — {e}"

    @tool
    async def read_table_data() -> str:
        """Extract all visible tables from the current page as structured text.

        Use this when the goal involves grades, CGPA, SGPA, marks, scores,
        fees, schedules, or any tabular data — especially when read_form_data
        does not show the expected values because they live in a table, not
        form inputs.

        Returns each table labelled [Table N] with rows joined by ' | '.
        """
        try:
            page = await browser.get_page(conv_id)
            output: str = await page.evaluate("""
                () => {
                    const tables = Array.from(document.querySelectorAll('table'))
                        .filter(t => t.offsetParent !== null);
                    if (!tables.length) return '';
                    return tables.map((t, i) => {
                        const rows = Array.from(t.querySelectorAll('tr'))
                            .map(r => Array.from(r.querySelectorAll('th, td'))
                                           .map(c => c.innerText.trim())
                                           .join(' | '))
                            .filter(r => r.trim());
                        return '[Table ' + (i + 1) + ']\\n' + rows.join('\\n');
                    }).join('\\n\\n');
                }
            """)
            if not output or not output.strip():
                return "OK: no tables found on this page."
            return "Table data on current page:\n" + output[:6000]
        except RuntimeError as e:
            return f"ERROR: browser not available — {e}"
        except Exception as e:  # noqa: BLE001
            log.warning("[browser] read_table_data failed", err=str(e))
            return f"ERROR: could not read table data — {e}"

    @tool
    async def press_key(key: str) -> str:
        """Press a keyboard key on the currently focused element or page.

        Use this to submit forms (Enter), move focus (Tab), or dismiss
        dialogs (Escape). Especially useful when a login/submit button click
        fails — focus the password field then press Enter to submit the form.

        Args:
            key: Key name, e.g. "Enter", "Tab", "Escape", "ArrowDown".
        """
        try:
            page = await browser.get_page(conv_id)
            url_before = page.url
            dom_count_before = await page.locator("*").count()
            await page.keyboard.press(key)
            try:
                await page.wait_for_load_state("networkidle", timeout=5_000)
            except Exception:  # noqa: BLE001
                pass
            # Extra wait to catch delayed post-submission redirects that settle
            # after networkidle (e.g. login → session creation → redirect).
            await page.wait_for_timeout(2_000)
            title = await page.title()
            url_after = page.url
            dom_count_after = await page.locator("*").count()
            if url_after != url_before:
                log.info("[browser] press_key navigated", cid=conv_id[:8], key=key, title=title)
                return f"OK: pressed {key}. Navigated to: '{title}' ({url_after})."
            if dom_count_after != dom_count_before:
                log.info("[browser] press_key interaction", cid=conv_id[:8], key=key)
                return f"OK: pressed {key}. Page updated. Current: '{title}' ({url_after})."
            log.info("[browser] press_key", cid=conv_id[:8], key=key)
            return f"OK: pressed {key}. Current page: '{title}' ({url_after})."
        except RuntimeError as e:
            return f"ERROR: browser not available — {e}"
        except Exception as e:  # noqa: BLE001
            log.warning("[browser] press_key failed", key=key, err=str(e))
            return f"ERROR: could not press '{key}' — {e}"

    return [
        open_web_page,
        read_web_page_content,
        list_page_elements,
        click_web_element,
        fill_web_input,
        select_dropdown_option,
        check_option,
        fill_date,
        read_form_data,
        read_table_data,
        press_key,
        scroll_web_page,
        go_web_back,
    ]
