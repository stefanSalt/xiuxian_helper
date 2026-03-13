from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

from ..config import Config
from ..core.contracts import MessageContext, SendAction
from ..core.scheduler import Scheduler
from ..domain.xinggong import parse_xinggong_observatory


class AutoXinggongPlugin:
    """星宫自动化：观星台 + 周天星斗大阵。"""

    name = "xinggong"
    priority = 40

    _CMD_OBSERVATORY = ".观星台"
    _CMD_SOOTHE = ".安抚星辰"
    _CMD_COLLECT = ".收集精华"
    _CMD_QIZHEN = ".启阵"
    _CMD_ZHUZHEN = ".助阵"
    _CMD_WENAN = ".每日问安"
    _CMD_VIEW_BIGUAN = ".查看闭关"
    _CMD_DEEP_BIGUAN = ".深度闭关"
    _CMD_FORCE_EXIT = ".强行出关"
    _MATURE_CHECK_BUFFER_SECONDS = 10
    _QIZHEN_COOLDOWN_BUFFER_SECONDS = 5
    _QIZHEN_COOLDOWN_SECONDS = 12 * 3600
    _DEEP_BIGUAN_REFRESH_DELAY_SECONDS = 5 * 3600
    _STATUS_REPLY_WINDOW_SECONDS = 120

    _HHMM_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self._config = config
        self._logger = logger
        self.enabled = bool(config.enable_xinggong)

        self._star_name = config.xinggong_star_name.strip() or "庚金星"
        self._poll_interval_seconds = max(60, int(config.xinggong_poll_interval_seconds))
        self._spacing_seconds = max(0, int(config.xinggong_action_spacing_seconds))
        self._wenan_enabled = bool(config.enable_xinggong_wenan)
        self._deep_biguan_enabled = bool(config.enable_xinggong_deep_biguan)
        self._wenan_interval_seconds = max(60, int(config.xinggong_wenan_interval_seconds))

        self._qizhen_hm = self._parse_hhmm(config.xinggong_qizhen_start_time)
        self._qizhen_retry_seconds = max(30, int(config.xinggong_qizhen_retry_interval_seconds))
        self._qizhen_second_offset_seconds = max(0, int(config.xinggong_qizhen_second_offset_seconds))

        self._scheduler: Scheduler | None = None
        self._send = None

        # Cycle state (a "day" starts at qizhen start time, not at midnight).
        self._cycle_date: date | None = None
        self._qizhen_first_success_at: datetime | None = None
        self._qizhen_second_success_at: datetime | None = None
        self._qizhen_pending_slot: int | None = None
        self._qizhen_last_invite_msg_id: int | None = None
        self._qizhen_last_invite_slot: int | None = None
        # Cooldown observed from bot replies (may span across cycles).
        self._qizhen_blocked_until: datetime | None = None
        self._qizhen_last_sent_at: datetime | None = None

        self._assist_blocked_until: datetime | None = None
        self._deep_biguan_status_msg_id: int | None = None
        self._deep_biguan_status_requested_at: datetime | None = None
        self._deep_biguan_status_reason: str | None = None

        if self.enabled:
            self._logger.info(
                "xinggong_plugin_enabled star=%s poll_interval_seconds=%s qizhen_start=%s retry_seconds=%s second_offset_seconds=%s wenan_enabled=%s deep_biguan_enabled=%s wenan_interval_seconds=%s",
                self._star_name,
                self._poll_interval_seconds,
                config.xinggong_qizhen_start_time,
                self._qizhen_retry_seconds,
                self._qizhen_second_offset_seconds,
                self._wenan_enabled,
                self._deep_biguan_enabled,
                self._wenan_interval_seconds,
            )

    def _my_tag(self) -> str:
        name = self._config.my_name.strip()
        if not name:
            return ""
        return name if name.startswith("@") else f"@{name}"

    def _parse_hhmm(self, raw: str) -> tuple[int, int]:
        match = self._HHMM_RE.match(raw or "")
        if not match:
            raise ValueError(f"invalid HH:MM: {raw!r}")
        hour = int(match.group(1))
        minute = int(match.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"invalid HH:MM: {raw!r}")
        return hour, minute

    def _cycle_date_for(self, now: datetime) -> date:
        hh, mm = self._qizhen_hm
        start_today = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now < start_today:
            return (start_today - timedelta(days=1)).date()
        return start_today.date()

    def _cycle_start_dt(self, now: datetime) -> datetime:
        hh, mm = self._qizhen_hm
        cycle_date = self._cycle_date_for(now)
        return now.replace(year=cycle_date.year, month=cycle_date.month, day=cycle_date.day, hour=hh, minute=mm, second=0, microsecond=0)

    def _reset_if_new_cycle(self, now: datetime) -> None:
        cycle = self._cycle_date_for(now)
        if self._cycle_date == cycle:
            return
        self._cycle_date = cycle
        self._qizhen_first_success_at = None
        self._qizhen_second_success_at = None
        self._qizhen_pending_slot = None
        self._qizhen_last_invite_msg_id = None
        self._qizhen_last_invite_slot = None
        self._assist_blocked_until = None
        self._clear_pending_biguan_status()

    def _next_poll_delay_seconds(self, status) -> float:
        base = float(self._poll_interval_seconds)
        if status.min_remaining_seconds is None:
            return base
        delay = float(status.min_remaining_seconds + self._MATURE_CHECK_BUFFER_SECONDS)
        delay = max(1.0, min(base, delay))
        return delay

    def _sow_cmd(self) -> str:
        # In this group, the command auto-fills all empty disks; no disk index needed.
        return f".牵引星辰 {self._star_name}"

    def _parse_duration_seconds(self, text: str) -> int | None:
        # Parse "2小时16分钟27秒" into seconds.
        text = (text or "").strip()
        if not text:
            return None

        def _pick(unit: str) -> int:
            match = re.search(rf"(\d+)\s*{unit}", text)
            return int(match.group(1)) if match else 0

        days = _pick("天")
        hours = _pick("小时")
        minutes = _pick("分钟")
        seconds = _pick("秒")
        total = days * 86400 + hours * 3600 + minutes * 60 + seconds
        return total if total > 0 else None

    def _infer_qizhen_success_at(self, now: datetime, remaining_cooldown_seconds: int) -> datetime:
        elapsed_seconds = max(0, self._QIZHEN_COOLDOWN_SECONDS - remaining_cooldown_seconds)
        return now - timedelta(seconds=elapsed_seconds)

    def _recover_qizhen_success_from_cooldown(
        self,
        now: datetime,
        remaining_cooldown_seconds: int,
    ) -> datetime | None:
        success_at = self._infer_qizhen_success_at(now, remaining_cooldown_seconds)
        cycle_start = self._cycle_start_dt(now)
        earliest_second_success_at = cycle_start + timedelta(seconds=self._qizhen_second_offset_seconds)

        if self._qizhen_first_success_at is None and self._qizhen_second_success_at is None:
            if success_at >= earliest_second_success_at:
                self._qizhen_first_success_at = cycle_start
                self._qizhen_second_success_at = success_at
            else:
                self._qizhen_first_success_at = success_at
            return success_at

        if self._qizhen_first_success_at is not None and self._qizhen_second_success_at is None:
            second_start = self._qizhen_first_success_at + timedelta(seconds=self._qizhen_second_offset_seconds)
            if success_at >= second_start or success_at >= earliest_second_success_at:
                self._qizhen_second_success_at = success_at
                return success_at

        return None

    def _clear_pending_biguan_status(self) -> None:
        self._deep_biguan_status_msg_id = None
        self._deep_biguan_status_requested_at = None
        self._deep_biguan_status_reason = None

    def _parse_deep_biguan_status(self, text: str) -> str | None:
        if "你并未处于深度闭关之中" in text:
            return "inactive"
        if "你正在深度闭关" in text:
            return "active"
        return None

    def _is_deep_biguan_status_reply(self, ctx: MessageContext, text: str, now: datetime) -> bool:
        requested_at = self._deep_biguan_status_requested_at
        if self._deep_biguan_status_reason is None or requested_at is None:
            return False
        if (now - requested_at).total_seconds() > self._STATUS_REPLY_WINDOW_SECONDS:
            self._clear_pending_biguan_status()
            return False
        if self._deep_biguan_status_msg_id is not None and ctx.reply_to_msg_id == self._deep_biguan_status_msg_id:
            return True
        return bool(ctx.is_reply_to_me and self._parse_deep_biguan_status(text) is not None)

    async def _schedule_deep_biguan_status_check(
        self,
        delay_seconds: float,
        *,
        key: str,
        reason: str,
    ) -> None:
        if self._scheduler is None:
            return

        async def _runner() -> None:
            await self._deep_biguan_status_loop(reason)

        await self._scheduler.schedule(key=key, delay_seconds=delay_seconds, action=_runner)

    async def _schedule_deep_biguan_after_qizhen_success(
        self,
        success_at: datetime,
        now: datetime,
        *,
        immediate_reason: str,
    ) -> None:
        if self._scheduler is None or not self._deep_biguan_enabled:
            return

        midpoint_delay_seconds = (
            success_at + timedelta(seconds=self._DEEP_BIGUAN_REFRESH_DELAY_SECONDS) - now
        ).total_seconds()
        if midpoint_delay_seconds <= 0:
            await self._schedule_deep_biguan_status_check(
                0.0,
                key="xinggong.deep_biguan.status.midpoint",
                reason="midpoint",
            )
            return

        await self._schedule_deep_biguan_status_check(
            0.0,
            key="xinggong.deep_biguan.status.now",
            reason=immediate_reason,
        )
        await self._schedule_deep_biguan_status_check(
            float(midpoint_delay_seconds),
            key="xinggong.deep_biguan.status.midpoint",
            reason="midpoint",
        )

    async def _deep_biguan_status_loop(self, reason: str) -> None:
        if not self.enabled or not self._deep_biguan_enabled or self._send is None:
            return
        requested_at = datetime.now()
        msg_id = await self._send(self.name, self._CMD_VIEW_BIGUAN, True)
        if msg_id is None:
            self._clear_pending_biguan_status()
            return
        self._deep_biguan_status_requested_at = requested_at
        self._deep_biguan_status_msg_id = msg_id
        self._deep_biguan_status_reason = reason

    async def bootstrap(self, scheduler: Scheduler, send) -> None:
        if not self.enabled:
            return
        self._scheduler = scheduler
        self._send = send
        await self._schedule_qizhen_loop(0.0)
        if self._wenan_enabled:
            await self._schedule_wenan_loop(0.0)

    async def _schedule_qizhen_loop(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return
        key = "xinggong.qizhen.loop"

        async def _runner() -> None:
            await self._qizhen_loop()

        await self._scheduler.schedule(key=key, delay_seconds=delay_seconds, action=_runner)

    async def _schedule_wenan_loop(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return
        key = "xinggong.wenan.loop"

        async def _runner() -> None:
            await self._wenan_loop()

        await self._scheduler.schedule(key=key, delay_seconds=delay_seconds, action=_runner)

    async def _wenan_loop(self) -> None:
        if not self.enabled or not self._wenan_enabled or self._send is None:
            return
        await self._send(self.name, self._CMD_WENAN, True)
        await self._schedule_wenan_loop(float(self._wenan_interval_seconds))

    async def _qizhen_loop(self) -> None:
        if not self.enabled or self._send is None:
            return

        now = datetime.now()
        self._reset_if_new_cycle(now)
        cycle_start = self._cycle_start_dt(now)

        if self._qizhen_first_success_at is None:
            desired_start = cycle_start
            if self._qizhen_blocked_until is not None and self._qizhen_blocked_until > desired_start:
                desired_start = self._qizhen_blocked_until
            if now < desired_start:
                await self._schedule_qizhen_loop((desired_start - now).total_seconds())
                return
            self._qizhen_pending_slot = 1
            self._qizhen_last_sent_at = now
            await self._send(self.name, self._CMD_QIZHEN, True)
            await self._schedule_qizhen_loop(float(self._qizhen_retry_seconds))
            return

        if self._qizhen_second_success_at is None:
            second_start = self._qizhen_first_success_at + timedelta(seconds=self._qizhen_second_offset_seconds)
            desired_start = second_start
            if self._qizhen_blocked_until is not None and self._qizhen_blocked_until > desired_start:
                desired_start = self._qizhen_blocked_until
            if now < desired_start:
                await self._schedule_qizhen_loop((desired_start - now).total_seconds())
                return
            self._qizhen_pending_slot = 2
            self._qizhen_last_sent_at = now
            await self._send(self.name, self._CMD_QIZHEN, True)
            await self._schedule_qizhen_loop(float(self._qizhen_retry_seconds))
            return

        # Both runs done for this cycle; schedule next cycle start.
        next_cycle_start = cycle_start + timedelta(days=1)
        await self._schedule_qizhen_loop(max(0.0, (next_cycle_start - now).total_seconds()))

    async def on_message(self, ctx: MessageContext) -> list[SendAction] | None:
        text = (ctx.text or "").strip()
        if not text:
            return None

        # Ignore command lines (including our own).
        if text.startswith("."):
            return None

        now = datetime.now()
        self._reset_if_new_cycle(now)

        # ---- 周天星斗大阵：成功/邀请/助阵冷却 ----
        my_tag = self._my_tag()
        if "再次启阵" in text and "请在" in text:
            # e.g. 你刚刚参与过布阵... 请在 11小时7分钟39秒 后再次启阵。
            is_related = bool(ctx.is_reply_to_me)
            if not is_related and self._qizhen_last_sent_at is not None:
                is_related = (now - self._qizhen_last_sent_at) <= timedelta(seconds=90)
            if not is_related:
                return None
            rem = self._parse_duration_seconds(text)
            if rem is None:
                return None
            blocked_until = now + timedelta(seconds=rem + self._QIZHEN_COOLDOWN_BUFFER_SECONDS)
            if self._qizhen_blocked_until is None or blocked_until > self._qizhen_blocked_until:
                self._qizhen_blocked_until = blocked_until
            recovered_success_at = self._recover_qizhen_success_from_cooldown(now, rem)
            # Stop retries; schedule the next loop at cooldown end.
            self._qizhen_pending_slot = None
            if recovered_success_at is not None:
                await self._schedule_deep_biguan_after_qizhen_success(
                    recovered_success_at,
                    now,
                    immediate_reason="qizhen_recovered",
                )
            if self._scheduler is not None:
                await self._schedule_qizhen_loop(max(0.0, (self._qizhen_blocked_until - now).total_seconds()))
            return None

        if "周天星斗大阵-启" in text:
            if my_tag and my_tag in text:
                # This is the bot's invite message for our own ".启阵".
                self._qizhen_last_invite_msg_id = ctx.message_id
                self._qizhen_last_invite_slot = self._qizhen_pending_slot
            else:
                # Others' invite -> try assist (no reply needed per your group rules).
                if self._assist_blocked_until is not None and now < self._assist_blocked_until:
                    return None
                return [
                    SendAction(
                        plugin=self.name,
                        text=self._CMD_ZHUZHEN,
                        reply_to_topic=True,
                        delay_seconds=0.0,
                        key="xinggong.action.zhuzhen",
                    )
                ]

        if "再次助阵" in text and "请在" in text:
            # e.g. 你刚刚参与过布阵... 请在 2小时16分钟27秒 后再次助阵。
            rem = self._parse_duration_seconds(text)
            if rem is not None:
                self._assist_blocked_until = now + timedelta(seconds=rem + 5)
            return None

        if "周天星斗大阵-成" in text or ("大阵已成" in text and "周天星斗大阵" in text):
            # Treat as success only if it matches our own invite edit, or explicitly mentions us.
            is_mine = (self._qizhen_last_invite_msg_id == ctx.message_id) or (my_tag and my_tag in text)
            if is_mine and self._qizhen_pending_slot in (1, 2):
                if self._qizhen_pending_slot == 1 and self._qizhen_first_success_at is None:
                    self._qizhen_first_success_at = now
                elif self._qizhen_pending_slot == 2 and self._qizhen_second_success_at is None:
                    self._qizhen_second_success_at = now
                self._qizhen_pending_slot = None
                # Recompute the schedule immediately (cancels pending retries via key override).
                if self._scheduler is not None:
                    await self._schedule_qizhen_loop(0.0)
                    await self._schedule_deep_biguan_after_qizhen_success(
                        now,
                        now,
                        immediate_reason="qizhen_success",
                    )
            return None

        if self._deep_biguan_enabled and self._is_deep_biguan_status_reply(ctx, text, now):
            status = self._parse_deep_biguan_status(text)
            self._clear_pending_biguan_status()
            if status == "inactive":
                return [
                    SendAction(
                        plugin=self.name,
                        text=self._CMD_DEEP_BIGUAN,
                        reply_to_topic=True,
                        delay_seconds=0.0,
                        key="xinggong.deep_biguan.enter",
                    )
                ]
            if status == "active":
                return [
                    SendAction(
                        plugin=self.name,
                        text=self._CMD_FORCE_EXIT,
                        reply_to_topic=True,
                        delay_seconds=0.0,
                        key="xinggong.deep_biguan.exit",
                    ),
                    SendAction(
                        plugin=self.name,
                        text=self._CMD_DEEP_BIGUAN,
                        reply_to_topic=True,
                        delay_seconds=float(self._spacing_seconds),
                        key="xinggong.deep_biguan.enter",
                    ),
                ]
            return None

        # ---- 观星台：动作回包（安抚/收集） -> 立即复查状态 ----
        if "成功安抚了" in text and "引星盘" in text:
            return [
                SendAction(
                    plugin=self.name,
                    text=self._CMD_OBSERVATORY,
                    reply_to_topic=True,
                    delay_seconds=float(self._spacing_seconds),
                    key="xinggong.poll",
                )
            ]

        if "成功从" in text and "收集" in text and "星辰精华" in text:
            return [
                SendAction(
                    plugin=self.name,
                    text=self._CMD_OBSERVATORY,
                    reply_to_topic=True,
                    delay_seconds=float(self._spacing_seconds),
                    key="xinggong.poll",
                )
            ]

        # ---- 观星台状态回包 ----
        status = parse_xinggong_observatory(text)
        if status is None:
            return None

        poll_delay_seconds = self._next_poll_delay_seconds(status)
        actions: list[SendAction] = [
            SendAction(
                plugin=self.name,
                text=self._CMD_OBSERVATORY,
                reply_to_topic=True,
                delay_seconds=poll_delay_seconds,
                key="xinggong.poll",
            )
        ]

        delay = 0.0

        if status.abnormal_disks:
            actions.append(
                SendAction(
                    plugin=self.name,
                    text=self._CMD_SOOTHE,
                    reply_to_topic=True,
                    delay_seconds=delay,
                    key="xinggong.action.soothe",
                )
            )
            return actions

        if status.collectable_disks:
            actions.append(
                SendAction(
                    plugin=self.name,
                    text=self._CMD_COLLECT,
                    reply_to_topic=True,
                    delay_seconds=delay,
                    key="xinggong.action.collect",
                )
            )
            return actions

        if status.idle_disks:
            actions.append(
                SendAction(
                    plugin=self.name,
                    text=self._sow_cmd(),
                    reply_to_topic=True,
                    delay_seconds=delay,
                    key="xinggong.action.sow",
                )
            )

        return actions
