import logging
import unittest
from datetime import datetime, timezone

from xiuxian_bot.config import Config
from xiuxian_bot.core.contracts import MessageContext
from xiuxian_bot.plugins.shiqie import ShiqiePlugin
from xiuxian_bot.runtime import build_plugins


def _dummy_config(**overrides) -> Config:
    values = {
        "tg_api_id": "1",
        "tg_api_hash": "hash",
        "tg_session_name": "session",
        "game_chat_id": "-100",
        "topic_id": "123",
        "my_name": "Me",
        "enable_shiqie": True,
    }
    values.update(overrides)
    return Config.from_mapping(values)


def _ctx(text: str, *, reply_to_msg_id: int | None = 1001) -> MessageContext:
    return MessageContext(
        chat_id=-100,
        message_id=2001,
        reply_to_msg_id=reply_to_msg_id,
        sender_id=999,
        text=text,
        ts=datetime.now(timezone.utc),
        is_reply=reply_to_msg_id is not None,
        is_reply_to_me=reply_to_msg_id is not None,
    )


class TestShiqiePlugin(unittest.IsolatedAsyncioTestCase):
    def test_build_plugins_includes_shiqie(self) -> None:
        plugins = build_plugins(_dummy_config(), logging.getLogger("test"))
        self.assertIn("shiqie", {plugin.name for plugin in plugins})

    async def test_bootstrap_schedules_two_loops(self) -> None:
        plugin = ShiqiePlugin(_dummy_config(), logging.getLogger("test"))
        calls: list[tuple[str, float, object]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                calls.append((key, delay_seconds, action))

        async def _send(_plugin: str, _text: str, _reply_to_topic: bool) -> int | None:
            return None

        await plugin.bootstrap(_FakeScheduler(), _send)
        self.assertEqual(
            [(key, delay) for key, delay, _ in calls],
            [("shiqie.tianji.loop", 0.0), ("shiqie.rumeng.loop", 0.0)],
        )

    async def test_loops_send_commands_and_schedule_fallbacks(self) -> None:
        plugin = ShiqiePlugin(_dummy_config(), logging.getLogger("test"))
        calls: list[tuple[str, float, object]] = []
        sends: list[str] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                calls.append((key, delay_seconds, action))

        async def _send(_plugin: str, text: str, _reply_to_topic: bool) -> int | None:
            sends.append(text)
            return 1000 + len(sends)

        await plugin.bootstrap(_FakeScheduler(), _send)
        await calls[0][2]()
        await calls[1][2]()

        self.assertEqual(sends, [".天机代卜", ".入梦寻图"])
        self.assertIn(("shiqie.tianji.loop", 43200.0), [(key, delay) for key, delay, _ in calls])
        self.assertIn(("shiqie.rumeng.loop", 28800.0), [(key, delay) for key, delay, _ in calls])

    async def test_tianji_cooldown_reschedules_only_tianji(self) -> None:
        plugin = ShiqiePlugin(_dummy_config(), logging.getLogger("test"))
        calls: list[tuple[str, float, object]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                calls.append((key, delay_seconds, action))

        async def _send(_plugin: str, _text: str, _reply_to_topic: bool) -> int | None:
            return 1001

        await plugin.bootstrap(_FakeScheduler(), _send)
        await calls[0][2]()
        await plugin.on_message(_ctx("请在 10小时33分钟59秒 后再试", reply_to_msg_id=1001))

        self.assertIn(("shiqie.tianji.loop", 38039.0), [(key, delay) for key, delay, _ in calls])
        self.assertNotIn(("shiqie.rumeng.loop", 38039.0), [(key, delay) for key, delay, _ in calls])

    async def test_rumeng_progress_four_triggers_pintu(self) -> None:
        plugin = ShiqiePlugin(_dummy_config(), logging.getLogger("test"))
        calls: list[tuple[str, float, object]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                calls.append((key, delay_seconds, action))

        async def _send(_plugin: str, text: str, _reply_to_topic: bool) -> int | None:
            return 1002 if text == ".入梦寻图" else None

        await plugin.bootstrap(_FakeScheduler(), _send)
        await calls[1][2]()
        actions = await plugin.on_message(_ctx("入梦寻图完成，当前进度：4/4", reply_to_msg_id=1002))

        assert actions is not None
        self.assertEqual([action.text for action in actions], [".拼图"])
        self.assertIn(("shiqie.rumeng.loop", 28800.0), [(key, delay) for key, delay, _ in calls])

    async def test_rumeng_progress_under_four_does_not_trigger_pintu(self) -> None:
        plugin = ShiqiePlugin(_dummy_config(), logging.getLogger("test"))
        actions = await plugin.on_message(_ctx("入梦寻图完成，当前进度：3/4"))

        self.assertIsNone(actions)
