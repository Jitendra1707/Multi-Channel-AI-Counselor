"""Worker lifecycle — starts/stops the background loops over the app lifespan."""

from __future__ import annotations

import asyncio

from business.clients import get_aegis_client
from business.config import get_settings
from business.logging import get_logger
from business.services.actions import run_actions_loop
from business.services.analyzer import run_analyzer_loop
from business.services.dialer import run_dialer_loop

log = get_logger(__name__)


class WorkerManager:
    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        s = get_settings()
        self._stop.clear()
        if s.analyzer_enabled:
            self._spawn(run_analyzer_loop(self._stop), "analyzer")
        if s.actions_enabled:
            self._spawn(run_actions_loop(self._stop), "actions")
        if s.dialer_enabled:
            self._spawn(run_dialer_loop(self._stop), "dialer")
        log.info(
            "workers started",
            analyzer=s.analyzer_enabled,
            actions=s.actions_enabled,
            dialer=s.dialer_enabled,
        )

    def _spawn(self, coro, name: str) -> None:
        task = asyncio.create_task(coro, name=f"worker-{name}")
        self._tasks.append(task)

    async def stop(self) -> None:
        self._stop.set()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
        try:
            await get_aegis_client().aclose()
        except Exception:  # noqa: BLE001
            pass
        log.info("workers stopped")
