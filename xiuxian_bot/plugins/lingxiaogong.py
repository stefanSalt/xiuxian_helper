from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable

from ..config import Config
from ..core.scheduler import Scheduler
from ..core.state_store import (
    SQLiteStateStore,
    coerce_int,
    deserialize_date,
    deserialize_datetime,
    serialize_date,
    serialize_datetime,
)
from ..core.contracts import MessageContext
from ..domain.text_normalizer import normalize_match_text

SendFn = Callable[[str, str, bool], Awaitable[int | None]]


@dataclass(frozen=True)
class _StatusSnapshot:
    cooldown_seconds: int | None
    wenxin_done: bool | None
    seal_name: str | None
    jiutian_unlocked: bool | None
    jiutian_cooldown_seconds: int | None
    tianmen_unlocked: bool | None


class AutoLingxiaogongPlugin:
    """凌霄宫自动化：天阶状态 / 问心台 / 登天阶 / 引九天罡风。"""

    name = "lingxiaogong"
    priority = 42

    _CMD_STATUS = ".天阶状态"
    _CMD_WENXIN = ".问心台"
    _CMD_CLIMB = ".登天阶"
    _CMD_JIUTIAN = ".引九天罡风"
    _CMD_TIANMEN = ".借天门势"

    _REPLY_WINDOW_SECONDS = 210
    _ACTION_SPACING_SECONDS = 15
    _COOLDOWN_BUFFER_SECONDS = 1
    _JIUTIAN_COOLDOWN_SECONDS = 12 * 60 * 60

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self._config = config
        self._logger = logger
        self.enabled = bool(config.enable_lingxiaogong)
        self._wenxin_enabled = bool(config.enable_lingxiaogong_wenxintai)
        self._jiutian_enabled = bool(config.enable_lingxiaogong_jiutian)
        self._climb_enabled = bool(config.enable_lingxiaogong_dengtianjie)
        self._poll_interval_seconds = max(60, int(config.lingxiaogong_poll_interval_seconds))
        self._wenxin_after_climb_count = max(1, int(config.lingxiaogong_wenxintai_after_climb_count))

        self._scheduler: Scheduler | None = None
        self._send: SendFn | None = None
        self._state_store: SQLiteStateStore | None = None

        self._current_day: date | None = None
        self._today_wenxin_done = False
        self._today_climb_count = 0
        self._seal_name: str | None = None
        self._jiutian_unlocked: bool | None = None
        self._tianmen_unlocked: bool | None = None

        self._next_status_at: datetime | None = None
        self._next_climb_at: datetime | None = None
        self._next_jiutian_at: datetime | None = None
        self._cooldown_until: datetime | None = None
        self._jiutian_cooldown_until: datetime | None = None

        self._status_requested_at: datetime | None = None
        self._status_request_msg_id: int | None = None
        self._wenxin_requested_at: datetime | None = None
        self._wenxin_request_msg_id: int | None = None
        self._jiutian_requested_at: datetime | None = None
        self._jiutian_request_msg_id: int | None = None
        self._climb_requested_at: datetime | None = None
        self._climb_request_msg_id: int | None = None

    def set_state_store(self, state_store: SQLiteStateStore) -> None:
        self._state_store = state_store

    def restore_state(self) -> None:
        if self._state_store is None:
            return
        state = self._state_store.load_state(self.name)
        self._current_day = deserialize_date(state.get("current_day"))
        self._today_wenxin_done = bool(state.get("today_wenxin_done", False))
        self._today_climb_count = max(0, coerce_int(state.get("today_climb_count")) or 0)
        seal_name = str(state.get("seal_name", "") or "").strip()
        self._seal_name = seal_name or None
        self._jiutian_unlocked = (
            None if state.get("jiutian_unlocked") is None else bool(state.get("jiutian_unlocked"))
        )
        self._tianmen_unlocked = (
            None if state.get("tianmen_unlocked") is None else bool(state.get("tianmen_unlocked"))
        )
        self._next_status_at = deserialize_datetime(state.get("next_status_at"))
        self._next_climb_at = deserialize_datetime(state.get("next_climb_at"))
        self._next_jiutian_at = deserialize_datetime(state.get("next_jiutian_at"))
        self._cooldown_until = deserialize_datetime(state.get("cooldown_until"))
        self._jiutian_cooldown_until = deserialize_datetime(state.get("jiutian_cooldown_until"))
        self._status_requested_at = deserialize_datetime(state.get("status_requested_at"))
        self._status_request_msg_id = coerce_int(state.get("status_request_msg_id"))
        self._wenxin_requested_at = deserialize_datetime(state.get("wenxin_requested_at"))
        self._wenxin_request_msg_id = coerce_int(state.get("wenxin_request_msg_id"))
        self._jiutian_requested_at = deserialize_datetime(state.get("jiutian_requested_at"))
        self._jiutian_request_msg_id = coerce_int(state.get("jiutian_request_msg_id"))
        self._climb_requested_at = deserialize_datetime(state.get("climb_requested_at"))
        self._climb_request_msg_id = coerce_int(state.get("climb_request_msg_id"))

    def _save_state(self) -> None:
        if self._state_store is None:
            return
        self._state_store.save_state(
            self.name,
            {
                "current_day": serialize_date(self._current_day),
                "today_wenxin_done": self._today_wenxin_done,
                "today_climb_count": self._today_climb_count,
                "seal_name": self._seal_name,
                "jiutian_unlocked": self._jiutian_unlocked,
                "tianmen_unlocked": self._tianmen_unlocked,
                "next_status_at": serialize_datetime(self._next_status_at),
                "next_climb_at": serialize_datetime(self._next_climb_at),
                "next_jiutian_at": serialize_datetime(self._next_jiutian_at),
                "cooldown_until": serialize_datetime(self._cooldown_until),
                "jiutian_cooldown_until": serialize_datetime(self._jiutian_cooldown_until),
                "status_requested_at": serialize_datetime(self._status_requested_at),
                "status_request_msg_id": self._status_request_msg_id,
                "wenxin_requested_at": serialize_datetime(self._wenxin_requested_at),
                "wenxin_request_msg_id": self._wenxin_request_msg_id,
                "jiutian_requested_at": serialize_datetime(self._jiutian_requested_at),
                "jiutian_request_msg_id": self._jiutian_request_msg_id,
                "climb_requested_at": serialize_datetime(self._climb_requested_at),
                "climb_request_msg_id": self._climb_request_msg_id,
            },
        )

    def _compact_text(self, text: str) -> str:
        return re.sub(r"\s+", "", text or "")

    def _parse_duration_seconds(self, text: str) -> int | None:
        raw = (text or "").strip()
        if not raw:
            return None

        matched = False

        def _pick(pattern: str) -> int:
            nonlocal matched
            match = re.search(pattern, raw)
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

    def _parse_status_snapshot(self, text: str) -> _StatusSnapshot | None:
        normalized = normalize_match_text(text)
        if not any(anchor in normalized for anchor in ("当前云阶进度", "登阶冷却", "问心状态", "罡风淬体")):
            return None

        cooldown_seconds: int | None = None
        cooldown_match = re.search(r"登阶冷却[:：]\s*([^\n\r]+)", text)
        if cooldown_match is not None:
            cooldown_seconds = self._parse_duration_seconds(cooldown_match.group(1))

        wenxin_done: bool | None = None
        seal_name: str | None = None
        seal_match = re.search(r"问心状态[:：]\s*【([^】]+)】", text)
        if seal_match is not None:
            wenxin_done = True
            seal_name = seal_match.group(1).strip()
        elif "今日已问心但道印已在登阶中耗尽" in normalized:
            wenxin_done = True
        elif "今日尚未问心" in normalized or "问心状态尚未问心" in normalized or "问心状态未问心" in normalized:
            wenxin_done = False

        jiutian_unlocked: bool | None = None
        jiutian_cooldown_seconds: int | None = None
        jiutian_match = re.search(r"引九天罡风[:：]\s*([^\n\r]+)", text)
        if jiutian_match is not None:
            jiutian_value = jiutian_match.group(1).strip()
            jiutian_normalized = normalize_match_text(jiutian_value)
            if "未解锁" in jiutian_normalized:
                jiutian_unlocked = False
            elif "可用" in jiutian_normalized:
                jiutian_unlocked = True
                jiutian_cooldown_seconds = 0
            else:
                cooldown = self._parse_duration_seconds(jiutian_value)
                if cooldown is not None:
                    jiutian_unlocked = True
                    jiutian_cooldown_seconds = cooldown
                elif self._CMD_JIUTIAN in text or "引九天罡风" in jiutian_normalized:
                    jiutian_unlocked = True

        tianmen_unlocked: bool | None = None
        tianmen_match = re.search(r"借天门势[:：]\s*([^\n\r]+)", text)
        if tianmen_match is not None and "未解锁" in normalize_match_text(tianmen_match.group(1)):
            tianmen_unlocked = False
        elif self._CMD_TIANMEN in text or "借天门势" in normalized:
            tianmen_unlocked = True

        return _StatusSnapshot(
            cooldown_seconds=cooldown_seconds,
            wenxin_done=wenxin_done,
            seal_name=seal_name,
            jiutian_unlocked=jiutian_unlocked,
            jiutian_cooldown_seconds=jiutian_cooldown_seconds,
            tianmen_unlocked=tianmen_unlocked,
        )

    def _parse_wenxin_feedback(self, text: str) -> tuple[bool, str | None] | None:
        seal_match = re.search(r"凝出一道【([^】]+)】之印", text)
        if seal_match is not None:
            return True, seal_match.group(1).strip()

        existing_match = re.search(r"留下一缕道印[:：]【([^】]+)】", text)
        if existing_match is not None:
            return True, existing_match.group(1).strip()

        if "你今日已在问心台前静坐过一次" in text or "道台不会再响应你" in text:
            return True, None

        normalized = normalize_match_text(text)
        if "问心台" in normalized:
            return True, None
        return None

    def _parse_climb_feedback_kind(self, text: str) -> str | None:
        if "九天罡风尚未再聚" in text and "后再试" in text:
            return "cooldown"
        if "踏上了第" in text or "当前云阶进度" in text or "本次获得" in text:
            return "result"
        return None

    def _parse_jiutian_feedback(self, text: str) -> tuple[str, str | None] | None:
        normalized = normalize_match_text(text)
        if "九天罡风尚未再聚" in normalized and "后再施展此术" in normalized:
            return "cooldown", None
        if "九天罡风" not in normalized:
            return None
        if "贯体" in normalized or "罡风淬体" in normalized:
            seal_match = re.search(r"凝得一道【([^】]+)】之印", text)
            return "success", seal_match.group(1).strip() if seal_match is not None else None
        return None

    def _clear_status_request(self, *, save: bool = True) -> None:
        self._status_requested_at = None
        self._status_request_msg_id = None
        if save:
            self._save_state()

    def _clear_wenxin_request(self, *, save: bool = True) -> None:
        self._wenxin_requested_at = None
        self._wenxin_request_msg_id = None
        if save:
            self._save_state()

    def _clear_jiutian_request(self, *, save: bool = True) -> None:
        self._jiutian_requested_at = None
        self._jiutian_request_msg_id = None
        if save:
            self._save_state()

    def _clear_climb_request(self, *, save: bool = True) -> None:
        self._climb_requested_at = None
        self._climb_request_msg_id = None
        if save:
            self._save_state()

    def _should_request_wenxin(self) -> bool:
        return (
            self._wenxin_enabled
            and not self._today_wenxin_done
            and self._today_climb_count >= self._wenxin_after_climb_count
        )

    def _reset_if_new_day(self, now: datetime) -> None:
        if self._current_day == now.date():
            return
        self._current_day = now.date()
        self._today_wenxin_done = False
        self._today_climb_count = 0
        self._seal_name = None
        self._clear_status_request(save=False)
        self._clear_wenxin_request(save=False)
        self._clear_jiutian_request(save=False)
        self._clear_climb_request(save=False)
        self._save_state()

    async def bootstrap(self, scheduler: Scheduler, send: SendFn) -> None:
        if not self.enabled:
            return
        self._scheduler = scheduler
        self._send = send
        now = datetime.now()
        self._reset_if_new_day(now)
        if self._next_status_at is not None and self._next_status_at > now:
            await self._schedule_status_loop((self._next_status_at - now).total_seconds())
        if self._next_climb_at is not None and self._next_climb_at > now and self._climb_enabled:
            await self._schedule_climb_loop((self._next_climb_at - now).total_seconds())
        if self._next_jiutian_at is not None and self._next_jiutian_at > now and self._jiutian_enabled:
            await self._schedule_jiutian_loop((self._next_jiutian_at - now).total_seconds())
        await self._status_loop()

    async def _schedule_status_loop(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return
        delay_seconds = max(0.0, delay_seconds)
        self._next_status_at = datetime.now() + timedelta(seconds=delay_seconds)
        self._save_state()

        async def _runner() -> None:
            await self._status_loop()

        await self._scheduler.schedule(
            key="lingxiaogong.status.loop",
            delay_seconds=delay_seconds,
            action=_runner,
        )

    async def _schedule_status_timeout(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return

        async def _runner() -> None:
            await self._status_timeout_loop()

        await self._scheduler.schedule(
            key="lingxiaogong.status.timeout",
            delay_seconds=max(0.0, delay_seconds),
            action=_runner,
        )

    async def _schedule_wenxin_timeout(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return

        async def _runner() -> None:
            await self._wenxin_timeout_loop()

        await self._scheduler.schedule(
            key="lingxiaogong.wenxin.timeout",
            delay_seconds=max(0.0, delay_seconds),
            action=_runner,
        )

    async def _schedule_climb_loop(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return
        delay_seconds = max(0.0, delay_seconds)
        self._next_climb_at = datetime.now() + timedelta(seconds=delay_seconds)
        self._save_state()

        async def _runner() -> None:
            await self._climb_loop()

        await self._scheduler.schedule(
            key="lingxiaogong.climb.loop",
            delay_seconds=delay_seconds,
            action=_runner,
        )

    async def _schedule_climb_timeout(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return

        async def _runner() -> None:
            await self._climb_timeout_loop()

        await self._scheduler.schedule(
            key="lingxiaogong.climb.timeout",
            delay_seconds=max(0.0, delay_seconds),
            action=_runner,
        )

    async def _schedule_jiutian_loop(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return
        delay_seconds = max(0.0, delay_seconds)
        self._next_jiutian_at = datetime.now() + timedelta(seconds=delay_seconds)
        self._save_state()

        async def _runner() -> None:
            await self._jiutian_loop()

        await self._scheduler.schedule(
            key="lingxiaogong.jiutian.loop",
            delay_seconds=delay_seconds,
            action=_runner,
        )

    async def _schedule_jiutian_timeout(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return

        async def _runner() -> None:
            await self._jiutian_timeout_loop()

        await self._scheduler.schedule(
            key="lingxiaogong.jiutian.timeout",
            delay_seconds=max(0.0, delay_seconds),
            action=_runner,
        )

    async def _status_loop(self) -> None:
        if not self.enabled or self._send is None:
            return
        self._reset_if_new_day(datetime.now())
        self._next_status_at = None
        self._save_state()
        requested_at = datetime.now()
        msg_id = await self._send(self.name, self._CMD_STATUS, True)
        if msg_id is None:
            await self._schedule_status_loop(float(self._poll_interval_seconds))
            return
        self._status_requested_at = requested_at
        self._status_request_msg_id = msg_id
        self._save_state()
        await self._schedule_status_timeout(float(self._REPLY_WINDOW_SECONDS))

    async def _request_wenxin(self) -> None:
        if (
            not self.enabled
            or not self._wenxin_enabled
            or self._send is None
            or self._today_wenxin_done
            or self._wenxin_requested_at is not None
        ):
            return
        requested_at = datetime.now()
        msg_id = await self._send(self.name, self._CMD_WENXIN, True)
        if msg_id is None:
            await self._schedule_status_loop(float(self._poll_interval_seconds))
            return
        self._wenxin_requested_at = requested_at
        self._wenxin_request_msg_id = msg_id
        self._save_state()
        await self._schedule_wenxin_timeout(float(self._REPLY_WINDOW_SECONDS))

    async def _climb_loop(self) -> None:
        if not self.enabled or not self._climb_enabled or self._send is None:
            return
        self._reset_if_new_day(datetime.now())
        self._next_climb_at = None
        self._save_state()
        now = datetime.now()
        if self._cooldown_until is not None and now < self._cooldown_until:
            await self._schedule_climb_loop((self._cooldown_until - now).total_seconds())
            return
        if (
            self._jiutian_enabled
            and self._jiutian_requested_at is None
            and self._jiutian_unlocked is True
            and (self._jiutian_cooldown_until is None or now >= self._jiutian_cooldown_until)
        ):
            await self._jiutian_loop()
            return
        if self._should_request_wenxin():
            await self._status_loop()
            return
        if self._climb_requested_at is not None:
            return
        requested_at = datetime.now()
        msg_id = await self._send(self.name, self._CMD_CLIMB, True)
        if msg_id is None:
            await self._schedule_status_loop(float(self._poll_interval_seconds))
            return
        self._climb_requested_at = requested_at
        self._climb_request_msg_id = msg_id
        self._save_state()
        await self._schedule_climb_timeout(float(self._REPLY_WINDOW_SECONDS))

    async def _jiutian_loop(self) -> None:
        if not self.enabled or not self._jiutian_enabled or self._send is None:
            return
        self._reset_if_new_day(datetime.now())
        self._next_jiutian_at = None
        self._save_state()
        now = datetime.now()
        if self._jiutian_unlocked is False:
            return
        if self._jiutian_cooldown_until is not None and now < self._jiutian_cooldown_until:
            await self._schedule_jiutian_loop((self._jiutian_cooldown_until - now).total_seconds())
            return
        if self._jiutian_requested_at is not None:
            return
        requested_at = datetime.now()
        msg_id = await self._send(self.name, self._CMD_JIUTIAN, True)
        if msg_id is None:
            await self._schedule_status_loop(float(self._poll_interval_seconds))
            return
        self._jiutian_requested_at = requested_at
        self._jiutian_request_msg_id = msg_id
        self._save_state()
        await self._schedule_jiutian_timeout(float(self._REPLY_WINDOW_SECONDS))

    async def _status_timeout_loop(self) -> None:
        requested_at = self._status_requested_at
        if requested_at is None:
            return
        if (datetime.now() - requested_at).total_seconds() < self._REPLY_WINDOW_SECONDS:
            return
        self._clear_status_request(save=False)
        self._save_state()
        await self._schedule_status_loop(float(self._poll_interval_seconds))

    async def _wenxin_timeout_loop(self) -> None:
        requested_at = self._wenxin_requested_at
        if requested_at is None:
            return
        if (datetime.now() - requested_at).total_seconds() < self._REPLY_WINDOW_SECONDS:
            return
        self._clear_wenxin_request(save=False)
        self._save_state()
        await self._schedule_status_loop(float(self._poll_interval_seconds))

    async def _climb_timeout_loop(self) -> None:
        requested_at = self._climb_requested_at
        if requested_at is None:
            return
        if (datetime.now() - requested_at).total_seconds() < self._REPLY_WINDOW_SECONDS:
            return
        self._clear_climb_request(save=False)
        self._save_state()
        await self._schedule_status_loop(float(self._poll_interval_seconds))

    async def _jiutian_timeout_loop(self) -> None:
        requested_at = self._jiutian_requested_at
        if requested_at is None:
            return
        if (datetime.now() - requested_at).total_seconds() < self._REPLY_WINDOW_SECONDS:
            return
        self._clear_jiutian_request(save=False)
        self._save_state()
        await self._schedule_status_loop(float(self._poll_interval_seconds))

    def _request_expired(self, requested_at: datetime | None, now: datetime) -> bool:
        return requested_at is not None and (now - requested_at).total_seconds() > self._REPLY_WINDOW_SECONDS

    async def _handle_status_snapshot(self, snapshot: _StatusSnapshot, now: datetime) -> None:
        if snapshot.wenxin_done is not None:
            self._today_wenxin_done = snapshot.wenxin_done
            if not snapshot.wenxin_done:
                self._seal_name = None
        if snapshot.seal_name is not None or snapshot.wenxin_done is True:
            self._seal_name = snapshot.seal_name
        if snapshot.jiutian_unlocked is not None:
            self._jiutian_unlocked = snapshot.jiutian_unlocked
            if snapshot.jiutian_unlocked is False:
                self._jiutian_cooldown_until = None
                self._next_jiutian_at = None
        if snapshot.tianmen_unlocked is not None:
            self._tianmen_unlocked = snapshot.tianmen_unlocked

        if snapshot.cooldown_seconds is not None:
            if snapshot.cooldown_seconds <= 0:
                self._cooldown_until = now
            else:
                self._cooldown_until = now + timedelta(
                    seconds=snapshot.cooldown_seconds + self._COOLDOWN_BUFFER_SECONDS
                )
            if self._climb_enabled:
                await self._schedule_climb_loop((self._cooldown_until - now).total_seconds())
        else:
            self._cooldown_until = None
            self._next_climb_at = None

        if snapshot.jiutian_cooldown_seconds is not None:
            if snapshot.jiutian_cooldown_seconds <= 0:
                self._jiutian_cooldown_until = now
            else:
                self._jiutian_cooldown_until = now + timedelta(
                    seconds=snapshot.jiutian_cooldown_seconds + self._COOLDOWN_BUFFER_SECONDS
                )
            if self._jiutian_enabled and self._jiutian_unlocked is True:
                await self._schedule_jiutian_loop((self._jiutian_cooldown_until - now).total_seconds())

        self._save_state()
        await self._schedule_status_loop(float(self._poll_interval_seconds))

        if (
            self._jiutian_enabled
            and self._jiutian_requested_at is None
            and self._jiutian_unlocked is True
            and (self._jiutian_cooldown_until is None or now >= self._jiutian_cooldown_until)
        ):
            await self._jiutian_loop()
            return

        if self._should_request_wenxin() and self._wenxin_requested_at is None:
            await self._request_wenxin()
            return

        if (
            self._climb_enabled
            and self._climb_requested_at is None
            and self._wenxin_requested_at is None
            and self._jiutian_requested_at is None
            and (self._cooldown_until is None or now >= self._cooldown_until)
        ):
            await self._climb_loop()

    async def on_message(self, ctx: MessageContext):
        text = (ctx.text or "").strip()
        if not text or text.startswith("."):
            return None

        now = datetime.now()
        self._reset_if_new_day(now)

        snapshot = self._parse_status_snapshot(text)
        if snapshot is not None:
            if self._request_expired(self._status_requested_at, now):
                self._clear_status_request()
            if self._status_requested_at is not None and (
                (self._status_request_msg_id is not None and ctx.reply_to_msg_id == self._status_request_msg_id)
                or ctx.is_reply_to_me
            ):
                self._clear_status_request(save=False)
                await self._handle_status_snapshot(snapshot, now)
                return None

        wenxin_feedback = self._parse_wenxin_feedback(text)
        if wenxin_feedback is not None:
            if self._request_expired(self._wenxin_requested_at, now):
                self._clear_wenxin_request()
            if self._wenxin_requested_at is not None and (
                (self._wenxin_request_msg_id is not None and ctx.reply_to_msg_id == self._wenxin_request_msg_id)
                or ctx.is_reply_to_me
            ):
                self._clear_wenxin_request(save=False)
                self._today_wenxin_done = wenxin_feedback[0]
                if wenxin_feedback[1] is not None:
                    self._seal_name = wenxin_feedback[1]
                self._save_state()
                await self._schedule_status_loop(float(self._ACTION_SPACING_SECONDS))
                return None

        jiutian_feedback = self._parse_jiutian_feedback(text)
        if jiutian_feedback is not None:
            if self._request_expired(self._jiutian_requested_at, now):
                self._clear_jiutian_request()
            if self._jiutian_requested_at is not None and (
                (self._jiutian_request_msg_id is not None and ctx.reply_to_msg_id == self._jiutian_request_msg_id)
                or ctx.is_reply_to_me
            ):
                self._clear_jiutian_request(save=False)
                self._jiutian_unlocked = True
                if jiutian_feedback[0] == "cooldown":
                    remaining = self._parse_duration_seconds(text)
                    if remaining is not None:
                        self._jiutian_cooldown_until = now + timedelta(
                            seconds=max(0, remaining) + self._COOLDOWN_BUFFER_SECONDS
                        )
                        self._save_state()
                        await self._schedule_jiutian_loop((self._jiutian_cooldown_until - now).total_seconds())
                        await self._schedule_status_loop(float(self._poll_interval_seconds))
                        return None
                self._jiutian_cooldown_until = now + timedelta(
                    seconds=self._JIUTIAN_COOLDOWN_SECONDS + self._COOLDOWN_BUFFER_SECONDS
                )
                if jiutian_feedback[1] is not None:
                    self._seal_name = jiutian_feedback[1]
                self._save_state()
                await self._schedule_jiutian_loop((self._jiutian_cooldown_until - now).total_seconds())
                await self._schedule_status_loop(float(self._ACTION_SPACING_SECONDS))
                return None

        climb_feedback = self._parse_climb_feedback_kind(text)
        if climb_feedback is not None:
            if self._request_expired(self._climb_requested_at, now):
                self._clear_climb_request()
            if self._climb_requested_at is not None and (
                (self._climb_request_msg_id is not None and ctx.reply_to_msg_id == self._climb_request_msg_id)
                or ctx.is_reply_to_me
            ):
                self._clear_climb_request(save=False)
                if climb_feedback == "cooldown":
                    remaining = self._parse_duration_seconds(text)
                    if remaining is not None:
                        self._cooldown_until = now + timedelta(
                            seconds=max(0, remaining) + self._COOLDOWN_BUFFER_SECONDS
                        )
                        self._save_state()
                        await self._schedule_climb_loop((self._cooldown_until - now).total_seconds())
                        await self._schedule_status_loop(float(self._poll_interval_seconds))
                        return None
                if climb_feedback == "result":
                    self._today_climb_count += 1
                self._save_state()
                await self._schedule_status_loop(float(self._ACTION_SPACING_SECONDS))
                return None

        return None
