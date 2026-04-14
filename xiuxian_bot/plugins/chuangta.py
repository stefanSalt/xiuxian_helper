from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable

from ..config import Config
from ..core.contracts import MessageContext, SendAction
from ..core.scheduler import Scheduler
from ..core.state_store import (
    SQLiteStateStore,
    deserialize_date,
    deserialize_datetime,
    serialize_date,
    serialize_datetime,
)

SendFn = Callable[[str, str, bool], Awaitable[int | None]]


class AutoChuangtaPlugin:
    """每日闯塔：固定时刻触发，并与元婴出窍状态协同。"""

    name = "chuangta"
    priority = 50

    _CMD_CHUANGTA = ".闯塔"
    _CMD_YUANYING_STATUS = ".元婴状态"
    _STATUS_REPLY_WINDOW_SECONDS = 210
    _TOWER_FEEDBACK_WINDOW_SECONDS = 210
    _HHMM_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self._config = config
        self._logger = logger
        self.enabled = bool(config.enable_chuangta)
        self._yuanying_enabled = bool(config.enable_yuanying)

        self._scheduler: Scheduler | None = None
        self._send: SendFn | None = None
        self._state_store: SQLiteStateStore | None = None

        self._current_day: date | None = None
        self._done_today = False
        self._pending_today = False

        self._status_requested_at: datetime | None = None
        self._status_request_msg_id: int | None = None
        self._yuanying_out_of_body: bool | None = None

        self._tower_sent_at: datetime | None = None
        self._tower_sent_msg_id: int | None = None

        self._tower_hm = self._parse_hhmm(config.chuangta_time) if self.enabled else (0, 0)

    def set_state_store(self, state_store: SQLiteStateStore) -> None:
        self._state_store = state_store

    def restore_state(self) -> None:
        if self._state_store is None:
            return
        state = self._state_store.load_state(self.name)
        self._current_day = deserialize_date(state.get("current_day"))
        self._done_today = bool(state.get("done_today", False))
        self._pending_today = bool(state.get("pending_today", False))
        self._status_requested_at = deserialize_datetime(state.get("status_requested_at"))
        msg_id = state.get("status_request_msg_id")
        self._status_request_msg_id = int(msg_id) if msg_id is not None else None
        self._yuanying_out_of_body = (
            None if state.get("yuanying_out_of_body") is None else bool(state.get("yuanying_out_of_body"))
        )
        self._tower_sent_at = deserialize_datetime(state.get("tower_sent_at"))
        tower_msg_id = state.get("tower_sent_msg_id")
        self._tower_sent_msg_id = int(tower_msg_id) if tower_msg_id is not None else None

    def _save_state(self) -> None:
        if self._state_store is None:
            return
        self._state_store.save_state(
            self.name,
            {
                "current_day": serialize_date(self._current_day),
                "done_today": self._done_today,
                "pending_today": self._pending_today,
                "status_requested_at": serialize_datetime(self._status_requested_at),
                "status_request_msg_id": self._status_request_msg_id,
                "yuanying_out_of_body": self._yuanying_out_of_body,
                "tower_sent_at": serialize_datetime(self._tower_sent_at),
                "tower_sent_msg_id": self._tower_sent_msg_id,
            },
        )

    def _parse_hhmm(self, value: str) -> tuple[int, int]:
        match = self._HHMM_RE.match((value or "").strip())
        if match is None:
            raise ValueError(f"Invalid CHUANGTA_TIME={value!r}; expected HH:MM")
        hour = int(match.group(1))
        minute = int(match.group(2))
        if hour > 23 or minute > 59:
            raise ValueError(f"Invalid CHUANGTA_TIME={value!r}; expected HH:MM")
        return hour, minute

    def _compact_text(self, text: str) -> str:
        return re.sub(r"\s+", "", text or "")

    def _clear_status_request(self) -> None:
        self._status_requested_at = None
        self._status_request_msg_id = None
        self._save_state()

    def _clear_tower_tracking(self) -> None:
        self._tower_sent_at = None
        self._tower_sent_msg_id = None
        self._save_state()

    def _reset_if_new_day(self, now: datetime) -> None:
        if self._current_day == now.date():
            return
        self._current_day = now.date()
        self._done_today = False
        self._pending_today = False
        self._clear_status_request()
        self._clear_tower_tracking()
        self._save_state()

    def _target_at(self, day: date) -> datetime:
        hour, minute = self._tower_hm
        return datetime(day.year, day.month, day.day, hour, minute)

    def _initial_delay_seconds(self, now: datetime) -> float:
        target = self._target_at(now.date())
        if now < target:
            return max(0.0, (target - now).total_seconds())
        return 0.0

    def _next_day_delay_seconds(self, now: datetime) -> float:
        tomorrow = now.date() + timedelta(days=1)
        return max(0.0, (self._target_at(tomorrow) - now).total_seconds())

    def _is_status_feedback(self, ctx: MessageContext, text: str, now: datetime) -> bool:
        requested_at = self._status_requested_at
        if requested_at is None:
            return False
        if (now - requested_at).total_seconds() > self._STATUS_REPLY_WINDOW_SECONDS:
            self._clear_status_request()
            return False
        if self._status_request_msg_id is not None and ctx.reply_to_msg_id == self._status_request_msg_id:
            return True
        compact = self._compact_text(text)
        if not ctx.is_effective_reply:
            return False
        return ("元婴" in compact) or ("元神" in compact) or ("窍中" in compact)

    def _is_chuangta_feedback(self, ctx: MessageContext, text: str, now: datetime) -> bool:
        related = False
        if ctx.is_effective_reply:
            related = True
        elif self._tower_sent_msg_id is not None and ctx.reply_to_msg_id == self._tower_sent_msg_id:
            related = True
        elif self._tower_sent_at is not None:
            related = (now - self._tower_sent_at).total_seconds() <= self._TOWER_FEEDBACK_WINDOW_SECONDS

        if not related:
            return False

        return (
            "你今日已挑战失败" in text
            or "琉璃问心塔" in text
            or "试炼古塔" in text
        )

    def _chuqiao_summary(self, text: str) -> bool:
        return "元神归窍总结" in text

    def _chuqiao_status_out_of_body(self, text: str) -> bool:
        compact = self._compact_text(text)
        return (
            "状态:元神出窍" in compact
            or ("元婴正在执行" in compact and "元神出窍" in compact)
            or "它将在外云游8小时" in compact
            or "下一次发言时若已归来" in compact
        )

    def _chuqiao_status_warming(self, text: str) -> bool:
        compact = self._compact_text(text)
        return "状态:窍中温养" in compact

    def _send_tower_action(self) -> SendAction:
        return SendAction(
            plugin=self.name,
            text=self._CMD_CHUANGTA,
            reply_to_topic=True,
            delay_seconds=0.0,
            key="chuangta.action.run",
        )

    async def bootstrap(self, scheduler: Scheduler, send: SendFn) -> None:
        if not self.enabled:
            return
        self._scheduler = scheduler
        self._send = send
        now = datetime.now()
        self._reset_if_new_day(now)
        await self._schedule_daily_loop(self._initial_delay_seconds(now))

    async def _schedule_daily_loop(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return

        async def _runner() -> None:
            await self._daily_loop()

        await self._scheduler.schedule(
            key="chuangta.daily.loop",
            delay_seconds=delay_seconds,
            action=_runner,
        )

    async def _schedule_status_timeout(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return

        async def _runner() -> None:
            await self._status_timeout_loop()

        await self._scheduler.schedule(
            key="chuangta.status.timeout",
            delay_seconds=delay_seconds,
            action=_runner,
        )

    async def _daily_loop(self) -> None:
        if not self.enabled:
            return
        now = datetime.now()
        self._reset_if_new_day(now)
        if not self._done_today:
            await self._run_today_flow()
        await self._schedule_daily_loop(self._next_day_delay_seconds(now))

    async def _run_today_flow(self) -> None:
        if self._done_today:
            return
        self._pending_today = True
        self._save_state()
        if self._yuanying_enabled:
            await self._request_yuanying_status()
            return
        await self._send_chuangta()

    async def _request_yuanying_status(self) -> None:
        if self._send is None:
            return
        requested_at = datetime.now()
        msg_id = await self._send(self.name, self._CMD_YUANYING_STATUS, True)
        self._status_requested_at = requested_at
        self._status_request_msg_id = msg_id
        self._save_state()
        await self._schedule_status_timeout(float(self._STATUS_REPLY_WINDOW_SECONDS))

    async def _send_chuangta(self) -> None:
        if self._send is None or self._done_today:
            return
        self._pending_today = False
        sent_at = datetime.now()
        self._status_requested_at = None
        self._status_request_msg_id = None
        msg_id = await self._send(self.name, self._CMD_CHUANGTA, True)
        self._tower_sent_at = sent_at
        self._tower_sent_msg_id = msg_id
        self._save_state()

    async def _status_timeout_loop(self) -> None:
        if not self.enabled or self._done_today or not self._pending_today:
            return
        requested_at = self._status_requested_at
        if requested_at is None:
            return
        elapsed = (datetime.now() - requested_at).total_seconds()
        if elapsed < self._STATUS_REPLY_WINDOW_SECONDS:
            return
        if self._yuanying_out_of_body is True:
            self._clear_status_request()
            return
        await self._send_chuangta()

    async def on_message(self, ctx: MessageContext) -> list[SendAction] | None:
        text = (ctx.text or "").strip()
        if not text or text.startswith("."):
            return None

        now = datetime.now()
        self._reset_if_new_day(now)

        if self._is_chuangta_feedback(ctx, text, now):
            self._done_today = True
            self._pending_today = False
            self._clear_status_request()
            self._clear_tower_tracking()
            self._save_state()
            return None

        if self._chuqiao_summary(text):
            self._yuanying_out_of_body = False
            self._save_state()
            if self._pending_today and not self._done_today:
                self._pending_today = False
                self._clear_status_request()
                self._save_state()
                return [self._send_tower_action()]
            return None

        if self._chuqiao_status_out_of_body(text):
            self._yuanying_out_of_body = True
            self._save_state()

        if self._chuqiao_status_warming(text):
            self._yuanying_out_of_body = False
            self._save_state()

        if not self._is_status_feedback(ctx, text, now):
            return None

        self._clear_status_request()

        if not self._pending_today or self._done_today:
            return None

        if self._chuqiao_status_out_of_body(text):
            return None

        if self._chuqiao_status_warming(text):
            self._pending_today = False
            self._save_state()
            return [self._send_tower_action()]

        self._pending_today = False
        self._save_state()
        return [self._send_tower_action()]
