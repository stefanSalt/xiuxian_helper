from __future__ import annotations

from collections import deque
import time


class SlidingWindowRateLimiter:
    def __init__(self, max_events: int, window_seconds: int) -> None:
        if max_events < 1:
            raise ValueError(f"max_events must be >= 1, got: {max_events}")
        if window_seconds < 1:
            raise ValueError(f"window_seconds must be >= 1, got: {window_seconds}")
        self._max_events = max_events
        self._window_seconds = window_seconds
        self._events: deque[float] = deque()

    def _prune(self, now: float) -> None:
        cutoff = now - self._window_seconds
        while self._events and self._events[0] <= cutoff:
            self._events.popleft()

    def can_allow_at(self, now: float) -> bool:
        self._prune(now)
        return len(self._events) < self._max_events

    def reserve_at(self, now: float) -> None:
        self._events.append(now)

    def allow(self) -> bool:
        now = time.monotonic()
        if not self.can_allow_at(now):
            return False
        self.reserve_at(now)
        return True

    def next_allowed_in_at(self, now: float) -> float:
        self._prune(now)
        if len(self._events) < self._max_events:
            return 0.0
        # Wait until the oldest event falls out of the window.
        return max(0.0, (self._events[0] + self._window_seconds) - now)

    def next_allowed_in(self) -> float:
        return self.next_allowed_in_at(time.monotonic())


class RateLimiter:
    def __init__(self, *, global_per_minute: int, plugin_per_minute: int) -> None:
        self._global = SlidingWindowRateLimiter(global_per_minute, 60)
        self._plugin_per_minute = plugin_per_minute
        self._per_plugin: dict[str, SlidingWindowRateLimiter] = {}

    def _limiter_for(self, plugin: str) -> SlidingWindowRateLimiter:
        limiter = self._per_plugin.get(plugin)
        if limiter is None:
            limiter = SlidingWindowRateLimiter(self._plugin_per_minute, 60)
            self._per_plugin[plugin] = limiter
        return limiter

    def allow(self, plugin: str) -> bool:
        # Atomic: only record the global event when the plugin bucket can also accept it.
        now = time.monotonic()
        limiter = self._limiter_for(plugin)
        if not self._global.can_allow_at(now) or not limiter.can_allow_at(now):
            return False
        self._global.reserve_at(now)
        limiter.reserve_at(now)
        return True

    def next_allowed_in(self, plugin: str) -> float:
        now = time.monotonic()
        limiter = self._limiter_for(plugin)
        return max(self._global.next_allowed_in_at(now), limiter.next_allowed_in_at(now))
