from __future__ import annotations

from collections import deque
import time


class SlidingWindowRateLimiter:
    def __init__(self, max_events: int, window_seconds: int) -> None:
        self._max_events = max_events
        self._window_seconds = window_seconds
        self._events: deque[float] = deque()

    def allow(self) -> bool:
        now = time.monotonic()
        cutoff = now - self._window_seconds
        while self._events and self._events[0] <= cutoff:
            self._events.popleft()
        if len(self._events) >= self._max_events:
            return False
        self._events.append(now)
        return True


class RateLimiter:
    def __init__(self, *, global_per_minute: int, plugin_per_minute: int) -> None:
        self._global = SlidingWindowRateLimiter(global_per_minute, 60)
        self._plugin_per_minute = plugin_per_minute
        self._per_plugin: dict[str, SlidingWindowRateLimiter] = {}

    def allow(self, plugin: str) -> bool:
        if not self._global.allow():
            return False
        limiter = self._per_plugin.get(plugin)
        if limiter is None:
            limiter = SlidingWindowRateLimiter(self._plugin_per_minute, 60)
            self._per_plugin[plugin] = limiter
        return limiter.allow()

