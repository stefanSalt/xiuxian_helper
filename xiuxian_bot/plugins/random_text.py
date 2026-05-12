from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

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

SendFn = Callable[[str, str, bool], Awaitable[int | None]]
NowFn = Callable[[], datetime]


class RandomTextPlugin:
    """身份内随机文本：只在正常插件命令发送后顺势追加。"""

    name = "random_text"
    priority = 10

    _STATE_KEY = "random_text"

    def __init__(
        self,
        config: Config,
        logger: logging.Logger,
        *,
        now_fn: NowFn | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._logger = logger
        self.enabled = bool(config.enable_random_text)
        self._identity_key = str(getattr(config, "active_identity_key", "main") or "main")
        self._messages = self._parse_messages(config.random_text_messages)
        self._min_interval_seconds = max(60, int(config.random_text_min_interval_seconds))
        self._max_interval_seconds = max(
            self._min_interval_seconds,
            int(config.random_text_max_interval_seconds),
        )
        self._daily_limit = max(0, int(config.random_text_daily_limit))
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._rng = rng or random.Random()
        self._state_store: SQLiteStateStore | None = None
        self._sent_on = None
        self._sent_today = 0
        self._next_allowed_at: datetime | None = None

    def set_state_store(self, store: SQLiteStateStore) -> None:
        self._state_store = store

    def restore_state(self) -> None:
        if self._state_store is None:
            return
        state = self._state_store.load_state(self._STATE_KEY)
        self._sent_on = deserialize_date(state.get("sent_on"))
        self._sent_today = max(0, coerce_int(state.get("sent_today")) or 0)
        self._next_allowed_at = self._normalize_datetime(
            deserialize_datetime(state.get("next_allowed_at"))
        )

    async def bootstrap(self, scheduler: Scheduler, send: SendFn) -> None:
        _ = (scheduler, send)
        return None

    async def on_message(self, ctx: MessageContext) -> list[SendAction] | None:
        _ = ctx
        return None

    def next_message(self) -> str | None:
        if not self.enabled or not self._messages or self._daily_limit <= 0:
            return None
        now = self._now()
        self._reset_if_new_day(now)
        if self._sent_today >= self._daily_limit:
            self._save_state()
            return None
        if self._next_allowed_at is None:
            self._next_allowed_at = now + timedelta(seconds=self._random_delay_seconds())
            self._save_state()
            return None
        if self._next_allowed_at > now:
            return None
        return self._rng.choice(self._messages)

    def mark_sent(self) -> None:
        now = self._now()
        self._reset_if_new_day(now)
        self._sent_on = now.date()
        self._sent_today += 1
        self._next_allowed_at = now + timedelta(seconds=self._random_delay_seconds())
        self._save_state()
        self._logger.info(
            "random_text_sent identity=%s sent_today=%s next_allowed_at=%s",
            self._identity_key,
            self._sent_today,
            serialize_datetime(self._next_allowed_at) or "-",
        )

    def _now(self) -> datetime:
        now = self._now_fn()
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now

    def _reset_if_new_day(self, now: datetime) -> None:
        today = now.date()
        if self._sent_on != today:
            self._sent_on = today
            self._sent_today = 0

    def _random_delay_seconds(self) -> float:
        return self._rng.uniform(
            float(self._min_interval_seconds),
            float(self._max_interval_seconds),
        )

    def _save_state(self) -> None:
        if self._state_store is None:
            return
        self._state_store.save_state(
            self._STATE_KEY,
            {
                "sent_on": serialize_date(self._sent_on),
                "sent_today": self._sent_today,
                "next_allowed_at": serialize_datetime(self._next_allowed_at),
            },
        )

    def _normalize_datetime(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    def _parse_messages(self, raw: Any) -> list[str]:
        return [line.strip() for line in str(raw or "").splitlines() if line.strip()]
