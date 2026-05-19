import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from xiuxian_bot.config import Config
from xiuxian_bot.core.contracts import MessageContext
from xiuxian_bot.core.state_store import SQLiteStateStore
from xiuxian_bot.plugins.qiling import QilingPlugin
from xiuxian_bot.runtime import build_plugins


ARTIFACT = "青竹蜂云剑（神雷版）"

QILING_LIST = f"""【本命器灵录】
🔮 雷竹 (依附于: {ARTIFACT}) - 状态: 温养中 - 等级: 1 (10/100)
🔮 斩灵 (依附于: 玄天斩灵剑) - 状态: 已苏醒 - 等级: 2 (20/100)
"""


def _dummy_config(**overrides) -> Config:
    values = {
        "tg_api_id": "1",
        "tg_api_hash": "hash",
        "tg_session_name": "session",
        "game_chat_id": "-100",
        "topic_id": "123",
        "my_name": "Me",
        "enable_qiling": True,
        "qiling_artifact_names": ARTIFACT,
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


class _FakeScheduler:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float, object]] = []

    async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
        self.calls.append((key, delay_seconds, action))


class TestQilingPlugin(unittest.IsolatedAsyncioTestCase):
    def test_build_plugins_includes_qiling(self) -> None:
        plugins = build_plugins(_dummy_config(), logging.getLogger("test"))
        self.assertIn("qiling", {plugin.name for plugin in plugins})

    async def test_artifact_list_discovery_extracts_attached_artifacts(self) -> None:
        plugin = QilingPlugin(
            _dummy_config(qiling_artifact_names=""),
            logging.getLogger("test"),
        )

        await plugin.on_message(_ctx(QILING_LIST))

        self.assertEqual(
            plugin._artifact_names(),  # type: ignore[attr-defined]
            [ARTIFACT, "玄天斩灵剑"],
        )

    async def test_bootstrap_discovers_when_no_artifact_is_configured(self) -> None:
        plugin = QilingPlugin(
            _dummy_config(qiling_artifact_names=""),
            logging.getLogger("test"),
        )
        scheduler = _FakeScheduler()
        sends: list[str] = []

        async def _send(_plugin: str, text: str, _reply_to_topic: bool) -> int | None:
            sends.append(text)
            return None

        await plugin.bootstrap(scheduler, _send)
        await scheduler.calls[0][2]()

        self.assertEqual(sends, [".我的器灵"])
        self.assertIn(
            ("qiling.loop", 3600.0),
            [(key, delay) for key, delay, _ in scheduler.calls],
        )

    async def test_independent_switches_allow_only_enabled_action(self) -> None:
        plugin = QilingPlugin(
            _dummy_config(
                qiling_enable_touch=False,
                qiling_enable_nurture=True,
                qiling_enable_trial=False,
            ),
            logging.getLogger("test"),
        )
        scheduler = _FakeScheduler()
        sends: list[str] = []

        async def _send(_plugin: str, text: str, _reply_to_topic: bool) -> int | None:
            sends.append(text)
            return None

        await plugin.bootstrap(scheduler, _send)
        await scheduler.calls[0][2]()

        self.assertEqual(sends, [f".温养器灵 {ARTIFACT}"])

    async def test_detail_status_updates_action_cooldowns(self) -> None:
        base_now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
        plugin = QilingPlugin(
            _dummy_config(qiling_artifact_names="玄天斩灵剑"),
            logging.getLogger("test"),
            now_fn=lambda: base_now,
        )

        await plugin.on_message(
            _ctx(
                """【器灵玉鉴】
🗡️ 玄天斩灵剑
- 温养状态: 需再等待 4小时12分钟5秒
- 试炼状态: 此刻可试炼
- 护主状态: 需再等待 1小时2分钟3秒
"""
            )
        )

        state = plugin._artifact_state("玄天斩灵剑")  # type: ignore[attr-defined]
        self.assertEqual(
            state["nurture_next_at"],
            base_now + timedelta(hours=4, minutes=12, seconds=5),
        )
        self.assertIsNone(state["trial_next_at"])
        self.assertEqual(
            state["protect_next_at"],
            base_now + timedelta(hours=1, minutes=2, seconds=3),
        )

    async def test_resource_missing_delays_only_nurture(self) -> None:
        base_now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
        plugin = QilingPlugin(
            _dummy_config(
                qiling_enable_touch=False,
                qiling_enable_nurture=True,
                qiling_enable_trial=False,
            ),
            logging.getLogger("test"),
            now_fn=lambda: base_now,
        )
        scheduler = _FakeScheduler()

        async def _send(_plugin: str, _text: str, _reply_to_topic: bool) -> int | None:
            return None

        await plugin.bootstrap(scheduler, _send)
        await scheduler.calls[0][2]()
        await plugin.on_message(
            _ctx("温养器灵需要 灵石x3000 与 养魂木x3。 你当前尚缺：灵石x2977、养魂木x0。")
        )

        state = plugin._artifact_state(ARTIFACT)  # type: ignore[attr-defined]
        self.assertEqual(state["nurture_next_at"], base_now + timedelta(hours=6))
        self.assertIsNone(state["touch_next_at"])
        self.assertIsNone(state["trial_next_at"])

    async def test_touch_cooldown_is_isolated_between_identities(self) -> None:
        base_now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.sqlite3"
            main_store = SQLiteStateStore(str(db_path), account_id="5:main")
            channel_store = SQLiteStateStore(str(db_path), account_id="5:channel_a")
            main = QilingPlugin(
                _dummy_config(
                    qiling_enable_touch=True,
                    qiling_enable_nurture=False,
                    qiling_enable_trial=False,
                    active_identity_key="main",
                ),
                logging.getLogger("test"),
                now_fn=lambda: base_now,
            )
            main.set_state_store(main_store)
            main.restore_state()
            scheduler = _FakeScheduler()

            async def _send(_plugin: str, _text: str, _reply_to_topic: bool) -> int | None:
                return None

            await main.bootstrap(scheduler, _send)
            await scheduler.calls[0][2]()
            await main.on_message(
                _ctx("器灵也是需要休息的，请在 1小时41分钟24秒 后再与它互动。")
            )

            main_reloaded = QilingPlugin(
                _dummy_config(
                    qiling_enable_touch=True,
                    qiling_enable_nurture=False,
                    qiling_enable_trial=False,
                    active_identity_key="main",
                ),
                logging.getLogger("test"),
                now_fn=lambda: base_now,
            )
            main_reloaded.set_state_store(main_store)
            main_reloaded.restore_state()
            main_sends: list[str] = []
            main_scheduler = _FakeScheduler()

            async def _send_main(_plugin: str, text: str, _reply_to_topic: bool) -> int | None:
                main_sends.append(text)
                return None

            await main_reloaded.bootstrap(main_scheduler, _send_main)
            await main_scheduler.calls[0][2]()

            channel = QilingPlugin(
                _dummy_config(
                    qiling_enable_touch=True,
                    qiling_enable_nurture=False,
                    qiling_enable_trial=False,
                    active_identity_key="channel_a",
                ),
                logging.getLogger("test"),
                now_fn=lambda: base_now,
            )
            channel.set_state_store(channel_store)
            channel.restore_state()
            channel_sends: list[str] = []
            channel_scheduler = _FakeScheduler()

            async def _send_channel(_plugin: str, text: str, _reply_to_topic: bool) -> int | None:
                channel_sends.append(text)
                return None

            await channel.bootstrap(channel_scheduler, _send_channel)
            await channel_scheduler.calls[0][2]()

            self.assertEqual(main_sends, [])
            self.assertEqual(channel_sends, [f".抚摸法宝 {ARTIFACT}"])
            main_store.close()
            channel_store.close()


if __name__ == "__main__":
    unittest.main()
