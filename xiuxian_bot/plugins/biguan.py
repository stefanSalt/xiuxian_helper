from __future__ import annotations

import logging
import random

from ..config import Config
from ..core.contracts import MessageContext, SendAction
from ..domain.parsers import parse_biguan_cooldown_minutes, parse_lingqi_cooldown_seconds


class AutoBiguanPlugin:
    name = "biguan"
    priority = 100

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self._config = config
        self._logger = logger
        self.enabled = config.enable_biguan

    async def on_message(self, ctx: MessageContext) -> list[SendAction] | None:
        text = ctx.text

        # 1) 正常闭关冷却：打坐调息 N 分钟
        if "打坐调息" in text and (self._config.my_name in text or ctx.is_reply_to_me):
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

            return [
                SendAction(
                    plugin=self.name,
                    text=self._config.action_cmd_biguan,
                    reply_to_topic=True,
                    delay_seconds=delay_seconds,
                    key="biguan.next",
                )
            ]

        # 2) 操作太频繁：灵气尚未平复 N分M秒
        if "灵气尚未平复" in text and (self._config.my_name in text or ctx.is_reply_to_me):
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

            return [
                SendAction(
                    plugin=self.name,
                    text=self._config.action_cmd_biguan,
                    reply_to_topic=True,
                    delay_seconds=delay_seconds,
                    key="biguan.next",
                )
            ]

        return None
