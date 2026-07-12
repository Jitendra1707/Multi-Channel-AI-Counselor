"""web_extractor — self-contained Playwright web/lead-extraction module.

Given a website URL and a plain-English goal, the NavigatorAgent drives a
Playwright browser via LLM-generated actions to extract lead data. Bundled
here with no relation to AegisBackend — logger, context, browser manager, and
LLM settings all resolve within BusinessLayer.

    manager.py          — Playwright/Chromium lifecycle (BrowserManager)
    tools.py            — the 13 Playwright browser-control tools
    navigator_agent.py  — the LLM decision loop that drives those tools
    _base.py            — minimal NavSession / ToolContext the tools close over

Usage::

    from business.web_extractor import BrowserManager, NavigatorAgent, NavSession

    browser = BrowserManager()
    await browser.start(headless=True)
    try:
        agent = NavigatorAgent(
            session=NavSession(conversation_id="run-1"),
            browser=browser,
            credentials={"username": "x", "password": "y"},
        )
        summary = await agent.run(url="https://example.com", goal="Extract the leads")
    finally:
        await browser.stop()
"""

from business.web_extractor._base import NavSession, ToolContext
from business.web_extractor.manager import BrowserManager
from business.web_extractor.navigator_agent import NavigatorAgent
from business.web_extractor.tools import build_tools

__all__ = ["BrowserManager", "NavigatorAgent", "NavSession", "ToolContext", "build_tools"]
