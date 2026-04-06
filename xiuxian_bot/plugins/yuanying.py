from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from ..config import Config
from ..core.contracts import MessageContext, SendAction
from ..core.scheduler import Scheduler
from ..core.state_store import SQLiteStateStore, deserialize_datetime, serialize_datetime

SendFn = Callable[[str, str, bool], Awaitable[int | None]]


class AutoYuanyingPlugin:
    """元婴期自动化：探寻裂缝 + 元婴出窍。"""

    name = "yuanying"
    priority = 45

    _CMD_LIEFENG = ".探寻裂缝"
    _CMD_CHUQIAO = ".元婴出窍"
    _CMD_CHUQIAO_STATUS = ".元婴状态"
    _CMD_CHUQIAO_SETTLE = "归来"
    _BUFFER_SECONDS = 5
    _RETRY_DELAY_SECONDS = 5
    _STATUS_RETRY_SECONDS = 120
    _SUMMARY_RECHECK_SECONDS = 15
    _ESCAPE_PAUSE_REASON = "元婴遁逃暂停中，等待手动恢复"
    _LIEFENG_SOURCE_INTERVAL = "interval"
    _LIEFENG_SOURCE_COOLDOWN = "cooldown"
    _LIEFENG_SOURCE_WEAKNESS = "weakness"

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self._config = config
        self._logger = logger
        self.enabled = bool(config.enable_yuanying)
        self._liefeng_enabled = bool(config.enable_yuanying_liefeng)

        self._scheduler: Scheduler | None = None
        self._send: SendFn | None = None
        self._state_store: SQLiteStateStore | None = None
        self._liefeng_blocked_until: datetime | None = None
        self._liefeng_block_source: str | None = None
        self._chuqiao_blocked_until: datetime | None = None
        self._chuqiao_waiting_settle = False
        self._escape_pause_active = False
        self._escape_pause_reason: str | None = None
        self._liefeng_interval_seconds = max(60, int(config.yuanying_liefeng_interval_seconds))
        self._chuqiao_interval_seconds = max(60, int(config.yuanying_chuqiao_interval_seconds))

        if self.enabled:
            self._logger.info(
                "yuanying_plugin_enabled liefeng_enabled=%s liefeng_interval_seconds=%s chuqiao_interval_seconds=%s",
                self._liefeng_enabled,
                self._liefeng_interval_seconds,
                self._chuqiao_interval_seconds,
            )

    def set_state_store(self, state_store: SQLiteStateStore) -> None:
        self._state_store = state_store

    def restore_state(self) -> None:
        if self._state_store is None:
            return
        state = self._state_store.load_state(self.name)
        self._liefeng_blocked_until = deserialize_datetime(state.get("liefeng_blocked_until"))
        source = state.get("liefeng_block_source")
        self._liefeng_block_source = source if isinstance(source, str) and source else None
        self._chuqiao_blocked_until = deserialize_datetime(state.get("chuqiao_blocked_until"))
        self._chuqiao_waiting_settle = bool(state.get("chuqiao_waiting_settle", False))
        self._escape_pause_active = bool(state.get("escape_pause_active", False))
        reason = state.get("escape_pause_reason")
        self._escape_pause_reason = reason if isinstance(reason, str) and reason else None

    def _save_state(self) -> None:
        if self._state_store is None:
            return
        self._state_store.save_state(
            self.name,
            {
                "liefeng_blocked_until": serialize_datetime(self._liefeng_blocked_until),
                "liefeng_block_source": self._liefeng_block_source,
                "chuqiao_blocked_until": serialize_datetime(self._chuqiao_blocked_until),
                "chuqiao_waiting_settle": self._chuqiao_waiting_settle,
                "escape_pause_active": self._escape_pause_active,
                "escape_pause_reason": self._escape_pause_reason,
            },
        )

    def runtime_pause_reason(self) -> str | None:
        if not self.enabled or not self._escape_pause_active:
            return None
        return self._escape_pause_reason or self._ESCAPE_PAUSE_REASON

    def clear_runtime_pause(self, *, clear_progress: bool = False) -> None:
        self._escape_pause_active = False
        self._escape_pause_reason = None
        if clear_progress:
            self._liefeng_blocked_until = None
            self._liefeng_block_source = None
            self._chuqiao_blocked_until = None
            self._chuqiao_waiting_settle = False
        self._save_state()

    def _activate_escape_pause(self) -> None:
        self._escape_pause_active = True
        self._escape_pause_reason = self._ESCAPE_PAUSE_REASON
        self._save_state()
        self._logger.warning("yuanying_escape_pause_activated reason=%s", self._escape_pause_reason)

    def _is_mine(self, ctx: MessageContext, text: str) -> bool:
        return bool(ctx.is_reply_to_me or (self._config.my_name and self._config.my_name in text))

    def _parse_duration_seconds(self, text: str) -> int | None:
        matched = False

        def _pick(unit: str) -> int:
            nonlocal matched
            match = re.search(rf"(\d+)\s*{unit}", text)
            if match:
                matched = True
                return int(match.group(1))
            return 0

        days = _pick("天")
        hours = _pick("小时")
        minutes = _pick("分钟")
        seconds = _pick("秒")
        if not matched:
            return None
        return days * 86400 + hours * 3600 + minutes * 60 + seconds

    def _compact_text(self, text: str) -> str:
        return re.sub(r"\s+", "", text or "")

    def _parse_chuqiao_status_remaining(self, text: str) -> int | None:
        compact = self._compact_text(text)
        if "状态:元神出窍" not in compact or "归来倒计时:" not in compact:
            return None
        return self._parse_duration_seconds(text)

    def _initial_liefeng_delay_seconds(self) -> float:
        now = datetime.now()
        if self._liefeng_blocked_until is not None and now < self._liefeng_blocked_until:
            return max(0.0, (self._liefeng_blocked_until - now).total_seconds())
        return float(self._liefeng_interval_seconds)

    async def bootstrap(self, scheduler: Scheduler, send: SendFn) -> None:
        if not self.enabled:
            return
        self._scheduler = scheduler
        self._send = send
        if self._liefeng_enabled:
            await self._schedule_liefeng_loop(self._initial_liefeng_delay_seconds())
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
        if not self.enabled or not self._liefeng_enabled or self._send is None:
            return

        now = datetime.now()
        if self._liefeng_blocked_until is not None and now < self._liefeng_blocked_until:
            await self._schedule_liefeng_loop((self._liefeng_blocked_until - now).total_seconds())
            return

        await self._send(self.name, self._CMD_LIEFENG, True)
        await self._set_liefeng_next(
            float(self._liefeng_interval_seconds),
            source=self._LIEFENG_SOURCE_INTERVAL,
        )

    async def _chuqiao_loop(self) -> None:
        if not self.enabled or self._send is None:
            return

        now = datetime.now()
        if self._chuqiao_blocked_until is not None and now < self._chuqiao_blocked_until:
            await self._schedule_chuqiao_loop((self._chuqiao_blocked_until - now).total_seconds())
            return

        if self._chuqiao_waiting_settle:
            await self._send(self.name, self._CMD_CHUQIAO_SETTLE, True)
            self._chuqiao_waiting_settle = False
            self._save_state()
            await self._schedule_chuqiao_loop(float(self._SUMMARY_RECHECK_SECONDS))
            return

        await self._send(self.name, self._CMD_CHUQIAO_STATUS, True)
        await self._schedule_chuqiao_loop(float(self._STATUS_RETRY_SECONDS))

    async def _set_liefeng_next(self, delay_seconds: float, *, source: str | None) -> None:
        self._liefeng_blocked_until = datetime.now() + timedelta(seconds=delay_seconds)
        self._liefeng_block_source = source
        self._save_state()
        if self._liefeng_enabled:
            await self._schedule_liefeng_loop(delay_seconds)

    async def _set_chuqiao_next(self, delay_seconds: float) -> None:
        self._chuqiao_blocked_until = datetime.now() + timedelta(seconds=delay_seconds)
        self._save_state()
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
            await self._set_liefeng_next(
                float(remaining + self._BUFFER_SECONDS),
                source=self._LIEFENG_SOURCE_COOLDOWN,
            )
            return None

        if "探寻成功" in text:
            await self._set_liefeng_next(
                float(self._liefeng_interval_seconds),
                source=self._LIEFENG_SOURCE_INTERVAL,
            )
            return None

        if "元婴遁逃" in text and "虚弱期" in text:
            remaining = self._parse_duration_seconds(text)
            if remaining is None:
                return None
            await self._set_liefeng_next(
                float(remaining + self._BUFFER_SECONDS),
                source=self._LIEFENG_SOURCE_WEAKNESS,
            )
            self._activate_escape_pause()
            return None

        if "遭遇风暴" in text or "不敌败退" in text:
            if not self._liefeng_enabled:
                return None
            return [
                SendAction(
                    plugin=self.name,
                    text=self._CMD_LIEFENG,
                    reply_to_topic=True,
                    delay_seconds=float(self._RETRY_DELAY_SECONDS),
                    key="yuanying.action.liefeng.retry",
                )
            ]

        remaining = self._parse_chuqiao_status_remaining(text)
        if remaining is not None:
            self._chuqiao_waiting_settle = True
            await self._set_chuqiao_next(float(remaining + self._BUFFER_SECONDS))
            return None

        compact = self._compact_text(text)
        if "状态:窍中温养" in compact:
            self._chuqiao_waiting_settle = False
            self._chuqiao_blocked_until = None
            self._save_state()
            return [
                SendAction(
                    plugin=self.name,
                    text=self._CMD_CHUQIAO,
                    reply_to_topic=True,
                    delay_seconds=0.0,
                    key="yuanying.action.chuqiao",
                )
            ]

        if "元神归窍总结" in text:
            self._chuqiao_waiting_settle = False
            self._chuqiao_blocked_until = None
            self._save_state()
            return [
                SendAction(
                    plugin=self.name,
                    text=self._CMD_CHUQIAO,
                    reply_to_topic=True,
                    delay_seconds=0.0,
                    key="yuanying.action.chuqiao",
                )
            ]

        if "它将在外云游8小时" in text or "下一次发言时若已归来" in text:
            self._chuqiao_waiting_settle = True
            await self._set_chuqiao_next(float(self._chuqiao_interval_seconds))
            return None

        if "元神出窍" in text and "无法分身" in text:
            return [
                SendAction(
                    plugin=self.name,
                    text=self._CMD_CHUQIAO_STATUS,
                    reply_to_topic=True,
                    delay_seconds=0.0,
                    key="yuanying.action.chuqiao.status",
                )
            ]

        return None
