import time
import unittest

from xiuxian_bot.core.rate_limit import RateLimiter, SlidingWindowRateLimiter


class TestRateLimiter(unittest.TestCase):
    def test_allow_is_atomic(self) -> None:
        limiter = RateLimiter(global_per_minute=2, plugin_per_minute=1)
        self.assertTrue(limiter.allow("a"))
        # Plugin bucket is full; the failed attempt must not consume the global bucket.
        self.assertFalse(limiter.allow("a"))
        self.assertTrue(limiter.allow("b"))

    def test_sliding_window_next_allowed_in(self) -> None:
        lim = SlidingWindowRateLimiter(max_events=1, window_seconds=1)
        self.assertTrue(lim.allow())
        self.assertFalse(lim.allow())
        wait = lim.next_allowed_in()
        self.assertGreater(wait, 0.0)
        time.sleep(wait + 0.05)
        self.assertTrue(lim.allow())

    def test_invalid_limits_raise(self) -> None:
        with self.assertRaises(ValueError):
            SlidingWindowRateLimiter(max_events=0, window_seconds=60)


if __name__ == "__main__":
    unittest.main()

