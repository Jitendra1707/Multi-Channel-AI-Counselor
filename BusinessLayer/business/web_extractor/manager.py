"""Playwright browser lifecycle manager (BusinessLayer web_extractor copy).

One shared Chromium `Browser` instance is started by the caller and held for
the lifetime of the extraction run. Each `conversation_id` gets its own
`BrowserContext` (isolated cookies, localStorage, session storage) so auth
state never leaks between runs. A single `Page` lives inside each context.

Usage::

    browser = BrowserManager()
    await browser.start(headless=True)   # launches Chromium
    ...
    await browser.stop()                 # closes all contexts, browser, Playwright

Tools call `get_page(conversation_id)` which lazily creates a context+page
on first access and returns the same page on subsequent calls.

No module-level singleton — instances are created and passed explicitly so the
module carries no shared global state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from business.logging import get_logger

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright

log = get_logger(__name__)


class BrowserManager:
    """Owns the Playwright process and all per-session browser contexts."""

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        # conversation_id → (BrowserContext, Page)
        self._contexts: dict[str, tuple[BrowserContext, Page]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, headless: bool = True) -> None:
        """Launch Playwright + Chromium. Called once at startup.

        Args:
            headless: Run without a visible window (default True for production).
                Pass False to watch the browser navigate in real time.
        """
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=headless)
        log.info("[web_extractor] Chromium launched", headless=headless)

    async def stop(self) -> None:
        """Close all sessions, browser, and Playwright. Called on shutdown."""
        for conv_id in list(self._contexts):
            await self.close_context(conv_id)
        if self._browser:
            try:
                await self._browser.close()
            except Exception as e:  # noqa: BLE001
                log.warning("[web_extractor] error closing browser", err=str(e))
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:  # noqa: BLE001
                log.warning("[web_extractor] error stopping playwright", err=str(e))
        self._browser = None
        self._playwright = None
        log.info("[web_extractor] stopped")

    # ------------------------------------------------------------------
    # Page access
    # ------------------------------------------------------------------

    async def get_page(self, conversation_id: str) -> Page:
        """Return the Page for this session, creating one if needed.

        Raises RuntimeError if `start()` hasn't been called yet.
        """
        if self._browser is None:
            raise RuntimeError(
                "BrowserManager has not been started — call start() first."
            )
        if conversation_id not in self._contexts:
            context: BrowserContext = await self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                java_script_enabled=True,
            )
            page: Page = await context.new_page()
            self._contexts[conversation_id] = (context, page)
            log.debug(
                "[web_extractor] new context created",
                cid=conversation_id[:8],
                total=len(self._contexts),
            )
        return self._contexts[conversation_id][1]

    async def close_context(self, conversation_id: str) -> None:
        """Close and remove the browser context for this session."""
        entry = self._contexts.pop(conversation_id, None)
        if entry is None:
            return
        context, _ = entry
        try:
            await context.close()
        except Exception as e:  # noqa: BLE001
            log.warning("[web_extractor] error closing context", cid=conversation_id[:8], err=str(e))
        log.debug("[web_extractor] context closed", cid=conversation_id[:8])
