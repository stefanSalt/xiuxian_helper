from __future__ import annotations

import logging

from ..config import Config
from ..core.contracts import MessageContext, SendAction


class DailyPlugin:
    """每日类自动化（低风险默认关闭）。

    先保留扩展位：未来可以在 app 启动时注册定时任务（例如每天固定时间发送指令），
    或根据机器人提示消息触发。
    """

    name = "daily"
    priority = 10

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self._config = config
        self._logger = logger
        self.enabled = config.enable_daily

        if self.enabled:
            self._logger.warning("daily_plugin_enabled risk_policy=low default_actions=none")

    async def on_message(self, ctx: MessageContext) -> list[SendAction] | None:
        _ = ctx
        return None

