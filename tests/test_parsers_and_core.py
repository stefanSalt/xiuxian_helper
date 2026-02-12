import asyncio
import logging
import unittest

from xiuxian_bot.core.scheduler import Scheduler
from xiuxian_bot.domain.parsers import (
    parse_biguan_cooldown_minutes,
    parse_lingqi_cooldown_seconds,
)


class TestParsers(unittest.TestCase):
    def test_parse_biguan_cooldown_minutes(self) -> None:
        self.assertEqual(parse_biguan_cooldown_minutes("打坐调息 10 分钟"), 10)
        self.assertEqual(parse_biguan_cooldown_minutes("打坐调息10分钟"), 10)
        self.assertIsNone(parse_biguan_cooldown_minutes("打坐调息"))

    def test_parse_lingqi_cooldown_seconds(self) -> None:
        self.assertEqual(parse_lingqi_cooldown_seconds("灵气尚未平复，请在 18秒 后再试"), 18)
        self.assertEqual(parse_lingqi_cooldown_seconds("请在 10分钟8秒 后再试（灵气尚未平复）"), 608)
        self.assertIsNone(parse_lingqi_cooldown_seconds("灵气尚未平复"))


class TestScheduler(unittest.IsolatedAsyncioTestCase):
    async def test_schedule_override_by_key(self) -> None:
        logger = logging.getLogger("test")
        scheduler = Scheduler(logger)

        hits: list[str] = []

        async def action1() -> None:
            hits.append("1")

        async def action2() -> None:
            hits.append("2")

        await scheduler.schedule(key="k", delay_seconds=0.2, action=action1)
        await scheduler.schedule(key="k", delay_seconds=0.05, action=action2)

        await asyncio.sleep(0.3)
        await scheduler.cancel_all()

        self.assertEqual(hits, ["2"])


if __name__ == "__main__":
    unittest.main()

