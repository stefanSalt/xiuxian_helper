from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable


class Scheduler:
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def schedule(
        self,
        *,
        key: str,
        delay_seconds: float,
        action: Callable[[], Awaitable[None]],
    ) -> None:
        async with self._lock:
            old = self._tasks.get(key)
            if old is not None:
                old.cancel()
            task = asyncio.create_task(self._run(key, delay_seconds, action))
            self._tasks[key] = task

    async def _run(
        self,
        key: str,
        delay_seconds: float,
        action: Callable[[], Awaitable[None]],
    ) -> None:
        try:
            await asyncio.sleep(delay_seconds)
            await action()
        except asyncio.CancelledError:
            return
        except Exception:
            self._logger.exception("scheduled_action_failed key=%s", key)
        finally:
            async with self._lock:
                task = self._tasks.get(key)
                if task is not None and task.done():
                    self._tasks.pop(key, None)

    async def cancel_all(self) -> None:
        async with self._lock:
            tasks = list(self._tasks.values())
            self._tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

