import logging
import unittest
from datetime import datetime, timezone

from xiuxian_bot.config import Config
from xiuxian_bot.core.contracts import MessageContext
from xiuxian_bot.plugins.daily import DailyPlugin


def _dummy_config(**overrides) -> Config:
    values = {
        "tg_api_id": "1",
        "tg_api_hash": "hash",
        "tg_session_name": "session",
        "game_chat_id": "-100",
        "topic_id": "123",
        "my_name": "Me",
        "send_to_topic": "true",
        "enable_daily": "true",
        "daily_bushi_times_per_day": "5",
        "daily_bushi_interval_seconds": "120",
        "daily_bushi_exchange_action": ".换取",
    }
    values.update(overrides)
    return Config.from_mapping(values)


class TestDailyPlugin(unittest.IsolatedAsyncioTestCase):
    async def test_bootstrap_sends_remaining_bushi_with_interval(self) -> None:
        plugin = DailyPlugin(
            _dummy_config(daily_bushi_times_per_day="2"),
            logging.getLogger("test"),
        )
        scheduled: list[tuple[str, float, object]] = []
        sends: list[tuple[str, str, bool]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                scheduled.append((key, delay_seconds, action))

        async def _send(plugin_name: str, text: str, reply_to_topic: bool) -> int | None:
            sends.append((plugin_name, text, reply_to_topic))
            return 1

        await plugin.bootstrap(_FakeScheduler(), _send)
        first = next(action for key, _, action in scheduled if key == "daily.bushi.loop")
        await first()
        second = scheduled[-1][2]
        await second()

        self.assertEqual(
            sends,
            [
                ("daily", ".卜筮问天", True),
                ("daily", ".卜筮问天", True),
            ],
        )
        self.assertEqual(scheduled[0][0], "daily.next_day")
        self.assertEqual(scheduled[1][0], "daily.bushi.loop")
        self.assertEqual(scheduled[2][0], "daily.bushi.loop")
        self.assertEqual(scheduled[2][1], 120.0)

    async def test_rare_event_replies_exchange_to_message(self) -> None:
        plugin = DailyPlugin(_dummy_config(), logging.getLogger("test"))
        ctx = MessageContext(
            chat_id=-100,
            message_id=1001,
            reply_to_msg_id=999,
            sender_id=123,
            text=(
                "【神物现世】！天机罗盘疯狂转动，最终指向一处被迷雾笼罩的上古神山！"
                "卦象显示，【昆吾通行令】的机缘已降临于你！\n"
                "天道示警：获取此等逆天之物，需献上祭品以获天道认可。\n"
                "请在 5分钟 内回复本消息 .换取 来确认，超时则机缘消散。"
            ),
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )

        actions = await plugin.on_message(ctx)

        assert actions is not None
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].text, ".换取")
        self.assertEqual(actions[0].reply_to_msg_id, 1001)

    async def test_rare_event_is_deduplicated(self) -> None:
        plugin = DailyPlugin(_dummy_config(), logging.getLogger("test"))
        ctx = MessageContext(
            chat_id=-100,
            message_id=1002,
            reply_to_msg_id=999,
            sender_id=123,
            text="神物现世 昆吾通行令 天道示警 回复本消息 .换取 @Me",
            ts=datetime.now(timezone.utc),
            is_reply=False,
            is_reply_to_me=False,
        )

        self.assertIsNotNone(await plugin.on_message(ctx))
        self.assertIsNone(await plugin.on_message(ctx))

    async def test_normal_hexagram_is_ignored(self) -> None:
        plugin = DailyPlugin(_dummy_config(), logging.getLogger("test"))
        ctx = MessageContext(
            chat_id=-100,
            message_id=1003,
            reply_to_msg_id=999,
            sender_id=123,
            text="【卦象：平】古井无波。",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )

        self.assertIsNone(await plugin.on_message(ctx))


if __name__ == "__main__":
    unittest.main()
