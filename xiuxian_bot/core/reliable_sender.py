from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from .rate_limit import RateLimiter

SendMessageFn = Callable[..., Awaitable[int | None]]
SleepFn = Callable[[float], Awaitable[None]]
MonotonicFn = Callable[[], float]


class ReliableSender:
    def __init__(
        self,
        *,
        send_message: SendMessageFn,
        limiter: RateLimiter,
        logger: logging.Logger,
        dry_run: bool,
        min_interval_seconds: float,
        sleep_fn: SleepFn = asyncio.sleep,
        monotonic_fn: MonotonicFn = time.monotonic,
    ) -> None:
        self._send_message = send_message
        self._limiter = limiter
        self._logger = logger
        self._dry_run = dry_run
        self._min_interval_seconds = max(0.0, float(min_interval_seconds))
        self._sleep = sleep_fn
        self._monotonic = monotonic_fn
        self._lock = asyncio.Lock()
        self._last_attempt_at: float | None = None

    def _min_interval_wait_seconds(self) -> float:
        if self._last_attempt_at is None:
            return 0.0
        elapsed = self._monotonic() - self._last_attempt_at
        return max(0.0, self._min_interval_seconds - elapsed)

    def _is_wait_error(self, exc: Exception) -> bool:
        seconds = getattr(exc, "seconds", None)
        return isinstance(seconds, (int, float)) and float(seconds) > 0

    def _retry_wait_seconds(self, exc: Exception) -> float:
        seconds = getattr(exc, "seconds", None)
        if isinstance(seconds, (int, float)) and float(seconds) > 0:
            return max(self._min_interval_seconds, float(seconds) + 1.0)
        return max(self._min_interval_seconds, 5.0)

    async def send(
        self,
        plugin: str,
        text: str,
        reply_to_topic: bool,
        *,
        reply_to_msg_id: int | None = None,
        identity_key: str | None = None,
    ) -> int | None:
        identity_prefix = f"[{identity_key}] " if identity_key else ""
        if self._dry_run:
            if reply_to_msg_id is None:
                self._logger.info(">> %s%s (dry-run)", identity_prefix, text)
            else:
                self._logger.info(">> %s%s (reply_to=%s, dry-run)", identity_prefix, text, reply_to_msg_id)
            return None

        async with self._lock:
            rate_limit_retries = 0
            send_retries = 0
            while True:
                interval_wait = self._min_interval_wait_seconds()
                if interval_wait > 0:
                    self._logger.debug(
                        "send_spacing_wait plugin=%s wait_seconds=%.1f text=%s",
                        plugin,
                        interval_wait,
                        text,
                    )
                    await self._sleep(interval_wait)
                    continue

                if not self._limiter.allow(plugin):
                    wait_seconds = max(0.5, self._limiter.next_allowed_in(plugin) + 0.5)
                    rate_limit_retries += 1
                    self._logger.warning(
                        "rate_limited plugin=%s retry=%s wait_seconds=%.1f text=%s",
                        plugin,
                        rate_limit_retries,
                        wait_seconds,
                        text,
                    )
                    await self._sleep(wait_seconds)
                    continue

                self._last_attempt_at = self._monotonic()
                try:
                    mid = await self._send_message(
                        text,
                        reply_to_topic=reply_to_topic,
                        reply_to_msg_id=reply_to_msg_id,
                    )
                except Exception as exc:
                    send_retries += 1
                    wait_seconds = self._retry_wait_seconds(exc)
                    if self._is_wait_error(exc):
                        self._logger.warning(
                            "send_wait_required plugin=%s retry=%s wait_seconds=%.1f error=%s text=%s",
                            plugin,
                            send_retries,
                            wait_seconds,
                            type(exc).__name__,
                            text,
                        )
                    else:
                        self._logger.exception(
                            "send_failed_retry plugin=%s identity=%s retry=%s wait_seconds=%.1f text=%s reply_to_topic=%s reply_to_msg_id=%s",
                            plugin,
                            identity_key,
                            send_retries,
                            wait_seconds,
                            text,
                            reply_to_topic,
                            reply_to_msg_id,
                        )
                    await self._sleep(wait_seconds)
                    continue

                if reply_to_msg_id is None:
                    self._logger.info(">> %s%s", identity_prefix, text)
                else:
                    self._logger.info(">> %s%s (reply_to=%s)", identity_prefix, text, reply_to_msg_id)
                return mid
