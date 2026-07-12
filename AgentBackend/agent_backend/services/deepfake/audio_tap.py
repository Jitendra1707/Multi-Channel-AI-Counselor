from __future__ import annotations
import asyncio
_tap: asyncio.Queue[bytes] | None = None
def register(q: asyncio.Queue[bytes]) -> None:
    global _tap; _tap = q
def unregister() -> None:
    global _tap; _tap = None
def get() -> asyncio.Queue[bytes] | None:
    return _tap
