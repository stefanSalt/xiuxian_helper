from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

from ..config import Config
from ..core.contracts import MessageContext, SendAction
from ..core.scheduler import Scheduler
from ..core.state_store import (
    SQLiteStateStore,
    coerce_int,
    deserialize_date,
    deserialize_datetime,
    serialize_date,
    serialize_datetime,
)
from ..domain.xinggong import parse_xinggong_observatory


class AutoXinggongPlugin:
    """星宫自动化：观星台 + 周天星斗大阵 + 观星劫持。"""

    name = "xinggong"
    priority = 40

    _CMD_OBSERVATORY = ".观星台"
    _CMD_SOOTHE = ".安抚星辰"
    _CMD_COLLECT = ".收集精华"
    _CMD_QIZHEN = ".启阵"
    _CMD_ZHUZHEN = ".助阵"
    _CMD_WENAN = ".每日问安"
    _CMD_GUANXING = ".观星"
    _CMD_GAIHUAN = ".改换星移"
    _CMD_VIEW_BIGUAN = ".查看闭关"
    _CMD_DEEP_BIGUAN = ".深度闭关"
    _CMD_FORCE_EXIT = ".强行出关"
    _MATURE_CHECK_BUFFER_SECONDS = 10
    _QIZHEN_BUFF_SECONDS = 6 * 3600
    _QIZHEN_COOLDOWN_BUFFER_SECONDS = 5
    _QIZHEN_COOLDOWN_SECONDS = 12 * 3600
    _QIZHEN_FEEDBACK_WINDOW_SECONDS = 210
    _QIZHEN_EXISTING_INVITE_WAIT_SECONDS = 210
    _DEEP_BIGUAN_REFRESH_DELAY_SECONDS = 5 * 3600
    _DEEP_BIGUAN_KEEP_REASON = "post_buff_keep"
    _STATUS_REPLY_WINDOW_SECONDS = 120
    _GUANXING_VALID_SECONDS = 300

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
        self._guanxing_enabled = bool(config.enable_xinggong_guanxing)
        self._wenan_interval_seconds = max(60, int(config.xinggong_wenan_interval_seconds))
        self._global_send_min_interval_seconds = max(0, int(config.global_send_min_interval_seconds))
        self._guanxing_target_username = self._normalize_username(
            config.xinggong_guanxing_target_username
        )
        self._guanxing_preview_advance_seconds = max(
            1, int(config.xinggong_guanxing_preview_advance_seconds)
        )
        self._guanxing_shift_advance_seconds = max(
            1, int(config.xinggong_guanxing_shift_advance_seconds)
        )
        self._guanxing_watch_events = self._parse_watch_events(
            config.xinggong_guanxing_watch_events
        )

        self._qizhen_hm = self._parse_hhmm(config.xinggong_qizhen_start_time)
        self._qizhen_retry_seconds = max(30, int(config.xinggong_qizhen_retry_interval_seconds))
        self._qizhen_second_offset_seconds = max(0, int(config.xinggong_qizhen_second_offset_seconds))

        self._scheduler: Scheduler | None = None
        self._send = None
        self._state_store: SQLiteStateStore | None = None

        # Cycle state (a "day" starts at qizhen start time, not at midnight).
        self._cycle_date: date | None = None
        self._qizhen_first_success_at: datetime | None = None
        self._qizhen_second_success_at: datetime | None = None
        self._qizhen_pending_slot: int | None = None
        self._qizhen_last_invite_msg_id: int | None = None
        self._qizhen_last_invite_slot: int | None = None
        # Cooldown observed from bot replies (may span across cycles).
        self._qizhen_blocked_until: datetime | None = None
        self._qizhen_next_cycle_at: datetime | None = None
        self._qizhen_last_sent_at: datetime | None = None
        self._qizhen_existing_invite_until: datetime | None = None

        self._assist_blocked_until: datetime | None = None
        self._deep_biguan_status_msg_id: int | None = None
        self._deep_biguan_status_requested_at: datetime | None = None
        self._deep_biguan_status_reason: str | None = None
        self._guanxing_claim_active = False
        self._guanxing_claim_event: str | None = None
        self._guanxing_settlement_at: datetime | None = None
        self._guanxing_window_expires_at: datetime | None = None
        self._guanxing_own_command_msg_id: int | None = None
        self._guanxing_own_preview_msg_id: int | None = None
        self._guanxing_preview_sent = False
        self._guanxing_shift_sent = False
        self._guanxing_detected_settlement_at: datetime | None = None
        self._next_poll_at: datetime | None = None
        self._wenan_next_at: datetime | None = None

        if self.enabled:
            self._logger.info(
                "xinggong_plugin_enabled star=%s poll_interval_seconds=%s qizhen_start=%s retry_seconds=%s second_offset_seconds=%s wenan_enabled=%s deep_biguan_enabled=%s guanxing_enabled=%s guanxing_preview_advance_seconds=%s guanxing_shift_advance_seconds=%s wenan_interval_seconds=%s",
                self._star_name,
                self._poll_interval_seconds,
                config.xinggong_qizhen_start_time,
                self._qizhen_retry_seconds,
                self._qizhen_second_offset_seconds,
                self._wenan_enabled,
                self._deep_biguan_enabled,
                self._guanxing_enabled,
                self._guanxing_preview_advance_seconds,
                self._guanxing_shift_advance_seconds,
                self._wenan_interval_seconds,
            )

    def set_state_store(self, state_store: SQLiteStateStore) -> None:
        self._state_store = state_store

    def restore_state(self) -> None:
        if self._state_store is None:
            return
        state = self._state_store.load_state(self.name)
        self._cycle_date = deserialize_date(state.get("cycle_date"))
        self._qizhen_first_success_at = deserialize_datetime(state.get("qizhen_first_success_at"))
        self._qizhen_second_success_at = deserialize_datetime(state.get("qizhen_second_success_at"))
        self._qizhen_pending_slot = coerce_int(state.get("qizhen_pending_slot"))
        self._qizhen_last_invite_msg_id = coerce_int(state.get("qizhen_last_invite_msg_id"))
        self._qizhen_last_invite_slot = coerce_int(state.get("qizhen_last_invite_slot"))
        self._qizhen_blocked_until = deserialize_datetime(state.get("qizhen_blocked_until"))
        self._qizhen_next_cycle_at = deserialize_datetime(state.get("qizhen_next_cycle_at"))
        self._qizhen_last_sent_at = deserialize_datetime(state.get("qizhen_last_sent_at"))
        self._qizhen_existing_invite_until = deserialize_datetime(state.get("qizhen_existing_invite_until"))
        self._assist_blocked_until = deserialize_datetime(state.get("assist_blocked_until"))
        self._deep_biguan_status_msg_id = coerce_int(state.get("deep_biguan_status_msg_id"))
        self._deep_biguan_status_requested_at = deserialize_datetime(
            state.get("deep_biguan_status_requested_at")
        )
        reason = state.get("deep_biguan_status_reason")
        self._deep_biguan_status_reason = reason if isinstance(reason, str) and reason else None
        self._guanxing_claim_active = bool(state.get("guanxing_claim_active", False))
        claim_event = state.get("guanxing_claim_event")
        self._guanxing_claim_event = claim_event if isinstance(claim_event, str) and claim_event else None
        self._guanxing_settlement_at = deserialize_datetime(state.get("guanxing_settlement_at"))
        self._guanxing_window_expires_at = deserialize_datetime(state.get("guanxing_window_expires_at"))
        self._guanxing_own_command_msg_id = coerce_int(state.get("guanxing_own_command_msg_id"))
        self._guanxing_own_preview_msg_id = coerce_int(state.get("guanxing_own_preview_msg_id"))
        self._guanxing_preview_sent = bool(state.get("guanxing_preview_sent", False))
        self._guanxing_shift_sent = bool(state.get("guanxing_shift_sent", False))
        self._guanxing_detected_settlement_at = deserialize_datetime(
            state.get("guanxing_detected_settlement_at")
        )
        self._next_poll_at = deserialize_datetime(state.get("next_poll_at"))
        self._wenan_next_at = deserialize_datetime(state.get("wenan_next_at"))
        self._sanitize_restored_state()
        self._save_state()

    def _save_state(self) -> None:
        if self._state_store is None:
            return
        self._state_store.save_state(
            self.name,
            {
                "cycle_date": serialize_date(self._cycle_date),
                "qizhen_first_success_at": serialize_datetime(self._qizhen_first_success_at),
                "qizhen_second_success_at": serialize_datetime(self._qizhen_second_success_at),
                "qizhen_pending_slot": self._qizhen_pending_slot,
                "qizhen_last_invite_msg_id": self._qizhen_last_invite_msg_id,
                "qizhen_last_invite_slot": self._qizhen_last_invite_slot,
                "qizhen_blocked_until": serialize_datetime(self._qizhen_blocked_until),
                "qizhen_next_cycle_at": serialize_datetime(self._qizhen_next_cycle_at),
                "qizhen_last_sent_at": serialize_datetime(self._qizhen_last_sent_at),
                "qizhen_existing_invite_until": serialize_datetime(self._qizhen_existing_invite_until),
                "assist_blocked_until": serialize_datetime(self._assist_blocked_until),
                "deep_biguan_status_msg_id": self._deep_biguan_status_msg_id,
                "deep_biguan_status_requested_at": serialize_datetime(
                    self._deep_biguan_status_requested_at
                ),
                "deep_biguan_status_reason": self._deep_biguan_status_reason,
                "guanxing_claim_active": self._guanxing_claim_active,
                "guanxing_claim_event": self._guanxing_claim_event,
                "guanxing_settlement_at": serialize_datetime(self._guanxing_settlement_at),
                "guanxing_window_expires_at": serialize_datetime(self._guanxing_window_expires_at),
                "guanxing_own_command_msg_id": self._guanxing_own_command_msg_id,
                "guanxing_own_preview_msg_id": self._guanxing_own_preview_msg_id,
                "guanxing_preview_sent": self._guanxing_preview_sent,
                "guanxing_shift_sent": self._guanxing_shift_sent,
                "guanxing_detected_settlement_at": serialize_datetime(
                    self._guanxing_detected_settlement_at
                ),
                "next_poll_at": serialize_datetime(self._next_poll_at),
                "wenan_next_at": serialize_datetime(self._wenan_next_at),
            },
        )

    def _my_tag(self) -> str:
        name = self._config.my_name.strip()
        if not name:
            return ""
        return name if name.startswith("@") else f"@{name}"

    def _normalize_username(self, raw: str) -> str:
        value = (raw or "").strip()
        if not value:
            return ""
        return value if value.startswith("@") else f"@{value}"

    def _parse_watch_events(self, raw: str) -> tuple[str, ...]:
        items = tuple(part.strip() for part in (raw or "").split(",") if part.strip())
        return items or ("星辰异象", "地磁暴动")

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

    def _sanitize_restored_state(self) -> None:
        now = datetime.now()
        self._reset_if_new_cycle(now)
        self._expire_guanxing_claim_if_needed(now)
        if self._assist_blocked_until is not None and now >= self._assist_blocked_until:
            self._assist_blocked_until = None
        if (
            self._qizhen_existing_invite_until is not None
            and now >= self._qizhen_existing_invite_until
        ):
            self._qizhen_existing_invite_until = None
        if (
            self._deep_biguan_status_requested_at is not None
            and (now - self._deep_biguan_status_requested_at).total_seconds()
            > self._STATUS_REPLY_WINDOW_SECONDS
        ):
            self._clear_pending_biguan_status()

    def _clear_qizhen_cycle_state(
        self,
        *,
        cycle_date: date,
        clear_blocked_until: bool,
    ) -> None:
        self._cycle_date = cycle_date
        self._qizhen_first_success_at = None
        self._qizhen_second_success_at = None
        self._qizhen_pending_slot = None
        self._qizhen_last_invite_msg_id = None
        self._qizhen_last_invite_slot = None
        self._qizhen_existing_invite_until = None
        self._qizhen_next_cycle_at = None
        if clear_blocked_until:
            self._qizhen_blocked_until = None
        self._assist_blocked_until = None
        self._clear_pending_biguan_status()
        self._save_state()

    def _reset_if_new_cycle(self, now: datetime) -> None:
        if self._qizhen_next_cycle_at is not None and now >= self._qizhen_next_cycle_at:
            self._clear_qizhen_cycle_state(cycle_date=now.date(), clear_blocked_until=True)
            return
        cycle = self._cycle_date_for(now)
        if self._cycle_date == cycle:
            return
        self._clear_qizhen_cycle_state(cycle_date=cycle, clear_blocked_until=False)

    def _next_poll_delay_seconds(self, status) -> float:
        base = float(self._poll_interval_seconds)
        if status.min_remaining_seconds is None:
            return base
        delay = float(status.min_remaining_seconds + self._MATURE_CHECK_BUFFER_SECONDS)
        delay = max(1.0, min(base, delay))
        return delay

    def _next_guanxing_settlement_at(self, now: datetime) -> datetime:
        aligned = now.replace(minute=0, second=0, microsecond=0)
        next_hour = ((aligned.hour // 3) + 1) * 3
        if next_hour >= 24:
            aligned = aligned + timedelta(days=1)
            next_hour = 0
        return aligned.replace(hour=next_hour)

    def _guanxing_window_start(self, settlement_at: datetime) -> datetime:
        return settlement_at - timedelta(hours=3)

    def _clear_guanxing_claim_state(self) -> None:
        self._guanxing_claim_active = False
        self._guanxing_claim_event = None
        self._guanxing_settlement_at = None
        self._guanxing_window_expires_at = None
        self._guanxing_own_command_msg_id = None
        self._guanxing_own_preview_msg_id = None
        self._guanxing_preview_sent = False
        self._guanxing_shift_sent = False
        self._save_state()

    def _expire_guanxing_claim_if_needed(self, now: datetime) -> None:
        if not self._guanxing_claim_active:
            return
        if self._guanxing_settlement_at is not None and now >= self._guanxing_settlement_at:
            self._clear_guanxing_claim_state()
            return
        if (
            self._guanxing_window_expires_at is not None
            and now >= self._guanxing_window_expires_at
            and self._guanxing_own_preview_msg_id is None
        ):
            self._clear_guanxing_claim_state()

    def _should_ignore_external_guanxing_preview(self, now: datetime) -> bool:
        settlement_at = self._next_guanxing_settlement_at(now)
        window_start = self._guanxing_window_start(settlement_at)
        return now < (window_start + timedelta(seconds=self._GUANXING_VALID_SECONDS))

    def _is_guanxing_preview(self, text: str) -> bool:
        return "【星盘显化】" in text

    def _match_guanxing_event(self, text: str) -> str | None:
        if not self._is_guanxing_preview(text):
            return None
        for event_name in self._guanxing_watch_events:
            if event_name and event_name in text:
                return event_name
        return None

    def _is_own_guanxing_preview(self, ctx: MessageContext, text: str) -> bool:
        if not self._is_guanxing_preview(text):
            return False
        if self._guanxing_own_command_msg_id is None:
            return False
        return ctx.reply_to_msg_id == self._guanxing_own_command_msg_id

    def _is_critical_guanxing_send(self, plugin: str, text: str) -> bool:
        if plugin != self.name:
            return False
        return text == self._CMD_GUANXING or text.startswith(self._CMD_GAIHUAN)

    def send_block_delay_seconds(
        self,
        plugin: str,
        text: str,
        *,
        now: datetime | None = None,
    ) -> float:
        if not self.enabled or not self._guanxing_enabled:
            return 0.0
        current = now or datetime.now()
        self._expire_guanxing_claim_if_needed(current)
        if not self._guanxing_claim_active or self._guanxing_settlement_at is None:
            return 0.0
        if self._is_critical_guanxing_send(plugin, text):
            return 0.0
        shift_at = self._guanxing_settlement_at - timedelta(
            seconds=self._guanxing_shift_advance_seconds
        )
        block_start = shift_at - timedelta(seconds=self._global_send_min_interval_seconds)
        if current < block_start:
            return 0.0
        return max(0.0, (self._guanxing_settlement_at - current).total_seconds())

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
    ) -> tuple[datetime | None, int | None]:
        success_at = self._infer_qizhen_success_at(now, remaining_cooldown_seconds)
        cycle_start = self._cycle_start_dt(now)
        pending_slot = self._qizhen_pending_slot

        if self._qizhen_first_success_at is None and self._qizhen_second_success_at is None:
            if pending_slot == 2:
                inferred_first_success_at = max(
                    cycle_start,
                    success_at - timedelta(seconds=self._qizhen_second_offset_seconds),
                )
                self._qizhen_first_success_at = inferred_first_success_at
                self._qizhen_second_success_at = success_at
                return success_at, 2
            self._qizhen_first_success_at = success_at
            return success_at, 1

        if self._qizhen_first_success_at is not None and self._qizhen_second_success_at is None:
            second_start = self._qizhen_first_success_at + timedelta(seconds=self._qizhen_second_offset_seconds)
            if pending_slot == 2 or success_at >= second_start:
                self._qizhen_second_success_at = success_at
                return success_at, 2

        return None, None

    def _set_qizhen_cooldown_from_success(self, success_at: datetime, *, slot: int) -> None:
        blocked_until = success_at + timedelta(seconds=self._QIZHEN_COOLDOWN_SECONDS + self._QIZHEN_COOLDOWN_BUFFER_SECONDS)
        self._qizhen_blocked_until = blocked_until
        if slot == 2:
            self._qizhen_next_cycle_at = blocked_until
        else:
            self._qizhen_next_cycle_at = None
        self._save_state()

    def _is_related_qizhen_feedback(self, ctx: MessageContext, now: datetime) -> bool:
        if ctx.is_reply_to_me:
            return True
        if self._qizhen_pending_slot not in (1, 2):
            return False
        if self._qizhen_last_sent_at is None:
            return False
        return (now - self._qizhen_last_sent_at) <= timedelta(seconds=self._QIZHEN_FEEDBACK_WINDOW_SECONDS)

    def _clear_qizhen_existing_invite_wait(self) -> None:
        self._qizhen_existing_invite_until = None
        self._save_state()

    def _clear_pending_biguan_status(self) -> None:
        self._deep_biguan_status_msg_id = None
        self._deep_biguan_status_requested_at = None
        self._deep_biguan_status_reason = None
        self._save_state()

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

        buff_end = success_at + timedelta(seconds=self._QIZHEN_BUFF_SECONDS)
        if now >= buff_end:
            await self._schedule_deep_biguan_status_check(
                0.0,
                key="xinggong.deep_biguan.status.keep",
                reason=self._DEEP_BIGUAN_KEEP_REASON,
            )
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
        self._save_state()

    def _initial_wenan_delay_seconds(self) -> float:
        now = datetime.now()
        if self._wenan_next_at is not None and now < self._wenan_next_at:
            return max(0.0, (self._wenan_next_at - now).total_seconds())
        return float(self._wenan_interval_seconds)

    async def bootstrap(self, scheduler: Scheduler, send) -> None:
        if not self.enabled:
            return
        self._scheduler = scheduler
        self._send = send
        poll_delay_seconds = 0.0
        if self._next_poll_at is not None:
            poll_delay_seconds = max(0.0, (self._next_poll_at - datetime.now()).total_seconds())
        await self._schedule_observatory_poll(poll_delay_seconds)
        await self._schedule_qizhen_loop(0.0)
        if self._wenan_enabled:
            await self._schedule_wenan_loop(self._initial_wenan_delay_seconds())
        await self._restore_deep_biguan_schedule()
        await self._restore_guanxing_schedules()

    async def _schedule_observatory_poll(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return

        async def _runner() -> None:
            await self._observatory_poll_loop()

        await self._scheduler.schedule(
            key="xinggong.poll",
            delay_seconds=max(0.0, delay_seconds),
            action=_runner,
        )

    async def _observatory_poll_loop(self) -> None:
        if not self.enabled or self._send is None:
            return
        self._next_poll_at = None
        self._save_state()
        await self._send(self.name, self._CMD_OBSERVATORY, True)

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
        self._wenan_next_at = datetime.now() + timedelta(seconds=self._wenan_interval_seconds)
        self._save_state()
        await self._send(self.name, self._CMD_WENAN, True)
        await self._schedule_wenan_loop(float(self._wenan_interval_seconds))

    async def _restore_deep_biguan_schedule(self) -> None:
        if not self._deep_biguan_enabled:
            return
        latest_success_at = self._qizhen_second_success_at or self._qizhen_first_success_at
        if latest_success_at is None:
            return
        cooldown_end = latest_success_at + timedelta(
            seconds=self._QIZHEN_COOLDOWN_SECONDS + self._QIZHEN_COOLDOWN_BUFFER_SECONDS
        )
        now = datetime.now()
        if now >= cooldown_end:
            return
        await self._schedule_deep_biguan_after_qizhen_success(
            latest_success_at,
            now,
            immediate_reason="restart_restore",
        )

    async def _restore_guanxing_schedules(self) -> None:
        if (
            not self._guanxing_enabled
            or not self._guanxing_claim_active
            or self._guanxing_settlement_at is None
        ):
            return
        now = datetime.now()
        shift_at = self._guanxing_settlement_at - timedelta(
            seconds=self._guanxing_shift_advance_seconds
        )
        if not self._guanxing_preview_sent and self._guanxing_own_command_msg_id is None:
            preview_at = self._guanxing_settlement_at - timedelta(
                seconds=self._guanxing_preview_advance_seconds
            )
            await self._schedule_guanxing_preview(
                max(0.0, (preview_at - now).total_seconds())
            )
        if not self._guanxing_shift_sent:
            await self._schedule_guanxing_shift(max(0.0, (shift_at - now).total_seconds()))

    async def _schedule_guanxing_preview(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return

        async def _runner() -> None:
            await self._send_guanxing_preview()

        await self._scheduler.schedule(
            key="xinggong.guanxing.preview",
            delay_seconds=max(0.0, delay_seconds),
            action=_runner,
        )

    async def _schedule_guanxing_shift(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return

        async def _runner() -> None:
            await self._send_guanxing_shift()

        await self._scheduler.schedule(
            key="xinggong.guanxing.shift",
            delay_seconds=max(0.0, delay_seconds),
            action=_runner,
        )

    async def _register_guanxing_claim(self, now: datetime, event_name: str) -> None:
        if (
            not self.enabled
            or not self._guanxing_enabled
            or self._send is None
            or not self._guanxing_target_username
        ):
            return

        settlement_at = self._next_guanxing_settlement_at(now)
        if self._guanxing_detected_settlement_at == settlement_at:
            return

        shift_at = settlement_at - timedelta(seconds=self._guanxing_shift_advance_seconds)
        if shift_at <= now or self._should_ignore_external_guanxing_preview(now):
            return

        self._guanxing_claim_active = True
        self._guanxing_claim_event = event_name
        self._guanxing_settlement_at = settlement_at
        self._guanxing_window_expires_at = None
        self._guanxing_own_command_msg_id = None
        self._guanxing_own_preview_msg_id = None
        self._guanxing_preview_sent = False
        self._guanxing_shift_sent = False
        self._guanxing_detected_settlement_at = settlement_at
        self._save_state()

        preview_at = settlement_at - timedelta(seconds=self._guanxing_preview_advance_seconds)
        preview_delay = max(0.0, (preview_at - now).total_seconds())
        await self._schedule_guanxing_preview(preview_delay)
        await self._schedule_guanxing_shift((shift_at - now).total_seconds())

    async def _send_guanxing_preview(self) -> None:
        if (
            not self.enabled
            or not self._guanxing_enabled
            or self._send is None
            or not self._guanxing_target_username
        ):
            return
        now = datetime.now()
        self._expire_guanxing_claim_if_needed(now)
        if not self._guanxing_claim_active or self._guanxing_settlement_at is None:
            return
        if self._guanxing_preview_sent or self._guanxing_own_command_msg_id is not None:
            return
        if now >= self._guanxing_settlement_at:
            self._clear_guanxing_claim_state()
            return
        self._guanxing_preview_sent = True
        self._save_state()
        msg_id = await self._send(self.name, self._CMD_GUANXING, True)
        if msg_id is None:
            self._clear_guanxing_claim_state()
            return
        self._guanxing_own_command_msg_id = msg_id
        self._save_state()

    async def _send_guanxing_shift(self) -> None:
        if (
            not self.enabled
            or not self._guanxing_enabled
            or self._send is None
            or not self._guanxing_target_username
        ):
            return
        now = datetime.now()
        self._expire_guanxing_claim_if_needed(now)
        if not self._guanxing_claim_active or self._guanxing_settlement_at is None:
            return

        if self._guanxing_own_preview_msg_id is None:
            remaining = (self._guanxing_settlement_at - now).total_seconds()
            if remaining > 0.2:
                await self._schedule_guanxing_shift(min(0.2, remaining))
            else:
                self._clear_guanxing_claim_state()
            return

        self._guanxing_shift_sent = True
        self._save_state()
        await self._send(
            self.name,
            f"{self._CMD_GAIHUAN} {self._guanxing_target_username}",
            True,
            reply_to_msg_id=self._guanxing_own_preview_msg_id,
        )
        self._clear_guanxing_claim_state()

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
            if self._qizhen_existing_invite_until is not None and self._qizhen_existing_invite_until > desired_start:
                desired_start = self._qizhen_existing_invite_until
            if now < desired_start:
                await self._schedule_qizhen_loop((desired_start - now).total_seconds())
                return
            self._clear_qizhen_existing_invite_wait()
            self._qizhen_pending_slot = 1
            self._qizhen_last_sent_at = now
            self._save_state()
            await self._send(self.name, self._CMD_QIZHEN, True)
            await self._schedule_qizhen_loop(float(self._qizhen_retry_seconds))
            return

        if self._qizhen_second_success_at is None:
            second_start = self._qizhen_first_success_at + timedelta(seconds=self._qizhen_second_offset_seconds)
            desired_start = second_start
            if self._qizhen_blocked_until is not None and self._qizhen_blocked_until > desired_start:
                desired_start = self._qizhen_blocked_until
            if self._qizhen_existing_invite_until is not None and self._qizhen_existing_invite_until > desired_start:
                desired_start = self._qizhen_existing_invite_until
            if now < desired_start:
                await self._schedule_qizhen_loop((desired_start - now).total_seconds())
                return
            self._clear_qizhen_existing_invite_wait()
            self._qizhen_pending_slot = 2
            self._qizhen_last_sent_at = now
            self._save_state()
            await self._send(self.name, self._CMD_QIZHEN, True)
            await self._schedule_qizhen_loop(float(self._qizhen_retry_seconds))
            return

        # Both runs done for this cycle; wait for the next cooldown-driven cycle rollover.
        if self._qizhen_next_cycle_at is not None and now < self._qizhen_next_cycle_at:
            await self._schedule_qizhen_loop((self._qizhen_next_cycle_at - now).total_seconds())
            return

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
        self._expire_guanxing_claim_if_needed(now)

        if self._guanxing_enabled:
            if self._is_own_guanxing_preview(ctx, text):
                self._guanxing_own_preview_msg_id = ctx.message_id
                self._guanxing_window_expires_at = now + timedelta(
                    seconds=self._GUANXING_VALID_SECONDS
                )
                self._save_state()
                return None

            if (
                self._guanxing_claim_active
                and self._guanxing_own_command_msg_id is not None
                and ctx.reply_to_msg_id == self._guanxing_own_command_msg_id
                and "你今日已观星一次，天机不可多泄，请明日再来" in text
            ):
                self._clear_guanxing_claim_state()
                return None

            matched_event = self._match_guanxing_event(text)
            if matched_event is not None:
                if not self._is_own_guanxing_preview(ctx, text):
                    await self._register_guanxing_claim(now, matched_event)
                return None

        # ---- 周天星斗大阵：成功/邀请/助阵冷却 ----
        my_tag = self._my_tag()
        if "再次启阵" in text and "请在" in text:
            # e.g. 你刚刚参与过布阵... 请在 11小时7分钟39秒 后再次启阵。
            if not self._is_related_qizhen_feedback(ctx, now):
                return None
            rem = self._parse_duration_seconds(text)
            if rem is None:
                return None
            blocked_until = now + timedelta(seconds=rem + self._QIZHEN_COOLDOWN_BUFFER_SECONDS)
            if self._qizhen_blocked_until is None or blocked_until > self._qizhen_blocked_until:
                self._qizhen_blocked_until = blocked_until
            self._clear_qizhen_existing_invite_wait()
            recovered_success_at, recovered_slot = self._recover_qizhen_success_from_cooldown(now, rem)
            if recovered_success_at is not None and recovered_slot is not None:
                self._set_qizhen_cooldown_from_success(recovered_success_at, slot=recovered_slot)
            # Stop retries; schedule the next loop at cooldown end.
            self._qizhen_pending_slot = None
            self._save_state()
            if recovered_success_at is not None:
                await self._schedule_deep_biguan_after_qizhen_success(
                    recovered_success_at,
                    now,
                    immediate_reason="qizhen_recovered",
                )
            if self._scheduler is not None:
                await self._schedule_qizhen_loop(max(0.0, (self._qizhen_blocked_until - now).total_seconds()))
            return None

        if "已发布启阵邀请" in text and "请勿重复操作" in text:
            if not self._is_related_qizhen_feedback(ctx, now):
                return None
            self._qizhen_existing_invite_until = now + timedelta(seconds=self._QIZHEN_EXISTING_INVITE_WAIT_SECONDS)
            self._save_state()
            if self._scheduler is not None:
                await self._schedule_qizhen_loop(
                    max(0.0, (self._qizhen_existing_invite_until - now).total_seconds())
                )
            return None

        if "周天星斗大阵-启" in text:
            if my_tag and my_tag in text:
                # This is the bot's invite message for our own ".启阵".
                self._clear_qizhen_existing_invite_wait()
                self._qizhen_last_invite_msg_id = ctx.message_id
                self._qizhen_last_invite_slot = self._qizhen_pending_slot
                self._save_state()
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
                self._save_state()
            return None

        if "周天星斗大阵-成" in text or ("大阵已成" in text and "周天星斗大阵" in text):
            # Treat as success only if it matches our own invite edit, or explicitly mentions us.
            is_mine = (self._qizhen_last_invite_msg_id == ctx.message_id) or (my_tag and my_tag in text)
            if is_mine and self._qizhen_pending_slot in (1, 2):
                success_slot = self._qizhen_pending_slot
                if success_slot == 1 and self._qizhen_first_success_at is None:
                    self._qizhen_first_success_at = now
                elif success_slot == 2 and self._qizhen_second_success_at is None:
                    self._qizhen_second_success_at = now
                self._set_qizhen_cooldown_from_success(now, slot=success_slot)
                self._clear_qizhen_existing_invite_wait()
                self._qizhen_pending_slot = None
                self._save_state()
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
            status_reason = self._deep_biguan_status_reason
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
                if status_reason == self._DEEP_BIGUAN_KEEP_REASON:
                    return None
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
            self._next_poll_at = now + timedelta(seconds=self._spacing_seconds)
            self._save_state()
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
            self._next_poll_at = now + timedelta(seconds=self._spacing_seconds)
            self._save_state()
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
        self._next_poll_at = now + timedelta(seconds=poll_delay_seconds)
        self._save_state()
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
