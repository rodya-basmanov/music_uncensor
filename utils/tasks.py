"""Фоновые задачи: держим ссылку и логируем исключения.

asyncio держит только weak-ссылку на задачи, поэтому fire-and-forget
create_task может быть собран GC посреди выполнения (не выполнятся finally),
а исключения теряются. spawn() это исправляет.
"""

import asyncio
from typing import Coroutine, Set

from utils.logger import get_logger

log = get_logger("tasks")

_bg: Set[asyncio.Task] = set()


def spawn(coro: Coroutine, name: str = "") -> asyncio.Task:
    """Запускает корутину в фоне, удерживая ссылку и логируя исключения."""
    task = asyncio.ensure_future(coro)
    _bg.add(task)

    def _done(t: asyncio.Task) -> None:
        _bg.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            log.error("background_task_error", name=name, error=str(exc))
            try:
                import sentry_sdk
                sentry_sdk.capture_exception(exc)
            except Exception:
                pass

    task.add_done_callback(_done)
    return task


def background_count() -> int:
    return len(_bg)
