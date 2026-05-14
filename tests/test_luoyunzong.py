import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from xiuxian_bot.config import Config
from xiuxian_bot.core.contracts import MessageContext
from xiuxian_bot.core.state_store import SQLiteStateStore
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

WATERING_SUCCESS = """【🌿 灵树灌溉】
当前环境: 生机萎靡 (需 木/森/草)
你注入了: 木行 灵气
💫 灵息相应 (多属性灵根找准了这一轮的发力方向)
------------------------------
🌳 成熟度: 66.38% -> 66.48%
🏅 宗门贡献: +30
🌱 养树底蕴 +10
🛡️ 护山底蕴 +2
"""

WATERING_COOLDOWN = "地脉灵气尚未恢复，请在 1小时57分钟36秒 后再来灌溉。"
WATERING_UNNEEDED = "灵眼之树已然成熟或正遭劫难，此刻无需灌溉，静待或守护即可。"
GUARD_COOLDOWN = "你刚刚注入过灵力,经脉尚需调息! 请在4分钟43秒后再来守山"
PUBLIC_GUARD_STARTED = """🚨 【警报！古剑门来袭！】 🚨
古剑门的修士觊觎我宗灵眼之树，趁其生长关键之时大举来犯！护山大阵已开启！
所有落云宗弟子请立刻使用 .协同守山 抵御外敌！
大阵耐久: 7200 / 7200
"""
PUBLIC_GUARD_FINISHED = """【守护成功！】
在众弟子的齐心协力下，古剑门的攻势已被成功击退！
灵眼之树安然无恙，继续汲取天地灵气生长！
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
        await calls[1][2]()

        self.assertEqual(sends, [".我的灵根", ".灵树状态"])
        self.assertIn(("luoyunzong.status.loop", 1800.0), [(key, delay) for key, delay, _ in calls])
        self.assertIn(
            ("luoyunzong.linggen.loop", 86400.0),
            [(key, delay) for key, delay, _ in calls],
        )

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

    async def test_public_guard_started_sends_guard(self) -> None:
        base_now = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
        plugin = LuoyunzongPlugin(
            _dummy_config(luoyunzong_watering_strategy="always"),
            logging.getLogger("test"),
            now_fn=lambda: base_now,
        )

        actions = await plugin.on_global_status(_ctx(PUBLIC_GUARD_STARTED, reply_to_msg_id=None))

        assert actions is not None
        self.assertEqual([action.text for action in actions], [".协同守山"])
        self.assertEqual(plugin._pending_action, "guard")  # type: ignore[attr-defined]
        self.assertTrue(plugin._last_status_under_attack)  # type: ignore[attr-defined]

    async def test_public_guard_finished_exits_guard_state(self) -> None:
        plugin = LuoyunzongPlugin(
            _dummy_config(luoyunzong_watering_strategy="always"),
            logging.getLogger("test"),
        )

        await plugin.on_global_status(_ctx(PUBLIC_GUARD_STARTED, reply_to_msg_id=None))
        actions = await plugin.on_global_status(_ctx(PUBLIC_GUARD_FINISHED, reply_to_msg_id=None))

        self.assertIsNone(actions)
        self.assertIsNone(plugin._pending_action)  # type: ignore[attr-defined]
        self.assertFalse(plugin._last_status_under_attack)  # type: ignore[attr-defined]

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

    async def test_global_harvested_status_keeps_harvest_suppression_per_identity(self) -> None:
        base_now = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
        plugin_a = LuoyunzongPlugin(
            _dummy_config(active_identity_key="avatar_a"),
            logging.getLogger("test"),
            now_fn=lambda: base_now,
        )
        plugin_b = LuoyunzongPlugin(
            _dummy_config(active_identity_key="avatar_b"),
            logging.getLogger("test"),
            now_fn=lambda: base_now,
        )

        await plugin_a.on_message(_ctx(HARVESTED_STATUS_WITH_REMAINING))
        actions_a = await plugin_a.on_global_status(_ctx(HARVESTED_STATUS_WITH_REMAINING))
        actions_b = await plugin_b.on_global_status(_ctx(HARVESTED_STATUS_WITH_REMAINING))

        self.assertIsNone(actions_a)
        assert actions_b is not None
        self.assertEqual([action.text for action in actions_b], [".采摘灵果"])

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
        self.assertEqual(sends, [".灵树状态"])

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
        await plugin.on_message(_ctx(WATERING_COOLDOWN))

        self.assertIn(7056.0, [delay for _, delay, _ in calls])
        self.assertEqual(  # type: ignore[attr-defined]
            plugin._watering_next_at,
            base_now + timedelta(hours=1, minutes=57, seconds=36),
        )

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

        await plugin.on_message(_ctx(WATERING_SUCCESS))
        self.assertEqual(  # type: ignore[attr-defined]
            plugin._watering_next_at,
            base_now + timedelta(seconds=7200),
        )

    async def test_watering_pending_ignores_unrelated_messages(self) -> None:
        base_now = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
        plugin = LuoyunzongPlugin(
            _dummy_config(luoyunzong_watering_strategy="always"),
            logging.getLogger("test"),
            now_fn=lambda: base_now,
        )

        await plugin.on_message(_ctx(NORMAL_STATUS))
        await plugin.on_message(_ctx("📊 天道股市 · 实时行情"))

        self.assertEqual(plugin._pending_action, "watering")  # type: ignore[attr-defined]
        self.assertIsNone(plugin._watering_next_at)  # type: ignore[attr-defined]

        await plugin.on_message(_ctx(WATERING_SUCCESS))
        self.assertEqual(  # type: ignore[attr-defined]
            plugin._watering_next_at,
            base_now + timedelta(seconds=7200),
        )

    async def test_watering_unneeded_feedback_does_not_refresh_cooldown(self) -> None:
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
        await plugin.on_message(_ctx(WATERING_UNNEEDED))

        self.assertIsNone(plugin._pending_action)  # type: ignore[attr-defined]
        self.assertIsNone(plugin._watering_next_at)  # type: ignore[attr-defined]
        self.assertIn(("luoyunzong.status.loop", 0.0), [(key, delay) for key, delay, _ in calls])

    async def test_guard_cooldown_feedback_reschedules_status(self) -> None:
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
        await plugin.on_message(_ctx(ATTACK_STATUS))
        await plugin.on_message(_ctx(GUARD_COOLDOWN))

        self.assertIn(283.0, [delay for _, delay, _ in calls])
        self.assertEqual(  # type: ignore[attr-defined]
            plugin._guard_suppress_until,
            base_now + timedelta(minutes=4, seconds=43),
        )
        self.assertIsNone(plugin._pending_action)  # type: ignore[attr-defined]

    async def test_pending_watering_expires_and_allows_retry(self) -> None:
        current_now = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
        plugin = LuoyunzongPlugin(
            _dummy_config(luoyunzong_watering_strategy="always"),
            logging.getLogger("test"),
            now_fn=lambda: current_now,
        )

        actions = await plugin.on_message(_ctx(NORMAL_STATUS))
        current_now = current_now + timedelta(minutes=6)
        retry_actions = await plugin.on_message(_ctx(NORMAL_STATUS))

        assert actions is not None
        assert retry_actions is not None
        self.assertEqual([action.text for action in actions], [".灵树灌溉"])
        self.assertEqual([action.text for action in retry_actions], [".灵树灌溉"])

    async def test_watering_state_is_scoped_by_identity_store(self) -> None:
        base_now = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.sqlite3"
            store_a = SQLiteStateStore(str(path), account_id="1:avatar_a")
            store_b = SQLiteStateStore(str(path), account_id="1:avatar_b")
            plugin_a = LuoyunzongPlugin(
                _dummy_config(luoyunzong_watering_strategy="always"),
                logging.getLogger("test"),
                now_fn=lambda: base_now,
            )
            plugin_b = LuoyunzongPlugin(
                _dummy_config(luoyunzong_watering_strategy="always"),
                logging.getLogger("test"),
                now_fn=lambda: base_now,
            )
            plugin_a.set_state_store(store_a)
            plugin_b.set_state_store(store_b)

            await plugin_a.on_message(_ctx(NORMAL_STATUS))
            await plugin_a.on_message(_ctx(WATERING_SUCCESS))
            plugin_b.restore_state()

            self.assertEqual(  # type: ignore[attr-defined]
                plugin_a._watering_next_at,
                base_now + timedelta(seconds=7200),
            )
            self.assertIsNone(plugin_b._watering_next_at)  # type: ignore[attr-defined]
            store_a.close()
            store_b.close()

    async def test_status_check_owner_is_shared_across_identity_stores(self) -> None:
        base_now = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.sqlite3"
            root = SQLiteStateStore(str(path), account_id="root")
            global_store = root.for_account("__global__:luoyunzong")
            plugin_a = LuoyunzongPlugin(
                _dummy_config(account_id="1", active_identity_key="avatar_a"),
                logging.getLogger("test"),
                now_fn=lambda: base_now,
            )
            plugin_b = LuoyunzongPlugin(
                _dummy_config(account_id="2", active_identity_key="avatar_b"),
                logging.getLogger("test"),
                now_fn=lambda: base_now,
            )
            plugin_a.set_state_store(root.for_account("1:avatar_a"))
            plugin_b.set_state_store(root.for_account("2:avatar_b"))
            plugin_a.set_global_state_store(global_store)
            plugin_b.set_global_state_store(global_store)
            plugin_a.restore_state()
            plugin_b.restore_state()
            calls_a: list[tuple[str, float, object]] = []
            calls_b: list[tuple[str, float, object]] = []

            class _FakeSchedulerA:
                async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                    calls_a.append((key, delay_seconds, action))

            class _FakeSchedulerB:
                async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                    calls_b.append((key, delay_seconds, action))

            async def _send(_plugin: str, _text: str, _reply_to_topic: bool) -> int | None:
                return None

            await plugin_a.bootstrap(_FakeSchedulerA(), _send)
            await plugin_b.bootstrap(_FakeSchedulerB(), _send)

            self.assertIn("luoyunzong.status.loop", [key for key, _, _ in calls_a])
            self.assertNotIn("luoyunzong.status.loop", [key for key, _, _ in calls_b])
            self.assertIn("luoyunzong.linggen.loop", [key for key, _, _ in calls_a])
            self.assertIn("luoyunzong.linggen.loop", [key for key, _, _ in calls_b])
            root.close()

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
