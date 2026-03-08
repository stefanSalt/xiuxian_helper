from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from ..config import Config
from ..core.contracts import MessageContext, SendAction
from ..core.scheduler import Scheduler

SendFn = Callable[[str, str, bool], Awaitable[int | None]]


class AutoYuanyingPlugin:
    """元婴期自动化：探寻裂缝 + 元婴出窍。"""

    name = "yuanying"
    priority = 45

    _CMD_LIEFENG = ".探寻裂缝"
    _CMD_CHUQIAO = ".元婴出窍"
    _LIEFENG_INTERVAL_SECONDS = 12 * 60 * 60
    _CHUQIAO_INTERVAL_SECONDS = 8 * 60 * 60
    _BUFFER_SECONDS = 5
    _RETRY_DELAY_SECONDS = 5

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self._config = config
        self._logger = logger
        self.enabled = bool(config.enable_yuanying)

        self._scheduler: Scheduler | None = None
        self._send: SendFn | None = None
        self._liefeng_blocked_until: datetime | None = None
        self._chuqiao_blocked_until: datetime | None = None

        if self.enabled:
            self._logger.info(
                "yuanying_plugin_enabled liefeng_interval_seconds=%s chuqiao_interval_seconds=%s",
                self._LIEFENG_INTERVAL_SECONDS,
                self._CHUQIAO_INTERVAL_SECONDS,
            )

    def _is_mine(self, ctx: MessageContext, text: str) -> bool:
        return bool(ctx.is_reply_to_me or (self._config.my_name and self._config.my_name in text))

    def _parse_duration_seconds(self, text: str) -> int | None:
        def _pick(unit: str) -> int:
            match = re.search(rf"(\d+)\s*{unit}", text)
            return int(match.group(1)) if match else 0

        days = _pick("天")
        hours = _pick("小时")
        minutes = _pick("分钟")
        seconds = _pick("秒")
        total = days * 86400 + hours * 3600 + minutes * 60 + seconds
        return total if total > 0 else None

    async def bootstrap(self, scheduler: Scheduler, send: SendFn) -> None:
        if not self.enabled:
            return
        self._scheduler = scheduler
        self._send = send
        await self._schedule_liefeng_loop(0.0)
        await self._schedule_chuqiao_loop(0.0)

    async def _schedule_liefeng_loop(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return

        async def _runner() -> None:
            await self._liefeng_loop()

        await self._scheduler.schedule(
            key="yuanying.liefeng.loop",
            delay_seconds=delay_seconds,
            action=_runner,
        )

    async def _schedule_chuqiao_loop(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return

        async def _runner() -> None:
            await self._chuqiao_loop()

        await self._scheduler.schedule(
            key="yuanying.chuqiao.loop",
            delay_seconds=delay_seconds,
            action=_runner,
        )

    async def _liefeng_loop(self) -> None:
        if not self.enabled or self._send is None:
            return

        now = datetime.now()
        if self._liefeng_blocked_until is not None and now < self._liefeng_blocked_until:
            await self._schedule_liefeng_loop((self._liefeng_blocked_until - now).total_seconds())
            return

        await self._send(self.name, self._CMD_LIEFENG, True)
        await self._schedule_liefeng_loop(float(self._LIEFENG_INTERVAL_SECONDS))

    async def _chuqiao_loop(self) -> None:
        if not self.enabled or self._send is None:
            return

        now = datetime.now()
        if self._chuqiao_blocked_until is not None and now < self._chuqiao_blocked_until:
            await self._schedule_chuqiao_loop((self._chuqiao_blocked_until - now).total_seconds())
            return

        await self._send(self.name, self._CMD_CHUQIAO, True)
        await self._schedule_chuqiao_loop(float(self._CHUQIAO_INTERVAL_SECONDS))

    async def _set_liefeng_next(self, delay_seconds: float) -> None:
        self._liefeng_blocked_until = datetime.now() + timedelta(seconds=delay_seconds)
        await self._schedule_liefeng_loop(delay_seconds)

    async def _set_chuqiao_next(self, delay_seconds: float) -> None:
        self._chuqiao_blocked_until = datetime.now() + timedelta(seconds=delay_seconds)
        await self._schedule_chuqiao_loop(delay_seconds)

    async def on_message(self, ctx: MessageContext) -> list[SendAction] | None:
        text = (ctx.text or "").strip()
        if not text or text.startswith("."):
            return None
        if not self._is_mine(ctx, text):
            return None

        if "空间裂缝尚未稳定" in text and "再行探寻" in text:
            remaining = self._parse_duration_seconds(text)
            if remaining is None:
                return None
            await self._set_liefeng_next(float(remaining + self._BUFFER_SECONDS))
            return None

        if "探寻成功" in text:
            await self._set_liefeng_next(float(self._LIEFENG_INTERVAL_SECONDS))
            return None

        if "元婴遁逃" in text and "虚弱期" in text:
            remaining = self._parse_duration_seconds(text)
            if remaining is None:
                return None
            await self._set_liefeng_next(float(remaining + self._BUFFER_SECONDS))
            return None

        if "遭遇风暴" in text or "不敌败退" in text:
            return [
                SendAction(
                    plugin=self.name,
                    text=self._CMD_LIEFENG,
                    reply_to_topic=True,
                    delay_seconds=float(self._RETRY_DELAY_SECONDS),
                    key="yuanying.action.liefeng.retry",
                )
            ]

        if "它将在外云游8小时" in text or "下一次发言时若已归来" in text:
            await self._set_chuqiao_next(float(self._CHUQIAO_INTERVAL_SECONDS))
            return None

        if "元神出窍" in text and "无法分身" in text:
            return None

        return None
