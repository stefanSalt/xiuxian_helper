import logging
import unittest
from datetime import datetime, timezone

from xiuxian_bot.config import Config
from xiuxian_bot.core.contracts import MessageContext
from xiuxian_bot.plugins.wild_explore import WildExplorePlugin
from xiuxian_bot.runtime import build_plugins


def _dummy_config(**overrides) -> Config:
    values = {
        "tg_api_id": "1",
        "tg_api_hash": "hash",
        "tg_session_name": "session",
        "game_chat_id": "-100",
        "topic_id": "123",
        "my_name": "Me",
        "enable_wild_explore": True,
    }
    values.update(overrides)
    return Config.from_mapping(values)


def _ctx(text: str) -> MessageContext:
    return MessageContext(
        chat_id=-100,
        message_id=2001,
        reply_to_msg_id=1001,
        sender_id=999,
        text=text,
        ts=datetime.now(timezone.utc),
        is_reply=True,
        is_reply_to_me=True,
    )


class TestWildExplorePlugin(unittest.IsolatedAsyncioTestCase):
    def test_build_plugins_includes_wild_explore(self) -> None:
        plugins = build_plugins(_dummy_config(), logging.getLogger("test"))
        self.assertIn("wild_explore", {plugin.name for plugin in plugins})

    async def test_bootstrap_schedules_initial_loop(self) -> None:
        plugin = WildExplorePlugin(_dummy_config(), logging.getLogger("test"))
        calls: list[tuple[str, float]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                calls.append((key, delay_seconds))

        async def _send(_plugin: str, _text: str, _reply_to_topic: bool) -> int | None:
            return None

        await plugin.bootstrap(_FakeScheduler(), _send)
        self.assertEqual(calls, [("wild_explore.loop", 0.0)])

    async def test_loop_sends_configured_strategy_twice_and_reschedules(self) -> None:
        plugin = WildExplorePlugin(
            _dummy_config(
                wild_explore_interval_seconds="7200",
                wild_explore_strategy="谨慎",
                wild_explore_repeat_delay_seconds="7",
            ),
            logging.getLogger("test"),
        )
        calls: list[tuple[str, float, object]] = []
        sends: list[str] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                calls.append((key, delay_seconds, action))

        async def _send(_plugin: str, text: str, _reply_to_topic: bool) -> int | None:
            sends.append(text)
            return None

        await plugin.bootstrap(_FakeScheduler(), _send)
        await calls[0][2]()
        self.assertEqual(sends, [".野外历练 谨慎"])
        self.assertIn(("wild_explore.repeat", 7.0), [(key, delay) for key, delay, _ in calls])
        self.assertIn(("wild_explore.loop", 7200.0), [(key, delay) for key, delay, _ in calls])

        repeat_action = next(action for key, _, action in calls if key == "wild_explore.repeat")
        await repeat_action()
        self.assertEqual(sends, [".野外历练 谨慎", ".野外历练 谨慎"])

    async def test_zero_repeat_delay_sends_twice_without_repeat_schedule(self) -> None:
        plugin = WildExplorePlugin(
            _dummy_config(wild_explore_repeat_delay_seconds="0"),
            logging.getLogger("test"),
        )
        calls: list[tuple[str, float, object]] = []
        sends: list[str] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                calls.append((key, delay_seconds, action))

        async def _send(_plugin: str, text: str, _reply_to_topic: bool) -> int | None:
            sends.append(text)
            return None

        await plugin.bootstrap(_FakeScheduler(), _send)
        await calls[0][2]()
        self.assertEqual(sends, [".野外历练 深入", ".野外历练 深入"])
        self.assertNotIn("wild_explore.repeat", {key for key, _, _ in calls})

    async def test_cooldown_feedback_reschedules_after_remaining_time(self) -> None:
        plugin = WildExplorePlugin(_dummy_config(), logging.getLogger("test"))
        calls: list[tuple[str, float, object]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                calls.append((key, delay_seconds, action))

        async def _send(_plugin: str, _text: str, _reply_to_topic: bool) -> int | None:
            return None

        await plugin.bootstrap(_FakeScheduler(), _send)
        await plugin.on_message(
            _ctx("【野外历练】\n山中灵机未复，请在 1小时59分钟45秒 后再来。")
        )

        self.assertIn(("wild_explore.loop", 7185.0), [(key, delay) for key, delay, _ in calls])

    async def test_normal_feedback_reschedules_after_interval(self) -> None:
        plugin = WildExplorePlugin(
            _dummy_config(wild_explore_interval_seconds="7200"),
            logging.getLogger("test"),
        )
        calls: list[tuple[str, float, object]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                calls.append((key, delay_seconds, action))

        async def _send(_plugin: str, _text: str, _reply_to_topic: bool) -> int | None:
            return None

        await plugin.bootstrap(_FakeScheduler(), _send)
        await plugin.on_message(_ctx("【野外历练】\n你深入山林，遭遇妖兽，获得灵石。"))

        self.assertIn(("wild_explore.loop", 7200.0), [(key, delay) for key, delay, _ in calls])
