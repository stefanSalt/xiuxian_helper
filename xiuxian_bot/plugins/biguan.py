from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta

from ..config import Config
from ..core.contracts import MessageContext, SendAction
from ..core.scheduler import Scheduler
from ..core.state_store import SQLiteStateStore, deserialize_datetime, serialize_datetime
from ..domain.parsers import parse_biguan_cooldown_minutes, parse_lingqi_cooldown_seconds


class AutoBiguanPlugin:
    name = "biguan"
    priority = 100

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self._config = config
        self._logger = logger
        self.enabled = bool(
            config.enable_biguan
            and not (config.enable_xinggong and config.enable_xinggong_deep_biguan)
        )
        self._scheduler: Scheduler | None = None
        self._send = None
        self._state_store: SQLiteStateStore | None = None
        self._next_attempt_at: datetime | None = None

    def set_state_store(self, state_store: SQLiteStateStore) -> None:
        self._state_store = state_store

    def restore_state(self) -> None:
        if self._state_store is None:
            return
        state = self._state_store.load_state(self.name)
        self._next_attempt_at = deserialize_datetime(state.get("next_attempt_at"))

    def _save_state(self) -> None:
        if self._state_store is None:
            return
        self._state_store.save_state(
            self.name,
            {"next_attempt_at": serialize_datetime(self._next_attempt_at)},
        )

    async def bootstrap(self, scheduler: Scheduler, send) -> None:
        if not self.enabled:
            return
        self._scheduler = scheduler
        self._send = send
        if self._next_attempt_at is None:
            return
        delay_seconds = max(0.0, (self._next_attempt_at - datetime.now()).total_seconds())
        await self._schedule_next(delay_seconds)

    async def _schedule_next(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return

        async def _runner() -> None:
            await self._run_next()

        await self._scheduler.schedule(
            key="biguan.next",
            delay_seconds=max(0.0, delay_seconds),
            action=_runner,
        )

    async def _run_next(self) -> None:
        if not self.enabled or self._send is None:
            return
        self._next_attempt_at = None
        self._save_state()
        await self._send(self.name, self._config.action_cmd_biguan, True)

    async def _arm_next(self, delay_seconds: float) -> list[SendAction] | None:
        self._next_attempt_at = datetime.now() + timedelta(seconds=delay_seconds)
        self._save_state()
        if self._scheduler is not None and self._send is not None:
            await self._schedule_next(delay_seconds)
            return None
        return [
            SendAction(
                plugin=self.name,
                text=self._config.action_cmd_biguan,
                reply_to_topic=True,
                delay_seconds=delay_seconds,
                key="biguan.next",
            )
        ]

    async def on_message(self, ctx: MessageContext) -> list[SendAction] | None:
        text = ctx.text

        # 0) 奇遇：闭关冷却被重置 -> 立即再次闭关
        if (
            "冷却时间" in text
            and "重置" in text
            and "闭关" in text
            and (self._config.my_name in text or ctx.is_effective_reply)
        ):
            delay_seconds = random.randint(
                self._config.biguan_retry_jitter_min_seconds,
                self._config.biguan_retry_jitter_max_seconds,
            )
            self._logger.debug(
                "biguan_reset_cooldown delay_seconds=%s reply_to_me=%s",
                delay_seconds,
                ctx.is_reply_to_me,
            )
            return await self._arm_next(float(delay_seconds))

        # 1) 正常闭关冷却：打坐调息 N 分钟
        if "打坐调息" in text and (self._config.my_name in text or ctx.is_effective_reply):
            minutes = parse_biguan_cooldown_minutes(text)
            if minutes is None:
                return None

            delay_seconds = (
                minutes * 60
                + self._config.biguan_extra_buffer_seconds
                + random.randint(
                    self._config.biguan_cooldown_jitter_min_seconds,
                    self._config.biguan_cooldown_jitter_max_seconds,
                )
            )

            self._logger.debug(
                "biguan_cooldown minutes=%s delay_seconds=%s reply_to_me=%s",
                minutes,
                delay_seconds,
                ctx.is_reply_to_me,
            )
            return await self._arm_next(float(delay_seconds))

        # 2) 操作太频繁：灵气尚未平复 N分M秒
        if "灵气尚未平复" in text and (self._config.my_name in text or ctx.is_effective_reply):
            total_seconds = parse_lingqi_cooldown_seconds(text)
            if total_seconds is None:
                return None

            delay_seconds = total_seconds + random.randint(
                self._config.biguan_retry_jitter_min_seconds,
                self._config.biguan_retry_jitter_max_seconds,
            )

            self._logger.debug(
                "biguan_retry total_seconds=%s delay_seconds=%s reply_to_me=%s",
                total_seconds,
                delay_seconds,
                ctx.is_reply_to_me,
            )
            return await self._arm_next(float(delay_seconds))

        return None
