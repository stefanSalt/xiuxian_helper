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
    _LINGGEN_LOOP_KEY = "luoyunzong.linggen.loop"
    _WATERING_RETRY_KEY = "luoyunzong.watering.retry"
    _STATE_KEY = "luoyunzong"
    _GUARD_SUPPRESS_SECONDS = 300
    _HARVEST_STATUS_CHECK_SECONDS = 4 * 3600
    _WATERING_DIRECT_RETRY_WINDOW_SECONDS = 5 * 60
    _PENDING_ACTION_TTL_SECONDS = 5 * 60
    _STATUS_OWNER_MIN_TTL_SECONDS = 10 * 60
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
        self._identity_key = str(getattr(config, "active_identity_key", "main") or "main")
        account_id = str(getattr(config, "account_id", "") or "")
        self._status_owner_key = (
            f"{account_id}:{self._identity_key}" if account_id else self._identity_key
        )
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
        self._global_state_store: SQLiteStateStore | None = None
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._linggen = ""
        self._linggen_refreshed_at: datetime | None = None
        self._watering_next_at: datetime | None = None
        self._harvest_suppress_until: datetime | None = None
        self._harvest_suppress_source = ""
        self._guard_suppress_until: datetime | None = None
        self._pending_action: str | None = None
        self._pending_action_started_at: datetime | None = None
        self._status_owner: str | None = None
        self._status_owner_until: datetime | None = None
        self._status_owner_next_at: datetime | None = None
        self._last_status_seen_at: datetime | None = None
        self._last_status_needs: list[str] = []
        self._last_status_mature = False
        self._last_status_under_attack = False

    def set_state_store(self, store: SQLiteStateStore) -> None:
        self._state_store = store

    def set_global_state_store(self, store: SQLiteStateStore) -> None:
        self._global_state_store = store

    def restore_state(self) -> None:
        if self._state_store is not None:
            state = self._state_store.load_state(self._STATE_KEY)
            self._linggen = str(state.get("linggen", "") or "")
            self._linggen_refreshed_at = deserialize_datetime(state.get("linggen_refreshed_at"))
            self._watering_next_at = deserialize_datetime(state.get("watering_next_at"))
            self._harvest_suppress_until = deserialize_datetime(state.get("harvest_suppress_until"))
            self._harvest_suppress_source = str(state.get("harvest_suppress_source", "") or "")
            self._guard_suppress_until = deserialize_datetime(state.get("guard_suppress_until"))
        self._load_global_state()

    async def bootstrap(self, scheduler: Scheduler, send: SendFn) -> None:
        if not self.enabled:
            return
        self._scheduler = scheduler
        self._send = send
        await self._schedule_linggen(self._linggen_delay_seconds())
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
            await self._schedule_linggen(float(self._linggen_refresh_seconds))
            self._logger.info(
                "luoyunzong_linggen_updated identity=%s linggen=%s",
                self._identity_key,
                self._linggen,
            )
            return None

        if self._is_tree_status(text):
            return await self._handle_status(text, ctx.message_id, personal=True)

        if self._looks_like_action_feedback(text):
            await self._handle_action_feedback(text)
            return None

        return None

    async def on_global_status(self, ctx: MessageContext) -> list[SendAction] | None:
        if not self.enabled:
            return None
        text = (ctx.text or "").strip()
        if not text:
            return None
        if self._is_public_guard_started(text):
            return await self._handle_public_guard_started(ctx.message_id)
        if self._is_public_guard_finished(text):
            await self._handle_public_guard_finished()
            return None
        if not self._is_tree_status(text):
            return None
        return await self._handle_status(text, ctx.message_id, personal=False)

    async def _handle_public_guard_started(self, message_id: int) -> list[SendAction] | None:
        status = self._public_status(under_attack=True)
        self._remember_global_status(status)
        if self._pending_action is not None and not self._expire_pending_action():
            await self._schedule_status(float(self._status_interval_seconds))
            self._log_status_decision(status, f"pending_{self._pending_action}", "skip")
            return None
        if self._is_guard_suppressed():
            await self._schedule_status(self._guard_suppress_remaining_seconds())
            self._log_status_decision(status, "guard_suppressed", "skip")
            return None
        self._guard_suppress_until = self._now() + timedelta(seconds=self._GUARD_SUPPRESS_SECONDS)
        self._set_pending_action("guard")
        self._save_state()
        await self._schedule_status(float(self._GUARD_SUPPRESS_SECONDS))
        self._log_status_decision(status, "public_under_attack", self._CMD_GUARD)
        return [self._action(self._CMD_GUARD, message_id)]

    async def _handle_public_guard_finished(self) -> None:
        status = self._public_status(under_attack=False)
        self._remember_global_status(status)
        if self._pending_action == "guard":
            self._clear_pending_action()
            self._save_state()
        await self._schedule_status(0.0)
        self._log_status_decision(status, "public_guard_finished", "skip")

    async def _handle_status(
        self,
        text: str,
        message_id: int,
        *,
        personal: bool,
    ) -> list[SendAction] | None:
        status = self._parse_status(text)
        self._remember_global_status(status)
        if personal and status["harvested"]:
            remaining_seconds = status["remaining_seconds"]
            self._mark_harvest_suppressed(
                remaining_seconds=remaining_seconds
                if isinstance(remaining_seconds, int)
                else None,
                refresh_default=False,
                source="status",
            )
            self._clear_pending_action()
            await self._schedule_status(self._next_status_delay_seconds())
            self._log_status_decision(status, "already_harvested", "suppress")
            return None

        if not status["mature"] or (personal and self._harvest_suppress_source != "feedback"):
            self._clear_harvest_suppression()

        if self._pending_action is not None:
            if not self._expire_pending_action():
                await self._schedule_status(float(self._status_interval_seconds))
                self._log_status_decision(status, f"pending_{self._pending_action}", "skip")
                return None

        if status["under_attack"]:
            if self._is_guard_suppressed():
                await self._schedule_status(self._guard_suppress_remaining_seconds())
                self._log_status_decision(status, "guard_suppressed", "skip")
                return None
            self._guard_suppress_until = self._now() + timedelta(seconds=self._GUARD_SUPPRESS_SECONDS)
            self._set_pending_action("guard")
            self._save_state()
            await self._schedule_status(float(self._status_interval_seconds))
            self._log_status_decision(status, "under_attack", self._CMD_GUARD)
            return [self._action(self._CMD_GUARD, message_id)]

        if status["mature"]:
            if self._is_harvest_suppressed():
                await self._schedule_status(self._next_status_delay_seconds())
                self._log_status_decision(status, "harvest_suppressed", "skip")
                return None
            self._set_pending_action("harvest")
            await self._schedule_status(float(self._status_interval_seconds))
            self._log_status_decision(status, "mature", self._CMD_HARVEST)
            return [self._action(self._CMD_HARVEST, message_id)]

        should_water, water_reason = self._watering_decision(status["needs"])
        if should_water:
            self._set_pending_action("watering")
            await self._schedule_status(float(self._status_interval_seconds))
            self._log_status_decision(status, water_reason, self._CMD_WATER)
            return [self._action(self._CMD_WATER, message_id)]

        direct_retry_scheduled = (
            await self._schedule_watering_retry_after_short_cooldown(status["needs"])
        )
        if not direct_retry_scheduled:
            await self._schedule_status(self._status_delay_after_decision(water_reason))
        self._log_status_decision(status, water_reason, "skip")
        return None

    async def _handle_action_feedback(self, text: str) -> None:
        kind = self._feedback_kind(text)
        remaining: int | None = None
        if kind == "watering_cooldown":
            remaining = self._parse_duration_seconds(text)
            if remaining is None:
                return
            self._watering_next_at = self._now() + timedelta(seconds=remaining)
            await self._schedule_status(float(remaining))
        elif kind == "watering_success":
            self._watering_next_at = self._now() + timedelta(
                seconds=self._watering_cooldown_seconds
            )
            await self._schedule_status(float(self._status_interval_seconds))
        elif kind == "watering_unneeded":
            await self._schedule_status(0.0)
        elif kind == "guard_cooldown":
            remaining = self._parse_duration_seconds(text)
            if remaining is None:
                return
            self._guard_suppress_until = self._now() + timedelta(seconds=remaining)
            await self._schedule_status(float(remaining))
        elif kind == "guard":
            self._guard_suppress_until = self._now() + timedelta(
                seconds=self._GUARD_SUPPRESS_SECONDS
            )
            await self._schedule_status(float(self._GUARD_SUPPRESS_SECONDS))
        elif kind == "harvest":
            self._mark_harvest_suppressed()
            await self._schedule_status(self._next_status_delay_seconds())
        else:
            return
        self._clear_pending_action()
        self._save_state()
        self._logger.info(
            "luoyunzong_feedback identity=%s kind=%s remaining_seconds=%s watering_next_at=%s harvest_suppress_until=%s guard_suppress_until=%s",
            self._identity_key,
            kind,
            remaining if remaining is not None else "-",
            self._format_datetime(self._watering_next_at),
            self._format_datetime(self._harvest_suppress_until),
            self._format_datetime(self._guard_suppress_until),
        )

    async def _status_loop(self) -> None:
        if not self.enabled or self._send is None:
            return
        if not self._claim_status_owner(self._next_status_delay_seconds()):
            return
        await self._send(self.name, self._CMD_STATUS, True)
        await self._schedule_status(self._next_status_delay_seconds())

    async def _schedule_status(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return
        if not self._claim_status_owner(delay_seconds):
            self._logger.debug(
                "luoyunzong_status_schedule_skipped identity=%s owner=%s owner_until=%s",
                self._identity_key,
                self._status_owner or "-",
                self._format_datetime(self._status_owner_until),
            )
            return

        async def _runner() -> None:
            await self._status_loop()

        await self._scheduler.schedule(
            key=self._STATUS_LOOP_KEY,
            delay_seconds=max(0.0, float(delay_seconds)),
            action=_runner,
        )

    async def _linggen_loop(self) -> None:
        if not self.enabled or self._send is None:
            return
        await self._send(self.name, self._CMD_LINGGEN, True)
        await self._schedule_linggen(float(self._linggen_refresh_seconds))

    async def _schedule_linggen(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return

        async def _runner() -> None:
            await self._linggen_loop()

        await self._scheduler.schedule(
            key=self._LINGGEN_LOOP_KEY,
            delay_seconds=max(0.0, float(delay_seconds)),
            action=_runner,
        )

    async def _schedule_watering_retry_after_short_cooldown(
        self, needs: object
    ) -> bool:
        if not isinstance(needs, list):
            return False
        remaining = self._watering_cooldown_remaining_seconds()
        if (
            remaining <= 0
            or remaining > float(self._WATERING_DIRECT_RETRY_WINDOW_SECONDS)
        ):
            return False
        if not self._would_water_without_cooldown([str(need) for need in needs]):
            return False
        await self._schedule_watering_retry(remaining)
        return True

    async def _schedule_watering_retry(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return

        async def _runner() -> None:
            await self._watering_retry_loop()

        await self._scheduler.schedule(
            key=self._WATERING_RETRY_KEY,
            delay_seconds=max(0.0, float(delay_seconds)),
            action=_runner,
        )

    async def _watering_retry_loop(self) -> None:
        if not self.enabled or self._send is None:
            return
        remaining = self._watering_cooldown_remaining_seconds()
        if remaining > 1:
            if remaining <= float(self._WATERING_DIRECT_RETRY_WINDOW_SECONDS):
                await self._schedule_watering_retry(remaining)
            else:
                await self._schedule_status(
                    self._status_delay_after_decision("watering_cooldown")
                )
            return
        if self._pending_action is not None and not self._expire_pending_action():
            await self._schedule_status(float(self._status_interval_seconds))
            return
        if not self._last_status_is_recently_safe_to_water():
            await self._schedule_status(0.0)
            return
        self._set_pending_action("watering")
        self._save_state()
        await self._send(self.name, self._CMD_WATER, True)
        await self._schedule_status(float(self._status_interval_seconds))

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

    def _linggen_delay_seconds(self) -> float:
        if not self._linggen or self._linggen_refreshed_at is None:
            return 0.0
        age_seconds = (self._now() - self._linggen_refreshed_at).total_seconds()
        return max(0.0, float(self._linggen_refresh_seconds) - age_seconds)

    def _status_delay_seconds(self) -> float:
        remaining = self._harvest_remaining_seconds()
        if remaining <= 0:
            return 0.0
        return min(remaining, float(self._HARVEST_STATUS_CHECK_SECONDS))

    def _next_status_delay_seconds(self) -> float:
        delay = self._status_delay_seconds()
        if delay > 0:
            return delay
        return float(self._status_interval_seconds)

    def _status_delay_after_decision(self, reason: str) -> float:
        if reason == "watering_cooldown":
            remaining = self._watering_cooldown_remaining_seconds()
            if remaining > 0:
                return min(remaining, float(self._status_interval_seconds))
        return float(self._status_interval_seconds)

    def _harvest_remaining_seconds(self) -> float:
        if self._harvest_suppress_until is None:
            return 0.0
        return max(0.0, (self._harvest_suppress_until - self._now()).total_seconds())

    def _watering_cooldown_remaining_seconds(self) -> float:
        if self._watering_next_at is None:
            return 0.0
        return max(0.0, (self._watering_next_at - self._now()).total_seconds())

    def _is_harvest_suppressed(self) -> bool:
        return self._harvest_remaining_seconds() > 0

    def _is_guard_suppressed(self) -> bool:
        return self._guard_suppress_remaining_seconds() > 0

    def _guard_suppress_remaining_seconds(self) -> float:
        if self._guard_suppress_until is None:
            return 0.0
        return max(0.0, (self._guard_suppress_until - self._now()).total_seconds())

    def _status_owner_ttl_seconds(self, delay_seconds: float) -> float:
        return max(
            float(self._STATUS_OWNER_MIN_TTL_SECONDS),
            float(delay_seconds) + float(self._status_interval_seconds),
        )

    def _claim_status_owner(self, delay_seconds: float) -> bool:
        if self._global_state_store is None:
            return True
        self._load_global_state()
        now = self._now()
        requested_next_at = now + timedelta(seconds=max(0.0, float(delay_seconds)))
        if (
            self._status_owner
            and self._status_owner != self._status_owner_key
            and self._status_owner_until is not None
            and self._status_owner_until > now
            and not self._can_preempt_status_owner(requested_next_at, now)
        ):
            return False
        self._status_owner = self._status_owner_key
        self._status_owner_next_at = requested_next_at
        self._status_owner_until = now + timedelta(
            seconds=self._status_owner_ttl_seconds(delay_seconds)
        )
        self._save_global_state()
        return True

    def _can_preempt_status_owner(self, requested_next_at: datetime, now: datetime) -> bool:
        if self._status_owner_next_at is None:
            return False
        if self._status_owner_next_at < now - timedelta(seconds=1):
            return True
        return requested_next_at < self._status_owner_next_at - timedelta(seconds=1)

    def _set_pending_action(self, action: str) -> None:
        self._pending_action = action
        self._pending_action_started_at = self._now()

    def _clear_pending_action(self) -> None:
        self._pending_action = None
        self._pending_action_started_at = None

    def _expire_pending_action(self) -> bool:
        if self._pending_action is None:
            return False
        if self._pending_action_started_at is None:
            self._pending_action_started_at = self._now()
            return False
        age = (self._now() - self._pending_action_started_at).total_seconds()
        if age < self._PENDING_ACTION_TTL_SECONDS:
            return False
        expired_action = self._pending_action
        self._clear_pending_action()
        self._logger.warning(
            "luoyunzong_pending_expired identity=%s action=%s age_seconds=%.1f",
            self._identity_key,
            expired_action,
            age,
        )
        return True

    def _linggen_needs_refresh(self) -> bool:
        if not self._linggen or self._linggen_refreshed_at is None:
            return True
        age_seconds = (self._now() - self._linggen_refreshed_at).total_seconds()
        return age_seconds >= self._linggen_refresh_seconds

    def _mark_harvest_suppressed(
        self,
        remaining_seconds: int | None = None,
        *,
        refresh_default: bool = True,
        source: str = "feedback",
    ) -> None:
        now = self._now()
        updated = False
        if remaining_seconds is not None:
            self._harvest_suppress_until = now + timedelta(seconds=max(0, remaining_seconds))
            updated = True
        elif refresh_default or not self._is_harvest_suppressed():
            self._harvest_suppress_until = now + timedelta(
                seconds=self._harvest_suppress_seconds
            )
            updated = True
        if updated:
            self._harvest_suppress_source = source
        self._save_state()

    def _clear_harvest_suppression(self) -> None:
        if self._harvest_suppress_until is None:
            return
        self._harvest_suppress_until = None
        self._harvest_suppress_source = ""
        self._save_state()

    def _remember_global_status(self, status: dict[str, object]) -> None:
        if self._global_state_store is not None:
            self._load_global_state()
        needs = status["needs"] if isinstance(status["needs"], list) else []
        self._last_status_seen_at = self._now()
        self._last_status_needs = [str(item) for item in needs]
        self._last_status_mature = bool(status["mature"])
        self._last_status_under_attack = bool(status["under_attack"])
        self._save_global_state()

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
                "harvest_suppress_source": self._harvest_suppress_source,
                "guard_suppress_until": serialize_datetime(self._guard_suppress_until),
            },
        )

    def _load_global_state(self) -> None:
        if self._global_state_store is None:
            return
        state = self._global_state_store.load_state(self._STATE_KEY)
        self._status_owner = str(state.get("status_owner", "") or "") or None
        self._status_owner_until = deserialize_datetime(state.get("status_owner_until"))
        self._status_owner_next_at = deserialize_datetime(state.get("status_owner_next_at"))
        self._last_status_seen_at = deserialize_datetime(state.get("last_status_seen_at"))
        raw_needs = state.get("last_status_needs", [])
        if isinstance(raw_needs, list):
            self._last_status_needs = [str(item) for item in raw_needs if str(item)]
        else:
            self._last_status_needs = []
        self._last_status_mature = bool(state.get("last_status_mature", False))
        self._last_status_under_attack = bool(state.get("last_status_under_attack", False))

    def _save_global_state(self) -> None:
        if self._global_state_store is None:
            return
        self._global_state_store.save_state(
            self._STATE_KEY,
            {
                "status_owner": self._status_owner,
                "status_owner_until": serialize_datetime(self._status_owner_until),
                "status_owner_next_at": serialize_datetime(self._status_owner_next_at),
                "last_status_seen_at": serialize_datetime(self._last_status_seen_at),
                "last_status_needs": self._last_status_needs,
                "last_status_mature": self._last_status_mature,
                "last_status_under_attack": self._last_status_under_attack,
            },
        )

    def _should_water(self, needs: list[str]) -> bool:
        return self._watering_decision(needs)[0]

    def _watering_decision(self, needs: list[str]) -> tuple[bool, str]:
        if not needs:
            return False, "no_needs"
        if self._watering_next_at is not None and self._watering_next_at > self._now():
            return False, "watering_cooldown"
        return self._watering_strategy_decision(needs)

    def _would_water_without_cooldown(self, needs: list[str]) -> bool:
        return self._watering_strategy_decision(needs)[0]

    def _watering_strategy_decision(self, needs: list[str]) -> tuple[bool, str]:
        if self._watering_strategy == "always":
            return True, "always"
        if self._watering_strategy == "match_need":
            matched = any(need in self._watering_required_needs for need in needs)
            return matched, "match_need" if matched else "need_mismatch"
        if not self._linggen:
            return False, "linggen_missing"
        matched = any(need and need in self._linggen for need in needs)
        return matched, "linggen_match" if matched else "linggen_mismatch"

    def _last_status_is_recently_safe_to_water(self) -> bool:
        if self._last_status_seen_at is None:
            return False
        age_seconds = (self._now() - self._last_status_seen_at).total_seconds()
        return (
            0 <= age_seconds <= float(self._WATERING_DIRECT_RETRY_WINDOW_SECONDS)
            and not self._last_status_mature
            and not self._last_status_under_attack
            and bool(self._last_status_needs)
        )

    def _log_status_decision(
        self,
        status: dict[str, object],
        reason: str,
        action: str,
    ) -> None:
        needs = ",".join(status["needs"]) if isinstance(status["needs"], list) else "-"
        self._logger.info(
            "luoyunzong_decision identity=%s action=%s reason=%s strategy=%s "
            "needs=%s linggen=%s watering_next_at=%s harvest_suppress_until=%s "
            "guard_suppress_until=%s mature=%s harvested=%s under_attack=%s progress=%s stage=%s",
            self._identity_key,
            action,
            reason,
            self._watering_strategy,
            needs or "-",
            self._linggen or "-",
            self._format_datetime(self._watering_next_at),
            self._format_datetime(self._harvest_suppress_until),
            self._format_datetime(self._guard_suppress_until),
            status["mature"],
            status["harvested"],
            status["under_attack"],
            status["progress"],
            status["stage"],
        )

    def _format_datetime(self, value: datetime | None) -> str:
        return serialize_datetime(value) or "-"

    def _parse_status(self, text: str) -> dict[str, object]:
        return {
            "needs": self._parse_needs(text),
            "progress": self._parse_progress(text),
            "stage": self._parse_stage(text),
            "mature": self._is_mature_status(text),
            "harvested": self._is_harvested_status(text),
            "under_attack": self._is_attack_status(text),
            "remaining_seconds": self._parse_remaining_seconds(text),
        }

    def _public_status(self, *, under_attack: bool) -> dict[str, object]:
        return {
            "needs": [],
            "progress": None,
            "stage": None,
            "mature": False,
            "harvested": False,
            "under_attack": under_attack,
            "remaining_seconds": None,
        }

    def _is_tree_status(self, text: str) -> bool:
        return "落云宗" in text and "灵眼之树" in text

    def _is_public_guard_started(self, text: str) -> bool:
        return (
            "古剑门来袭" in text
            and "护山大阵" in text
            and ".协同守山" in text
        )

    def _is_public_guard_finished(self, text: str) -> bool:
        return (
            "守护成功" in text
            and "古剑门" in text
            and "成功击退" in text
        )

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

    def _parse_remaining_seconds(self, text: str) -> int | None:
        match = re.search(r"剩余[:：]\s*([0-9 \t天小时分钟分秒]+)", text)
        if match is None:
            return None
        return self._parse_duration_seconds(match.group(1))

    def _is_attack_status(self, text: str) -> bool:
        return (
            ("警报" in text and "古剑门入侵中" in text)
            or "请速用 .协同守山" in text
        )

    def _feedback_kind(self, text: str) -> str | None:
        if self._is_watering_unneeded_feedback(text):
            return "watering_unneeded"
        if self._is_watering_cooldown_feedback(text):
            return "watering_cooldown"
        if self._is_watering_success_feedback(text):
            return "watering_success"
        if self._is_guard_cooldown_feedback(text):
            return "guard_cooldown"
        if self._is_guard_success_feedback(text):
            return "guard"
        if "采摘灵果" in text or "采摘" in text or "奖励已入袋" in text:
            return "harvest"
        return None

    def _is_watering_success_feedback(self, text: str) -> bool:
        return "灵树灌溉" in text and "成熟度" in text and "->" in text

    def _is_watering_cooldown_feedback(self, text: str) -> bool:
        return "灌溉" in text and "请在" in text and "后再来" in text

    def _is_watering_unneeded_feedback(self, text: str) -> bool:
        return "灵眼之树已然成熟或正遭劫难" in text and "无需灌溉" in text

    def _is_guard_cooldown_feedback(self, text: str) -> bool:
        return "守山" in text and "请在" in text and "后再来守山" in text

    def _is_guard_success_feedback(self, text: str) -> bool:
        return (
            "守山成功" in text
            and "护山大阵" in text
            and ("大阵修复" in text or "宗门贡献" in text or "注入" in text)
        )

    def _looks_like_action_feedback(self, text: str) -> bool:
        return any(
            token in text
            for token in (
                "灵树灌溉",
                "地脉灵气尚未恢复",
                "后再来灌溉",
                "无需灌溉",
                "守山成功",
                "后再来守山",
                "采摘灵果",
                "已经采摘过灵果",
                "不可贪得无厌",
                "奖励已入袋",
            )
        )

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
