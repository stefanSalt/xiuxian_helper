import asyncio
import logging
import unittest
from datetime import datetime, timedelta, timezone

from xiuxian_bot.config import Config
from xiuxian_bot.core.contracts import MessageContext
from xiuxian_bot.core.scheduler import Scheduler
from xiuxian_bot.plugins.chuangta import AutoChuangtaPlugin


def _dummy_config(
    *,
    enable_chuangta: bool = True,
    enable_yuanying: bool = False,
    chuangta_time: str = "14:15",
) -> Config:
    return Config(
        tg_api_id=1,
        tg_api_hash="hash",
        tg_session_name="session",
        game_chat_id=-100,
        topic_id=123,
        my_name="Me",
        send_to_topic=True,
        action_cmd_biguan=".闭关修炼",
        dry_run=False,
        log_level="INFO",
        global_sends_per_minute=999,
        plugin_sends_per_minute=999,
        enable_biguan=False,
        enable_daily=False,
        enable_garden=False,
        enable_xinggong=False,
        enable_yuanying=enable_yuanying,
        enable_zongmen=False,
        biguan_extra_buffer_seconds=60,
        biguan_cooldown_jitter_min_seconds=5,
        biguan_cooldown_jitter_max_seconds=15,
        biguan_retry_jitter_min_seconds=3,
        biguan_retry_jitter_max_seconds=8,
        garden_seed_name="清灵草种子",
        garden_poll_interval_seconds=3600,
        garden_action_spacing_seconds=25,
        xinggong_star_name="庚金星",
        xinggong_poll_interval_seconds=3600,
        xinggong_action_spacing_seconds=25,
        xinggong_qizhen_start_time="07:00",
        xinggong_qizhen_retry_interval_seconds=120,
        xinggong_qizhen_second_offset_seconds=43500,
        xinggong_wenan_interval_seconds=43200,
        yuanying_liefeng_interval_seconds=43200,
        yuanying_chuqiao_interval_seconds=28800,
        zongmen_cmd_dianmao=".宗门点卯",
        zongmen_cmd_chuangong=".宗门传功",
        zongmen_dianmao_time=None,
        zongmen_chuangong_times=None,
        zongmen_chuangong_xinde_text="宗门传功",
        zongmen_catch_up=True,
        zongmen_action_spacing_seconds=20,
        enable_chuangta=enable_chuangta,
        chuangta_time=chuangta_time,
    )


class TestChuangtaPlugin(unittest.IsolatedAsyncioTestCase):
    async def test_bootstrap_catchup_without_yuanying_sends_chuangta(self) -> None:
        logger = logging.getLogger("test")
        scheduler = Scheduler(logger)
        plugin = AutoChuangtaPlugin(
            _dummy_config(enable_yuanying=False, chuangta_time="00:00"),
            logger,
        )

        calls: list[str] = []

        async def fake_send(_plugin: str, text: str, _reply_to_topic: bool) -> int | None:
            calls.append(text)
            return 100

        await plugin.bootstrap(scheduler, fake_send)
        await asyncio.sleep(0.1)
        await scheduler.cancel_all()

        self.assertIn(".闯塔", calls)

    async def test_bootstrap_catchup_with_yuanying_requests_status_first(self) -> None:
        logger = logging.getLogger("test")
        scheduler = Scheduler(logger)
        plugin = AutoChuangtaPlugin(
            _dummy_config(enable_yuanying=True, chuangta_time="00:00"),
            logger,
        )

        calls: list[str] = []

        async def fake_send(_plugin: str, text: str, _reply_to_topic: bool) -> int | None:
            calls.append(text)
            return 101

        await plugin.bootstrap(scheduler, fake_send)
        await asyncio.sleep(0.1)
        await scheduler.cancel_all()

        self.assertIn(".元婴状态", calls)
        self.assertNotIn(".闯塔", calls)

    async def test_wenyang_status_triggers_chuangta(self) -> None:
        plugin = AutoChuangtaPlugin(
            _dummy_config(enable_yuanying=True),
            logging.getLogger("test"),
        )
        plugin._current_day = datetime.now().date()  # type: ignore[attr-defined]
        plugin._pending_today = True  # type: ignore[attr-defined]
        plugin._status_requested_at = datetime.now()  # type: ignore[attr-defined]
        plugin._status_request_msg_id = 200  # type: ignore[attr-defined]

        ctx = MessageContext(
            chat_id=-100,
            message_id=201,
            reply_to_msg_id=200,
            sender_id=999,
            text="你的本命元婴 等级: 8 级 经验: 1086 / 4000 五行: 风 状态: 窍中温养 使用 .元婴出窍 或 .元婴闭关 派遣元婴。",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )
        actions = await plugin.on_message(ctx)

        assert actions is not None
        self.assertEqual([a.text for a in actions], [".闯塔"])

    async def test_out_of_body_status_keeps_waiting(self) -> None:
        plugin = AutoChuangtaPlugin(
            _dummy_config(enable_yuanying=True),
            logging.getLogger("test"),
        )
        plugin._current_day = datetime.now().date()  # type: ignore[attr-defined]
        plugin._pending_today = True  # type: ignore[attr-defined]
        plugin._status_requested_at = datetime.now()  # type: ignore[attr-defined]
        plugin._status_request_msg_id = 300  # type: ignore[attr-defined]

        ctx = MessageContext(
            chat_id=-100,
            message_id=301,
            reply_to_msg_id=300,
            sender_id=999,
            text="【元婴状态】状态:元神出窍 归来倒计时:6小时50分钟30秒",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )
        actions = await plugin.on_message(ctx)

        self.assertIsNone(actions)
        self.assertTrue(getattr(plugin, "_pending_today"))

    async def test_summary_after_wait_triggers_chuangta(self) -> None:
        plugin = AutoChuangtaPlugin(
            _dummy_config(enable_yuanying=True),
            logging.getLogger("test"),
        )
        plugin._current_day = datetime.now().date()  # type: ignore[attr-defined]
        plugin._pending_today = True  # type: ignore[attr-defined]
        plugin._yuanying_out_of_body = True  # type: ignore[attr-defined]

        ctx = MessageContext(
            chat_id=-100,
            message_id=401,
            reply_to_msg_id=400,
            sender_id=999,
            text="📜 修士 @Me 元神归窍总结 你的元婴在虚空中神游八小时，带回了以下收获：",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )
        actions = await plugin.on_message(ctx)

        assert actions is not None
        self.assertEqual([a.text for a in actions], [".闯塔"])

    async def test_unknown_yuanying_reply_falls_back_to_chuangta(self) -> None:
        plugin = AutoChuangtaPlugin(
            _dummy_config(enable_yuanying=True),
            logging.getLogger("test"),
        )
        plugin._current_day = datetime.now().date()  # type: ignore[attr-defined]
        plugin._pending_today = True  # type: ignore[attr-defined]
        plugin._status_requested_at = datetime.now()  # type: ignore[attr-defined]
        plugin._status_request_msg_id = 500  # type: ignore[attr-defined]

        ctx = MessageContext(
            chat_id=-100,
            message_id=501,
            reply_to_msg_id=500,
            sender_id=999,
            text="你尚未凝结元婴，暂时无法查看元婴状态。",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )
        actions = await plugin.on_message(ctx)

        assert actions is not None
        self.assertEqual([a.text for a in actions], [".闯塔"])

    async def test_status_timeout_falls_back_to_chuangta(self) -> None:
        plugin = AutoChuangtaPlugin(
            _dummy_config(enable_yuanying=True),
            logging.getLogger("test"),
        )
        calls: list[str] = []

        async def fake_send(_plugin: str, text: str, _reply_to_topic: bool) -> int | None:
            calls.append(text)
            return 600

        plugin._send = fake_send  # type: ignore[attr-defined]
        plugin._current_day = datetime.now().date()  # type: ignore[attr-defined]
        plugin._pending_today = True  # type: ignore[attr-defined]
        plugin._status_requested_at = datetime.now() - timedelta(seconds=220)  # type: ignore[attr-defined]
        await plugin._status_timeout_loop()  # type: ignore[attr-defined]

        self.assertEqual(calls, [".闯塔"])

    async def test_manual_chuangta_feedback_marks_done(self) -> None:
        plugin = AutoChuangtaPlugin(_dummy_config(), logging.getLogger("test"))
        plugin._current_day = datetime.now().date()  # type: ignore[attr-defined]

        ctx = MessageContext(
            chat_id=-100,
            message_id=701,
            reply_to_msg_id=700,
            sender_id=999,
            text="【琉璃问心塔】 你深吸一口气，踏入了古塔的第 1 层。",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )
        await plugin.on_message(ctx)

        self.assertTrue(getattr(plugin, "_done_today"))


if __name__ == "__main__":
    unittest.main()
