from __future__ import annotations

import logging
import re
from typing import Awaitable, Callable, Literal

from ..config import Config
from ..core.contracts import MessageContext, SendAction
from ..core.scheduler import Scheduler

SendFn = Callable[[str, str, bool], Awaitable[int | None]]
CommandKind = Literal["tianji", "rumeng"]


class ShiqiePlugin:
    """侍妾：定期代卜、寻图，并在碎片集齐后拼图。"""

    name = "shiqie"
    priority = 10

    _CMD_TIANJI = ".天机代卜"
    _CMD_RUMENG = ".入梦寻图"
    _CMD_PINTU = ".拼图"
    _TIANJI_LOOP_KEY = "shiqie.tianji.loop"
    _RUMENG_LOOP_KEY = "shiqie.rumeng.loop"
    _PROGRESS_RE = re.compile(r"当前进度[:：]\s*(\d+)\s*/\s*(\d+)")

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self._logger = logger
        self.enabled = bool(config.enable_shiqie)
        self._tianji_interval_seconds = max(60, int(config.shiqie_tianji_interval_seconds))
        self._rumeng_interval_seconds = max(60, int(config.shiqie_rumeng_interval_seconds))
        self._scheduler: Scheduler | None = None
        self._send: SendFn | None = None
        self._tianji_request_msg_id: int | None = None
        self._rumeng_request_msg_id: int | None = None

    async def bootstrap(self, scheduler: Scheduler, send: SendFn) -> None:
        if not self.enabled:
            return
        self._scheduler = scheduler
        self._send = send
        await self._schedule_tianji_loop(0.0)
        await self._schedule_rumeng_loop(0.0)

    async def on_message(self, ctx: MessageContext) -> list[SendAction] | None:
        if not self.enabled:
            return None
        text = (ctx.text or "").strip()
        if not text or text.startswith(".") or "拼合成功" in text:
            return None

        kind = self._feedback_kind(ctx, text)
        if kind is None:
            return None

        if "请在" in text and "后再试" in text:
            remaining = self._parse_duration_seconds(text)
            if remaining is not None:
                await self._schedule_kind(kind, float(remaining))
            return None

        await self._schedule_kind(kind, self._default_interval(kind))
        if kind == "rumeng" and self._progress_complete(text):
            return [
                SendAction(
                    plugin=self.name,
                    text=self._CMD_PINTU,
                    reply_to_topic=True,
                    key=f"shiqie.pintu.{ctx.message_id}",
                )
            ]
        return None

    def _feedback_kind(self, ctx: MessageContext, text: str) -> CommandKind | None:
        if self._tianji_request_msg_id is not None and ctx.reply_to_msg_id == self._tianji_request_msg_id:
            return "tianji"
        if self._rumeng_request_msg_id is not None and ctx.reply_to_msg_id == self._rumeng_request_msg_id:
            return "rumeng"
        if self._PROGRESS_RE.search(text):
            return "rumeng"
        return None

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

    def _progress_complete(self, text: str) -> bool:
        match = self._PROGRESS_RE.search(text)
        if match is None:
            return False
        current = int(match.group(1))
        total = int(match.group(2))
        return total > 0 and current >= total

    def _default_interval(self, kind: CommandKind) -> float:
        if kind == "tianji":
            return float(self._tianji_interval_seconds)
        return float(self._rumeng_interval_seconds)

    async def _schedule_kind(self, kind: CommandKind, delay_seconds: float) -> None:
        if kind == "tianji":
            await self._schedule_tianji_loop(delay_seconds)
            return
        await self._schedule_rumeng_loop(delay_seconds)

    async def _schedule_tianji_loop(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return

        async def _runner() -> None:
            await self._tianji_loop()

        await self._scheduler.schedule(
            key=self._TIANJI_LOOP_KEY,
            delay_seconds=max(0.0, delay_seconds),
            action=_runner,
        )

    async def _schedule_rumeng_loop(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return

        async def _runner() -> None:
            await self._rumeng_loop()

        await self._scheduler.schedule(
            key=self._RUMENG_LOOP_KEY,
            delay_seconds=max(0.0, delay_seconds),
            action=_runner,
        )

    async def _tianji_loop(self) -> None:
        if not self.enabled or self._send is None:
            return
        self._tianji_request_msg_id = await self._send(self.name, self._CMD_TIANJI, True)
        await self._schedule_tianji_loop(float(self._tianji_interval_seconds))

    async def _rumeng_loop(self) -> None:
        if not self.enabled or self._send is None:
            return
        self._rumeng_request_msg_id = await self._send(self.name, self._CMD_RUMENG, True)
        await self._schedule_rumeng_loop(float(self._rumeng_interval_seconds))
