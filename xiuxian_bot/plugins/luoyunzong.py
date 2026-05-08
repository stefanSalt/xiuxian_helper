from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Awaitable

from ..config import Config
from ..core.contracts import MessageContext, SendAction
from ..core.scheduler import Scheduler
from ..core.state_store import SQLiteStateStore, deserialize_datetime, serialize_datetime

SendFn = Callable[[str, str, bool], Awaitable[int | None]]
NowFn = Callable[[], datetime]


class LuoyunzongPlugin:
    """落云宗灵眼之树：状态检查、灌溉、守山、采摘。"""

    name = "luoyunzong"
    priority = 10

    _CMD_LINGGEN = ".我的灵根"
    _CMD_STATUS = ".灵树状态"
    _CMD_WATER = ".灵树灌溉"
    _CMD_GUARD = ".协同守山"
    _CMD_HARVEST = ".采摘灵果"
    _STATUS_LOOP_KEY = "luoyunzong.status.loop"
    _STATE_KEY = "luoyunzong"
    _GUARD_SUPPRESS_SECONDS = 300
    _VALID_WATERING_STRATEGIES = {"match_linggen", "always", "match_need"}

    def __init__(
        self,
        config: Config,
        logger: logging.Logger,
        *,
        now_fn: NowFn | None = None,
    ) -> None:
        self._logger = logger
        self.enabled = bool(getattr(config, "enable_luoyunzong", False))
        self._status_interval_seconds = max(
            60,
            int(getattr(config, "luoyunzong_status_interval_seconds", 1800)),
        )
        self._watering_cooldown_seconds = max(
            60,
            int(getattr(config, "luoyunzong_watering_cooldown_seconds", 7200)),
        )
        self._linggen_refresh_seconds = max(
            60,
            int(getattr(config, "luoyunzong_linggen_refresh_seconds", 86400)),
        )
        self._harvest_suppress_seconds = max(
            60,
            int(getattr(config, "luoyunzong_harvest_suppress_seconds", 86400)),
        )
        strategy = str(getattr(config, "luoyunzong_watering_strategy", "match_linggen")).strip()
        if strategy not in self._VALID_WATERING_STRATEGIES:
            strategy = "match_linggen"
        self._watering_strategy = strategy
        self._watering_required_needs = self._split_tokens(
            str(getattr(config, "luoyunzong_watering_required_needs", ""))
        )
        self._scheduler: Scheduler | None = None
        self._send: SendFn | None = None
        self._state_store: SQLiteStateStore | None = None
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._linggen = ""
        self._linggen_refreshed_at: datetime | None = None
        self._watering_next_at: datetime | None = None
        self._harvest_suppress_until: datetime | None = None
        self._guard_suppress_until: datetime | None = None
        self._pending_action: str | None = None

    def set_state_store(self, store: SQLiteStateStore) -> None:
        self._state_store = store

    def restore_state(self) -> None:
        if self._state_store is None:
            return
        state = self._state_store.load_state(self._STATE_KEY)
        self._linggen = str(state.get("linggen", "") or "")
        self._linggen_refreshed_at = deserialize_datetime(state.get("linggen_refreshed_at"))
        self._watering_next_at = deserialize_datetime(state.get("watering_next_at"))
        self._harvest_suppress_until = deserialize_datetime(state.get("harvest_suppress_until"))
        self._guard_suppress_until = deserialize_datetime(state.get("guard_suppress_until"))

    async def bootstrap(self, scheduler: Scheduler, send: SendFn) -> None:
        if not self.enabled:
            return
        self._scheduler = scheduler
        self._send = send
        await self._schedule_status(self._status_delay_seconds())

    async def on_message(self, ctx: MessageContext) -> list[SendAction] | None:
        if not self.enabled:
            return None
        text = (ctx.text or "").strip()
        if not text:
            return None

        linggen = self._parse_linggen(text)
        if linggen:
            self._linggen = linggen
            self._linggen_refreshed_at = self._now()
            self._save_state()
            return None

        if self._is_tree_status(text):
            return await self._handle_status(text, ctx.message_id)

        if self._pending_action is not None or self._looks_like_action_feedback(text):
            await self._handle_action_feedback(text)
            return None

        return None

    async def _handle_status(self, text: str, message_id: int) -> list[SendAction] | None:
        status = self._parse_status(text)
        if status["harvested"]:
            self._mark_harvest_suppressed()
            await self._schedule_status(self._harvest_remaining_seconds())
            return None

        if status["under_attack"]:
            if self._is_guard_suppressed():
                await self._schedule_status(float(self._status_interval_seconds))
                return None
            self._guard_suppress_until = self._now() + timedelta(seconds=self._GUARD_SUPPRESS_SECONDS)
            self._pending_action = "guard"
            self._save_state()
            await self._schedule_status(float(self._status_interval_seconds))
            return [self._action(self._CMD_GUARD, message_id)]

        if status["mature"]:
            if self._is_harvest_suppressed():
                await self._schedule_status(self._harvest_remaining_seconds())
                return None
            self._pending_action = "harvest"
            self._mark_harvest_suppressed()
            await self._schedule_status(self._harvest_remaining_seconds())
            return [self._action(self._CMD_HARVEST, message_id)]

        if self._should_water(status["needs"]):
            self._pending_action = "watering"
            self._watering_next_at = self._now() + timedelta(seconds=self._watering_cooldown_seconds)
            self._save_state()
            await self._schedule_status(float(self._status_interval_seconds))
            return [self._action(self._CMD_WATER, message_id)]

        await self._schedule_status(float(self._status_interval_seconds))
        return None

    async def _handle_action_feedback(self, text: str) -> None:
        kind = self._pending_action
        if kind == "watering" or "灌溉" in text:
            remaining = self._parse_duration_seconds(text)
            if remaining is not None:
                self._watering_next_at = self._now() + timedelta(seconds=remaining)
                await self._schedule_status(float(remaining))
            else:
                self._watering_next_at = self._now() + timedelta(
                    seconds=self._watering_cooldown_seconds
                )
                await self._schedule_status(float(self._status_interval_seconds))
        elif kind == "guard" or "协同守山" in text or "守山" in text:
            self._guard_suppress_until = self._now() + timedelta(
                seconds=self._GUARD_SUPPRESS_SECONDS
            )
        elif kind == "harvest" or "采摘" in text or "奖励已入袋" in text:
            self._mark_harvest_suppressed()
            await self._schedule_status(self._harvest_remaining_seconds())
        self._pending_action = None
        self._save_state()

    async def _status_loop(self) -> None:
        if not self.enabled or self._send is None:
            return
        delay = self._status_delay_seconds()
        if delay > 0:
            await self._schedule_status(delay)
            return
        if self._linggen_needs_refresh():
            await self._send(self.name, self._CMD_LINGGEN, True)
        await self._send(self.name, self._CMD_STATUS, True)
        await self._schedule_status(float(self._status_interval_seconds))

    async def _schedule_status(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return

        async def _runner() -> None:
            await self._status_loop()

        await self._scheduler.schedule(
            key=self._STATUS_LOOP_KEY,
            delay_seconds=max(0.0, float(delay_seconds)),
            action=_runner,
        )

    def _action(self, text: str, source_message_id: int) -> SendAction:
        return SendAction(
            plugin=self.name,
            text=text,
            reply_to_topic=True,
            key=f"luoyunzong.{text}.{source_message_id}",
        )

    def _now(self) -> datetime:
        now = self._now_fn()
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now

    def _status_delay_seconds(self) -> float:
        return self._harvest_remaining_seconds()

    def _harvest_remaining_seconds(self) -> float:
        if self._harvest_suppress_until is None:
            return 0.0
        return max(0.0, (self._harvest_suppress_until - self._now()).total_seconds())

    def _is_harvest_suppressed(self) -> bool:
        return self._harvest_remaining_seconds() > 0

    def _is_guard_suppressed(self) -> bool:
        if self._guard_suppress_until is None:
            return False
        return (self._guard_suppress_until - self._now()).total_seconds() > 0

    def _linggen_needs_refresh(self) -> bool:
        if not self._linggen or self._linggen_refreshed_at is None:
            return True
        age_seconds = (self._now() - self._linggen_refreshed_at).total_seconds()
        return age_seconds >= self._linggen_refresh_seconds

    def _mark_harvest_suppressed(self) -> None:
        self._harvest_suppress_until = self._now() + timedelta(
            seconds=self._harvest_suppress_seconds
        )
        self._save_state()

    def _save_state(self) -> None:
        if self._state_store is None:
            return
        self._state_store.save_state(
            self._STATE_KEY,
            {
                "linggen": self._linggen,
                "linggen_refreshed_at": serialize_datetime(self._linggen_refreshed_at),
                "watering_next_at": serialize_datetime(self._watering_next_at),
                "harvest_suppress_until": serialize_datetime(self._harvest_suppress_until),
                "guard_suppress_until": serialize_datetime(self._guard_suppress_until),
            },
        )

    def _should_water(self, needs: list[str]) -> bool:
        if not needs:
            return False
        if self._watering_next_at is not None and self._watering_next_at > self._now():
            return False
        if self._watering_strategy == "always":
            return True
        if self._watering_strategy == "match_need":
            return any(need in self._watering_required_needs for need in needs)
        return bool(self._linggen) and any(need and need in self._linggen for need in needs)

    def _parse_status(self, text: str) -> dict[str, object]:
        return {
            "needs": self._parse_needs(text),
            "progress": self._parse_progress(text),
            "stage": self._parse_stage(text),
            "mature": self._is_mature_status(text),
            "harvested": self._is_harvested_status(text),
            "under_attack": self._is_attack_status(text),
        }

    def _is_tree_status(self, text: str) -> bool:
        return "落云宗" in text and "灵眼之树" in text

    def _parse_linggen(self, text: str) -> str:
        match = re.search(r"灵根[:：]\s*([^\r\n]+)", text)
        return match.group(1).strip() if match else ""

    def _parse_needs(self, text: str) -> list[str]:
        match = re.search(r"环境[:：][^\r\n]*[（(]\s*需?\s*([^）)\r\n]+)", text)
        if match is None:
            return []
        return self._split_tokens(match.group(1))

    def _parse_progress(self, text: str) -> float | None:
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
        return float(match.group(1)) if match else None

    def _parse_stage(self, text: str) -> tuple[int, int] | None:
        match = re.search(r"阶段[:：]\s*(\d+)\s*/\s*(\d+)", text)
        if match is None:
            return None
        return int(match.group(1)), int(match.group(2))

    def _is_mature_status(self, text: str) -> bool:
        return bool(re.search(r"状态[:：]\s*成熟采摘期", text))

    def _is_harvested_status(self, text: str) -> bool:
        return ("你的当前状态" in text and "已采摘" in text) or "奖励已入袋" in text

    def _is_attack_status(self, text: str) -> bool:
        return (
            ("警报" in text and "古剑门入侵中" in text)
            or "请速用 .协同守山" in text
        )

    def _looks_like_action_feedback(self, text: str) -> bool:
        return any(token in text for token in ("灵树灌溉", "协同守山", "采摘灵果", "奖励已入袋"))

    def _parse_duration_seconds(self, text: str) -> int | None:
        matched = False

        def _pick(pattern: str) -> int:
            nonlocal matched
            match = re.search(pattern, text)
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

    def _split_tokens(self, raw: str) -> list[str]:
        return [item for item in re.split(r"[/、，,\s]+", raw.strip()) if item]
