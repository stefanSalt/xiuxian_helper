import logging
import unittest

from xiuxian_bot.core.rate_limit import RateLimiter
from xiuxian_bot.core.reliable_sender import ReliableSender


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


class _WaitError(Exception):
    def __init__(self, seconds: int) -> None:
        super().__init__(f"wait {seconds}")
        self.seconds = seconds


def _test_logger() -> logging.Logger:
    logger = logging.getLogger("test.reliable_sender")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    logger.setLevel(logging.CRITICAL)
    return logger


class TestReliableSender(unittest.IsolatedAsyncioTestCase):
    async def test_enforces_global_min_interval(self) -> None:
        clock = _FakeClock()
        send_times: list[float] = []

        async def _send_message(text: str, *, reply_to_topic: bool, reply_to_msg_id=None) -> int | None:
            send_times.append(clock.monotonic())
            return len(send_times)

        sender = ReliableSender(
            send_message=_send_message,
            limiter=RateLimiter(global_per_minute=60, plugin_per_minute=60),
            logger=_test_logger(),
            dry_run=False,
            min_interval_seconds=10,
            sleep_fn=clock.sleep,
            monotonic_fn=clock.monotonic,
        )

        await sender.send("a", ".one", True)
        await sender.send("b", ".two", True)

        self.assertEqual(send_times, [0.0, 10.0])

    async def test_retries_wait_error_until_success(self) -> None:
        clock = _FakeClock()
        attempts = 0
        send_times: list[float] = []

        async def _send_message(text: str, *, reply_to_topic: bool, reply_to_msg_id=None) -> int | None:
            nonlocal attempts
            attempts += 1
            send_times.append(clock.monotonic())
            if attempts == 1:
                raise _WaitError(12)
            return 99

        sender = ReliableSender(
            send_message=_send_message,
            limiter=RateLimiter(global_per_minute=60, plugin_per_minute=60),
            logger=_test_logger(),
            dry_run=False,
            min_interval_seconds=10,
            sleep_fn=clock.sleep,
            monotonic_fn=clock.monotonic,
        )

        mid = await sender.send("a", ".one", True)

        self.assertEqual(mid, 99)
        self.assertEqual(send_times, [0.0, 13.0])

    async def test_retries_generic_exception_until_success(self) -> None:
        clock = _FakeClock()
        attempts = 0
        send_times: list[float] = []

        async def _send_message(text: str, *, reply_to_topic: bool, reply_to_msg_id=None) -> int | None:
            nonlocal attempts
            attempts += 1
            send_times.append(clock.monotonic())
            if attempts == 1:
                raise RuntimeError("network")
            return 7

        sender = ReliableSender(
            send_message=_send_message,
            limiter=RateLimiter(global_per_minute=60, plugin_per_minute=60),
            logger=_test_logger(),
            dry_run=False,
            min_interval_seconds=10,
            sleep_fn=clock.sleep,
            monotonic_fn=clock.monotonic,
        )

        mid = await sender.send("a", ".one", True)

        self.assertEqual(mid, 7)
        self.assertEqual(send_times, [0.0, 10.0])
