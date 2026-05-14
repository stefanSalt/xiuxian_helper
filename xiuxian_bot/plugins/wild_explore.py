from __future__ import annotations

import logging
import re
from typing import Awaitable, Callable

from ..config import Config
from ..core.contracts import MessageContext, SendAction
from ..core.scheduler import Scheduler

SendFn = Callable[[str, str, bool], Awaitable[int | None]]


class WildExplorePlugin:
    """野外历练：按固定间隔发送历练指令。"""

    name = "wild_explore"
    priority = 10

    _CMD_EXPLORE = ".野外历练"
    _LOOP_KEY = "wild_explore.loop"
    _TITLE = "【野外历练】"

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self._logger = logger
        self.enabled = bool(config.enable_wild_explore)
        self._strategy = config.wild_explore_strategy
        self._interval_seconds = max(60, int(config.wild_explore_interval_seconds))
        self._scheduler: Scheduler | None = None
        self._send: SendFn | None = None

    async def bootstrap(self, scheduler: Scheduler, send: SendFn) -> None:
        if not self.enabled:
            return
        self._scheduler = scheduler
        self._send = send
        await self._schedule_loop(0.0)

    async def on_message(self, ctx: MessageContext) -> list[SendAction] | None:
        text = (ctx.text or "").strip()
        if self._TITLE not in text:
            return None
        if "山中灵机未复" in text and "后再来" in text:
            remaining = self._parse_duration_seconds(text)
            if remaining is not None:
                await self._schedule_loop(float(remaining))
            return None
        await self._schedule_loop(float(self._interval_seconds))
        return None

    def _command(self) -> str:
        return f"{self._CMD_EXPLORE} {self._strategy}"

    def _parse_duration_seconds(self, text: str) -> int | None:
        matched = False

        def _pick(pattern: str) -> int:
            nonlocal matched
            match = re.search(pattern, text)
            if match is None:
                return 0
            matched = True
            return int(match.group(1))

        days = _pick(r"(\d+)\s*天")
        hours = _pick(r"(\d+)\s*小时")
        minutes = _pick(r"(\d+)\s*(?:分钟|分)")
        seconds = _pick(r"(\d+)\s*秒")
        if not matched:
            return None
        return days * 86400 + hours * 3600 + minutes * 60 + seconds

    async def _schedule_loop(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return

        async def _runner() -> None:
            await self._explore_loop()

        await self._scheduler.schedule(
            key=self._LOOP_KEY,
            delay_seconds=max(0.0, delay_seconds),
            action=_runner,
        )

    async def _send_command(self) -> None:
        if not self.enabled or self._send is None:
            return
        await self._send(self.name, self._command(), True)

    async def _explore_loop(self) -> None:
        if not self.enabled or self._send is None:
            return
        await self._send_command()
        await self._schedule_loop(float(self._interval_seconds))
