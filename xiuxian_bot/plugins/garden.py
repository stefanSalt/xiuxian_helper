from __future__ import annotations

import logging
from datetime import datetime, timedelta

from ..config import Config
from ..core.contracts import MessageContext, SendAction
from ..domain.garden import parse_garden_status


class AutoGardenPlugin:
    """自动种植（小药园）。

    目前按你确认的“一键操作”实现：
    - `.小药园` 拉取状态
    - `.浇水/.除虫/.除草/.采药` 一键处理
    - `.播种 <种子名>` 一键补满空闲灵田
    """

    name = "garden"
    priority = 50

    _CMD_STATUS = ".小药园"
    _CMD_WATER = ".浇水"
    _CMD_INSECT = ".除虫"
    _CMD_WEED = ".除草"
    _CMD_HARVEST = ".采药"
    _MATURE_CHECK_BUFFER_SECONDS = 10

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self._config = config
        self._logger = logger
        self.enabled = config.enable_garden

        self._seed_insufficient = False
        self._seed_insufficient_warned = False
        self._sow_blocked_no_idle = False

        if self.enabled:
            self._logger.info(
                "garden_plugin_enabled poll_interval_seconds=%s seed_name=%s",
                self._config.garden_poll_interval_seconds,
                self._config.garden_seed_name,
            )

    def _sow_cmd(self) -> str:
        return f".播种 {self._config.garden_seed_name}"

    def _next_poll_delay_seconds(self, status) -> float:
        base = float(self._config.garden_poll_interval_seconds)
        if status.min_remaining_seconds is None:
            return base

        # Check shortly after the earliest "剩余" should hit 0, but don't exceed the normal poll interval.
        delay = float(status.min_remaining_seconds + self._MATURE_CHECK_BUFFER_SECONDS)
        delay = max(1.0, min(base, delay))
        return delay

    async def on_message(self, ctx: MessageContext) -> list[SendAction] | None:
        text = (ctx.text or "").strip()
        if not text:
            return None

        # Ignore command lines (including our own).
        if text.startswith("."):
            return None

        # ---- Command replies (heuristic, keyword-based) ----
        if "你的药园中已无空闲的灵田" in text:
            self._sow_blocked_no_idle = True
            return None

        if "数量不足" in text and "种子" in text:
            self._seed_insufficient = True
            if not self._seed_insufficient_warned:
                self._seed_insufficient_warned = True
                self._logger.warning(
                    "garden_seed_insufficient seed_name=%s text=%r",
                    self._config.garden_seed_name,
                    text,
                )
            return None

        if "播种成功" in text:
            self._sow_blocked_no_idle = False
            return None

        if "一键采药完成" in text:
            # Harvesting usually creates idle plots right away, so try sowing once.
            self._sow_blocked_no_idle = False
            if self._seed_insufficient:
                return None
            return [
                SendAction(
                    plugin=self.name,
                    text=self._sow_cmd(),
                    reply_to_topic=True,
                    delay_seconds=float(self._config.garden_action_spacing_seconds),
                    key="garden.action.sow",
                )
            ]

        # ---- Status reply (.小药园) ----
        status = parse_garden_status(text)
        if status is None:
            return None

        poll_delay_seconds = self._next_poll_delay_seconds(status)
        if status.min_remaining_seconds is not None and poll_delay_seconds < float(self._config.garden_poll_interval_seconds):
            now = datetime.now()
            eta = now + timedelta(seconds=int(status.min_remaining_seconds))
            check_at = now + timedelta(seconds=int(poll_delay_seconds))
            self._logger.info(
                "garden_eta min_remaining_seconds=%s eta=%s check_at=%s",
                status.min_remaining_seconds,
                eta.isoformat(timespec="seconds"),
                check_at.isoformat(timespec="seconds"),
            )

        actions: list[SendAction] = [
            # Keep a single poll scheduled; the scheduler key will override older ones.
            SendAction(
                plugin=self.name,
                text=self._CMD_STATUS,
                reply_to_topic=True,
                delay_seconds=poll_delay_seconds,
                key="garden.poll",
            )
        ]

        delay = 0.0
        spacing = float(self._config.garden_action_spacing_seconds)

        # Priority: fix bad states -> harvest -> (later) sow.
        if status.has_insect:
            actions.append(
                SendAction(
                    plugin=self.name,
                    text=self._CMD_INSECT,
                    reply_to_topic=True,
                    delay_seconds=delay,
                    key="garden.action.insect",
                )
            )
            delay += spacing

        if status.has_weed:
            actions.append(
                SendAction(
                    plugin=self.name,
                    text=self._CMD_WEED,
                    reply_to_topic=True,
                    delay_seconds=delay,
                    key="garden.action.weed",
                )
            )
            delay += spacing

        if status.has_drought:
            actions.append(
                SendAction(
                    plugin=self.name,
                    text=self._CMD_WATER,
                    reply_to_topic=True,
                    delay_seconds=delay,
                    key="garden.action.water",
                )
            )
            delay += spacing

        if status.has_mature:
            actions.append(
                SendAction(
                    plugin=self.name,
                    text=self._CMD_HARVEST,
                    reply_to_topic=True,
                    delay_seconds=delay,
                    key="garden.action.harvest",
                )
            )
            return actions

        # No mature crops: sow only when we can detect idle plots.
        if status.has_idle and not self._seed_insufficient and not self._sow_blocked_no_idle:
            actions.append(
                SendAction(
                    plugin=self.name,
                    text=self._sow_cmd(),
                    reply_to_topic=True,
                    delay_seconds=delay,
                    key="garden.action.sow",
                )
            )

        return actions
