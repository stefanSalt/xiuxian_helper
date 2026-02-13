from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable

from ..config import Config
from ..core.contracts import MessageContext, SendAction
from ..core.scheduler import Scheduler


SendFn = Callable[[str, str, bool], Awaitable[int | None]]


class AutoZongmenPlugin:
    """宗门日常：点卯 + 传功（定时）。"""

    name = "zongmen"
    priority = 20

    _HHMM_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")
    _CG_COUNT_RE = re.compile(r"今日已传功\s*(\d+)\s*/\s*(\d+)\s*次")

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self._config = config
        self._logger = logger
        self.enabled = config.enable_zongmen

        self._cmd_dianmao = config.zongmen_cmd_dianmao.strip()
        self._cmd_chuangong = config.zongmen_cmd_chuangong.strip()
        self._xinde_text = config.zongmen_chuangong_xinde_text.strip()
        self._catch_up = bool(config.zongmen_catch_up)
        self._spacing = max(0, int(config.zongmen_action_spacing_seconds))

        self._dianmao_hm = self._parse_hhmm(config.zongmen_dianmao_time) if self.enabled else (0, 0)
        self._chuangong_hms = (
            self._parse_hhmm_list(config.zongmen_chuangong_times) if self.enabled else [(0, 0), (0, 0), (0, 0)]
        )

        self._state_date: date | None = None
        self._dianmao_done = False
        self._chuangong_count = 0
        self._chuangong_disabled = False

        if self.enabled:
            self._logger.info(
                "zongmen_plugin_enabled dianmao_time=%s chuangong_times=%s catch_up=%s",
                config.zongmen_dianmao_time,
                config.zongmen_chuangong_times,
                self._catch_up,
            )

    def _reset_if_new_day(self, now: datetime) -> None:
        today = now.date()
        if self._state_date == today:
            return
        self._state_date = today
        self._dianmao_done = False
        self._chuangong_count = 0
        self._chuangong_disabled = False

    def _parse_hhmm(self, raw: str | None) -> tuple[int, int]:
        if raw is None:
            raise ValueError("missing HH:MM")
        match = self._HHMM_RE.match(raw)
        if not match:
            raise ValueError(f"invalid HH:MM: {raw!r}")
        hour = int(match.group(1))
        minute = int(match.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"invalid HH:MM: {raw!r}")
        return hour, minute

    def _parse_hhmm_list(self, raw: str | None) -> list[tuple[int, int]]:
        if raw is None:
            raise ValueError("missing HH:MM list")
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if len(parts) != 3:
            raise ValueError(f"ZONGMEN_CHUANGONG_TIMES must have 3 times, got: {raw!r}")
        return [self._parse_hhmm(p) for p in parts]

    def _normalize_cmd(self, text: str) -> str:
        return text.strip().lstrip(".").strip()

    def _xinde_for_send(self) -> str:
        text = self._xinde_text or "今日修行心得：稳中求进。"
        # Defensive: avoid sending the command itself as the "valuable message".
        if self._normalize_cmd(text) == self._normalize_cmd(self._cmd_chuangong):
            return f"心得：{text}"
        return text

    def _seconds_until(self, now: datetime, hour: int, minute: int) -> tuple[float, date]:
        """Return (delay_seconds, occurrence_date) for the next scheduled run.

        If catch_up is enabled and today's time has passed, schedule a catch-up run "now".
        """

        today_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now <= today_dt:
            return (today_dt - now).total_seconds(), today_dt.date()
        if self._catch_up:
            return 0.0, today_dt.date()
        next_dt = today_dt + timedelta(days=1)
        return (next_dt - now).total_seconds(), next_dt.date()

    def _next_occurrence(self, now: datetime, hour: int, minute: int) -> datetime:
        dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if dt > now:
            return dt
        return dt + timedelta(days=1)

    async def on_message(self, ctx: MessageContext) -> list[SendAction] | None:
        # Only observe replies to update local state; no direct actions here (scheduled elsewhere).
        text = (ctx.text or "").strip()
        if not text:
            return None

        now = datetime.now()
        self._reset_if_new_day(now)

        if "点卯成功" in text or "今日已点卯" in text:
            self._dianmao_done = True
            return None

        if "此神通需回复你的一条有价值的发言" in text:
            # Avoid spamming if our reply strategy doesn't work in this group.
            self._chuangong_disabled = True
            self._logger.warning("zongmen_chuangong_disabled reason=need_reply_hint text=%r", text)
            return None

        if "每日最多传功" in text or "你今日传功过于频繁" in text:
            self._chuangong_count = 3
            return None

        match = self._CG_COUNT_RE.search(text)
        if match:
            try:
                count = int(match.group(1))
                total = int(match.group(2))
            except ValueError:
                return None
            if total == 3 and 0 <= count <= 3:
                self._chuangong_count = count
        return None

    async def bootstrap(self, scheduler: Scheduler, send) -> None:
        """Register daily schedules.

        `send` is expected to be app-level _send(plugin, text, reply_to_topic, reply_to_msg_id=...).
        """

        if not self.enabled:
            return

        now = datetime.now()
        self._reset_if_new_day(now)

        # Catch-up offsets to avoid hitting rate limits if we start after all times.
        offset = 0.0

        dianmao_delay, dianmao_date = self._seconds_until(now, *self._dianmao_hm)
        if dianmao_delay == 0.0 and self._catch_up:
            dianmao_delay = offset
            offset += float(self._spacing)
        await self._schedule_dianmao(scheduler, send, dianmao_date, dianmao_delay)

        for idx, (hh, mm) in enumerate(self._chuangong_hms, start=1):
            delay, occ_date = self._seconds_until(now, hh, mm)
            if delay == 0.0 and self._catch_up:
                delay = offset
                offset += float(self._spacing)
            await self._schedule_chuangong(scheduler, send, idx, occ_date, delay)

    async def _schedule_dianmao(
        self,
        scheduler: Scheduler,
        send,
        occ_date: date,
        delay_seconds: float,
    ) -> None:
        key = f"zongmen.dianmao.{occ_date.strftime('%Y%m%d')}"

        async def _runner() -> None:
            await self._maybe_send_dianmao(send)
            next_dt = self._next_occurrence(datetime.now(), *self._dianmao_hm)
            await self._schedule_dianmao(
                scheduler,
                send,
                next_dt.date(),
                max(0.0, (next_dt - datetime.now()).total_seconds()),
            )

        self._logger.info("zongmen_scheduled key=%s delay_seconds=%s", key, delay_seconds)
        await scheduler.schedule(key=key, delay_seconds=delay_seconds, action=_runner)

    async def _schedule_chuangong(
        self,
        scheduler: Scheduler,
        send,
        slot: int,
        occ_date: date,
        delay_seconds: float,
    ) -> None:
        hh, mm = self._chuangong_hms[slot - 1]
        key = f"zongmen.chuangong.{slot}.{occ_date.strftime('%Y%m%d')}"

        async def _runner() -> None:
            await self._maybe_send_chuangong(send)
            next_dt = self._next_occurrence(datetime.now(), hh, mm)
            await self._schedule_chuangong(
                scheduler,
                send,
                slot,
                next_dt.date(),
                max(0.0, (next_dt - datetime.now()).total_seconds()),
            )

        self._logger.info("zongmen_scheduled key=%s delay_seconds=%s", key, delay_seconds)
        await scheduler.schedule(key=key, delay_seconds=delay_seconds, action=_runner)

    async def _maybe_send_dianmao(self, send) -> None:
        now = datetime.now()
        self._reset_if_new_day(now)
        if self._dianmao_done:
            self._logger.info("zongmen_skip action=dianmao reason=already_done")
            return
        await send(self.name, self._cmd_dianmao, True)

    async def _maybe_send_chuangong(self, send) -> None:
        now = datetime.now()
        self._reset_if_new_day(now)
        if self._chuangong_disabled:
            self._logger.warning("zongmen_skip action=chuangong reason=disabled")
            return
        if self._chuangong_count >= 3:
            self._logger.info("zongmen_skip action=chuangong reason=limit_reached count=%s", self._chuangong_count)
            return

        mid = await send(self.name, self._xinde_for_send(), True)
        if mid is None:
            self._logger.warning("zongmen_chuangong_abort reason=no_mid")
            return

        await send(self.name, self._cmd_chuangong, True, reply_to_msg_id=mid)

