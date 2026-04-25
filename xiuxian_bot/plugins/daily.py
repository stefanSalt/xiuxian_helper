from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable

from ..config import Config
from ..core.contracts import MessageContext, SendAction
from ..core.scheduler import Scheduler
from ..core.state_store import SQLiteStateStore, coerce_int, deserialize_date, serialize_date
from ..domain.text_normalizer import normalize_match_text

SendFn = Callable[[str, str, bool], Awaitable[int | None]]


class DailyPlugin:
    """每日自动化：卜筮问天。"""

    name = "daily"
    priority = 10

    _CMD_BUSHI = ".卜筮问天"
    _BUSHI_LOOP_KEY = "daily.bushi.loop"
    _NEXT_DAY_KEY = "daily.next_day"
    _RARE_EVENT_ANCHORS = (
        normalize_match_text("神物现世"),
        normalize_match_text("昆吾通行令"),
        normalize_match_text("天道示警"),
        normalize_match_text("回复本消息"),
        normalize_match_text(".换取"),
    )

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self._config = config
        self._logger = logger
        self.enabled = bool(config.enable_daily)
        self._scheduler: Scheduler | None = None
        self._send: SendFn | None = None
        self._state_store: SQLiteStateStore | None = None
        self._current_day: date | None = None
        self._bushi_count_today = 0
        self._handled_rare_message_ids: set[int] = set()

    def set_state_store(self, state_store: SQLiteStateStore) -> None:
        self._state_store = state_store

    def restore_state(self) -> None:
        if self._state_store is None:
            return
        state = self._state_store.load_state(self.name)
        self._current_day = deserialize_date(state.get("current_day"))
        self._bushi_count_today = max(0, coerce_int(state.get("bushi_count_today")) or 0)
        raw_handled = state.get("handled_rare_message_ids")
        if isinstance(raw_handled, list):
            self._handled_rare_message_ids = {
                int(item) for item in raw_handled if isinstance(item, int | str) and str(item).isdigit()
            }

    def _save_state(self) -> None:
        if self._state_store is None:
            return
        self._state_store.save_state(
            self.name,
            {
                "current_day": serialize_date(self._current_day),
                "bushi_count_today": self._bushi_count_today,
                "handled_rare_message_ids": sorted(self._handled_rare_message_ids)[-50:],
            },
        )

    def _reset_if_new_day(self, now: datetime) -> None:
        if self._current_day == now.date():
            return
        self._current_day = now.date()
        self._bushi_count_today = 0
        self._handled_rare_message_ids = set()
        self._save_state()

    def _remaining_today(self) -> int:
        return max(0, self._config.daily_bushi_times_per_day - self._bushi_count_today)

    def _seconds_until_next_day(self, now: datetime) -> float:
        tomorrow = now.date() + timedelta(days=1)
        next_day = datetime(tomorrow.year, tomorrow.month, tomorrow.day)
        return max(0.0, (next_day - now).total_seconds())

    async def bootstrap(self, scheduler: Scheduler, send: SendFn) -> None:
        if not self.enabled:
            return
        self._scheduler = scheduler
        self._send = send
        now = datetime.now()
        self._reset_if_new_day(now)
        await self._schedule_next_day_reset(self._seconds_until_next_day(now))
        if self._remaining_today() > 0:
            await self._schedule_bushi_loop(0.0)

    async def _schedule_bushi_loop(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return

        async def _runner() -> None:
            await self._bushi_loop()

        await self._scheduler.schedule(
            key=self._BUSHI_LOOP_KEY,
            delay_seconds=max(0.0, delay_seconds),
            action=_runner,
        )

    async def _schedule_next_day_reset(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return

        async def _runner() -> None:
            await self._next_day_loop()

        await self._scheduler.schedule(
            key=self._NEXT_DAY_KEY,
            delay_seconds=max(0.0, delay_seconds),
            action=_runner,
        )

    async def _next_day_loop(self) -> None:
        now = datetime.now()
        self._reset_if_new_day(now)
        await self._schedule_next_day_reset(self._seconds_until_next_day(now))
        if self._remaining_today() > 0:
            await self._schedule_bushi_loop(0.0)

    async def _bushi_loop(self) -> None:
        if not self.enabled or self._send is None:
            return
        self._reset_if_new_day(datetime.now())
        if self._remaining_today() <= 0:
            return
        self._bushi_count_today += 1
        self._save_state()
        await self._send(self.name, self._CMD_BUSHI, True)
        if self._remaining_today() > 0:
            await self._schedule_bushi_loop(float(self._config.daily_bushi_interval_seconds))

    def _matches_rare_event(self, normalized_text: str) -> bool:
        return all(anchor in normalized_text for anchor in self._RARE_EVENT_ANCHORS)

    def _matches_identity(self, normalized_text: str) -> bool:
        identity = self._config.active_identity
        return any(token and token in normalized_text for token in identity.normalized_tokens())

    async def on_message(self, ctx: MessageContext) -> list[SendAction] | None:
        normalized_text = normalize_match_text(ctx.text)
        if not normalized_text or not self._matches_rare_event(normalized_text):
            return None
        if ctx.message_id in self._handled_rare_message_ids:
            return None
        if not (ctx.is_effective_reply or self._matches_identity(normalized_text)):
            return None
        self._handled_rare_message_ids.add(ctx.message_id)
        self._save_state()
        return [
            SendAction(
                plugin=self.name,
                text=self._config.daily_bushi_exchange_action,
                reply_to_topic=True,
                reply_to_msg_id=ctx.message_id,
                key=f"daily.bushi.exchange.{ctx.message_id}",
            )
        ]
