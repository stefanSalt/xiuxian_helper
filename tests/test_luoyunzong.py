import logging
import unittest
from datetime import datetime, timedelta, timezone

from xiuxian_bot.config import Config
from xiuxian_bot.core.contracts import MessageContext
from xiuxian_bot.plugins.luoyunzong import LuoyunzongPlugin
from xiuxian_bot.runtime import build_plugins


def _dummy_config(**overrides) -> Config:
    values = {
        "tg_api_id": "1",
        "tg_api_hash": "hash",
        "tg_session_name": "session",
        "game_chat_id": "-100",
        "topic_id": "123",
        "my_name": "Me",
        "enable_luoyunzong": True,
        "luoyunzong_status_interval_seconds": "1800",
        "luoyunzong_watering_cooldown_seconds": "7200",
        "luoyunzong_watering_strategy": "match_linggen",
        "luoyunzong_watering_required_needs": "",
        "luoyunzong_linggen_refresh_seconds": "86400",
        "luoyunzong_harvest_suppress_seconds": "86400",
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


NORMAL_STATUS = """【落云宗 · 灵眼之树】
🌿 环境: 生机萎靡 (需 木/森/草)
🌲 进度:
🟩🟩🟩⬜ 81.42%
🔄 阶段: 4 / 4
🏛️ 三派异动: 【古剑门·试剑修枝】
👤 你的当前状态: 2098 点
"""


ATTACK_STATUS = """【落云宗 · 灵眼之树】
⚔️ 警报: 古剑门入侵中！大阵耐久: 9890
请速用 .协同守山！
🏛️ 三派异动: 【古剑门·攻山夺枝】
"""


ATTACK_FORECAST_STATUS = """【落云宗 · 灵眼之树】
🌿 环境: 生机萎靡 (需 木/森/草)
🌲 进度:
🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩⬜ 95.79%
🔄 阶段: 4 / 4
🏛️ 三派异动: 【古剑门·攻山夺枝】
   古剑门趁灵树将熟，突袭山门，试图强夺本轮枝果。
👤 你的当前状态: 232 点
"""


MATURE_STATUS = """【落云宗 · 灵眼之树】
✨ 状态: 成熟采摘期
⏳ 剩余: 0秒
👤 你的当前状态: 1265 点
"""


HARVESTED_STATUS = """【落云宗 · 灵眼之树】
✨ 状态: 成熟采摘期
👤 你的当前状态: 已采摘 (奖励已入袋)
"""


HARVESTED_STATUS_WITH_REMAINING = """【落云宗 · 灵眼之树】
✨ 状态: 成熟采摘期
⏳ 剩余: 19小时40分钟10秒
👤 你的当前状态: 已采摘 (奖励已入袋)
"""


class TestLuoyunzongPlugin(unittest.IsolatedAsyncioTestCase):
    def test_build_plugins_includes_luoyunzong(self) -> None:
        plugins = build_plugins(_dummy_config(), logging.getLogger("test"))
        self.assertIn("luoyunzong", {plugin.name for plugin in plugins})

    def test_parse_status_extracts_needs_progress_and_stage(self) -> None:
        plugin = LuoyunzongPlugin(_dummy_config(), logging.getLogger("test"))

        status = plugin._parse_status(NORMAL_STATUS)  # noqa: SLF001

        self.assertEqual(status["needs"], ["木", "森", "草"])
        self.assertEqual(status["progress"], 81.42)
        self.assertEqual(status["stage"], (4, 4))

    async def test_bootstrap_sends_linggen_then_status(self) -> None:
        plugin = LuoyunzongPlugin(_dummy_config(), logging.getLogger("test"))
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

        self.assertEqual(sends, [".我的灵根", ".灵树状态"])
        self.assertIn(("luoyunzong.status.loop", 1800.0), [(key, delay) for key, delay, _ in calls])

    async def test_linggen_match_sends_watering(self) -> None:
        plugin = LuoyunzongPlugin(_dummy_config(), logging.getLogger("test"))
        await plugin.on_message(_ctx("灵根: 天灵根(木)"))

        actions = await plugin.on_message(_ctx(NORMAL_STATUS))

        assert actions is not None
        self.assertEqual([action.text for action in actions], [".灵树灌溉"])

    async def test_attack_sends_guard_not_watering(self) -> None:
        plugin = LuoyunzongPlugin(
            _dummy_config(luoyunzong_watering_strategy="always"),
            logging.getLogger("test"),
        )

        actions = await plugin.on_message(_ctx(ATTACK_STATUS))

        assert actions is not None
        self.assertEqual([action.text for action in actions], [".协同守山"])

    async def test_attack_forecast_does_not_send_guard(self) -> None:
        plugin = LuoyunzongPlugin(
            _dummy_config(luoyunzong_watering_strategy="always"),
            logging.getLogger("test"),
        )

        actions = await plugin.on_message(_ctx(ATTACK_FORECAST_STATUS))

        assert actions is not None
        self.assertEqual([action.text for action in actions], [".灵树灌溉"])

    async def test_mature_status_sends_harvest_once(self) -> None:
        base_now = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
        plugin = LuoyunzongPlugin(
            _dummy_config(),
            logging.getLogger("test"),
            now_fn=lambda: base_now,
        )

        actions = await plugin.on_message(_ctx(MATURE_STATUS))
        second_actions = await plugin.on_message(_ctx(MATURE_STATUS))

        assert actions is not None
        self.assertEqual([action.text for action in actions], [".采摘灵果"])
        self.assertIsNone(plugin._harvest_suppress_until)  # type: ignore[attr-defined]
        self.assertIsNone(second_actions)

        await plugin.on_message(_ctx("采摘灵果成功，奖励已入袋。"))
        self.assertEqual(  # type: ignore[attr-defined]
            plugin._harvest_suppress_until,
            base_now + timedelta(seconds=86400),
        )

    async def test_harvested_status_checks_again_after_four_hours(self) -> None:
        base_now = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
        plugin = LuoyunzongPlugin(
            _dummy_config(),
            logging.getLogger("test"),
            now_fn=lambda: base_now,
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
        await plugin.on_message(_ctx(HARVESTED_STATUS))
        await calls[-1][2]()

        self.assertEqual(calls[-1][0], "luoyunzong.status.loop")
        self.assertEqual(calls[-1][1], 14400.0)
        self.assertEqual(sends, [".我的灵根", ".灵树状态"])

    async def test_harvested_status_uses_remaining_time(self) -> None:
        base_now = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
        plugin = LuoyunzongPlugin(
            _dummy_config(),
            logging.getLogger("test"),
            now_fn=lambda: base_now,
        )

        await plugin.on_message(_ctx(HARVESTED_STATUS_WITH_REMAINING))

        self.assertEqual(  # type: ignore[attr-defined]
            plugin._harvest_suppress_until,
            base_now + timedelta(hours=19, minutes=40, seconds=10),
        )

    async def test_harvested_status_without_remaining_does_not_extend_suppression(self) -> None:
        current_now = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
        plugin = LuoyunzongPlugin(
            _dummy_config(),
            logging.getLogger("test"),
            now_fn=lambda: current_now,
        )
        one_hour_status = HARVESTED_STATUS_WITH_REMAINING.replace(
            "19小时40分钟10秒",
            "1小时",
        )

        await plugin.on_message(_ctx(one_hour_status))
        current_now = current_now + timedelta(minutes=10)
        await plugin.on_message(_ctx(HARVESTED_STATUS))

        self.assertEqual(  # type: ignore[attr-defined]
            plugin._harvest_suppress_until,
            datetime(2026, 5, 8, 13, 0, tzinfo=timezone.utc),
        )

    async def test_normal_status_clears_harvest_suppression(self) -> None:
        base_now = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
        plugin = LuoyunzongPlugin(
            _dummy_config(luoyunzong_watering_strategy="always"),
            logging.getLogger("test"),
            now_fn=lambda: base_now,
        )

        await plugin.on_message(_ctx(HARVESTED_STATUS_WITH_REMAINING))
        actions = await plugin.on_message(_ctx(NORMAL_STATUS))

        self.assertIsNone(plugin._harvest_suppress_until)  # type: ignore[attr-defined]
        assert actions is not None
        self.assertEqual([action.text for action in actions], [".灵树灌溉"])

    async def test_mature_status_without_harvested_clears_harvest_suppression(self) -> None:
        base_now = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
        plugin = LuoyunzongPlugin(
            _dummy_config(),
            logging.getLogger("test"),
            now_fn=lambda: base_now,
        )

        await plugin.on_message(_ctx(HARVESTED_STATUS_WITH_REMAINING))
        actions = await plugin.on_message(_ctx(MATURE_STATUS))

        self.assertIsNone(plugin._harvest_suppress_until)  # type: ignore[attr-defined]
        assert actions is not None
        self.assertEqual([action.text for action in actions], [".采摘灵果"])

    async def test_always_strategy_waters_without_linggen_match(self) -> None:
        plugin = LuoyunzongPlugin(
            _dummy_config(luoyunzong_watering_strategy="always"),
            logging.getLogger("test"),
        )

        actions = await plugin.on_message(_ctx(NORMAL_STATUS))

        assert actions is not None
        self.assertEqual([action.text for action in actions], [".灵树灌溉"])

    async def test_match_need_strategy_waters_only_configured_needs(self) -> None:
        plugin = LuoyunzongPlugin(
            _dummy_config(
                luoyunzong_watering_strategy="match_need",
                luoyunzong_watering_required_needs="火,金",
            ),
            logging.getLogger("test"),
        )

        no_action = await plugin.on_message(_ctx(NORMAL_STATUS))
        yes_action = await plugin.on_message(_ctx(NORMAL_STATUS.replace("木/森/草", "火/土")))

        self.assertIsNone(no_action)
        assert yes_action is not None
        self.assertEqual([action.text for action in yes_action], [".灵树灌溉"])

    async def test_watering_cooldown_feedback_reschedules_status(self) -> None:
        base_now = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
        plugin = LuoyunzongPlugin(
            _dummy_config(luoyunzong_watering_strategy="always"),
            logging.getLogger("test"),
            now_fn=lambda: base_now,
        )
        calls: list[tuple[str, float, object]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                calls.append((key, delay_seconds, action))

        async def _send(_plugin: str, _text: str, _reply_to_topic: bool) -> int | None:
            return None

        await plugin.bootstrap(_FakeScheduler(), _send)
        await plugin.on_message(_ctx(NORMAL_STATUS))
        await plugin.on_message(_ctx("灵树灌溉冷却中，请在 1小时59分钟45秒 后再来。"))

        self.assertIn((7200.0, 7185.0), [(7200.0, delay) for _, delay, _ in calls])

    async def test_watering_state_waits_for_feedback(self) -> None:
        base_now = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
        plugin = LuoyunzongPlugin(
            _dummy_config(luoyunzong_watering_strategy="always"),
            logging.getLogger("test"),
            now_fn=lambda: base_now,
        )

        actions = await plugin.on_message(_ctx(NORMAL_STATUS))
        second_actions = await plugin.on_message(_ctx(NORMAL_STATUS))

        assert actions is not None
        self.assertEqual([action.text for action in actions], [".灵树灌溉"])
        self.assertIsNone(plugin._watering_next_at)  # type: ignore[attr-defined]
        self.assertIsNone(second_actions)

        await plugin.on_message(_ctx("【✂️ 灵树灌溉】 成熟度: 0.95% -> 1.05%"))
        self.assertEqual(  # type: ignore[attr-defined]
            plugin._watering_next_at,
            base_now + timedelta(seconds=7200),
        )

    async def test_status_decision_logs_skip_reason(self) -> None:
        plugin = LuoyunzongPlugin(
            _dummy_config(luoyunzong_watering_strategy="match_linggen"),
            logging.getLogger("test.luoyunzong"),
        )

        with self.assertLogs("test.luoyunzong", level="INFO") as captured:
            actions = await plugin.on_message(_ctx(NORMAL_STATUS))

        self.assertIsNone(actions)
        self.assertTrue(
            any(
                "luoyunzong_decision" in line and "reason=linggen_missing" in line
                for line in captured.output
            )
        )


if __name__ == "__main__":
    unittest.main()
