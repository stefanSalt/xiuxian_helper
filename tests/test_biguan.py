import logging
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from xiuxian_bot.config import Config
from xiuxian_bot.core.contracts import MessageContext
from xiuxian_bot.plugins.biguan import AutoBiguanPlugin


def _dummy_config(
    *,
    enable_biguan: bool = True,
    enable_xinggong: bool = False,
    enable_xinggong_deep_biguan: bool = False,
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
        enable_biguan=enable_biguan,
        enable_daily=False,
        enable_garden=False,
        enable_xinggong=enable_xinggong,
        enable_yuanying=False,
        enable_zongmen=False,
        biguan_extra_buffer_seconds=60,
        biguan_cooldown_jitter_min_seconds=5,
        biguan_cooldown_jitter_max_seconds=15,
        biguan_retry_jitter_min_seconds=3,
        biguan_retry_jitter_max_seconds=3,
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
        enable_xinggong_deep_biguan=enable_xinggong_deep_biguan,
    )


class TestBiguanPlugin(unittest.IsolatedAsyncioTestCase):
    def test_disabled_when_xinggong_deep_biguan_enabled(self) -> None:
        plugin = AutoBiguanPlugin(
            _dummy_config(
                enable_biguan=True,
                enable_xinggong=True,
                enable_xinggong_deep_biguan=True,
            ),
            logging.getLogger("test"),
        )
        self.assertFalse(plugin.enabled)

    async def test_reset_cooldown_triggers_immediate_retry(self) -> None:
        plugin = AutoBiguanPlugin(_dummy_config(), logging.getLogger("test"))

        text = (
            "【闭关失败】有侍妾 若兰 在旁护法，为你抚平了部分紊乱的灵力。"
            "【奇遇】你甚至觉得可以立刻再次闭关！你的【闭关修炼】冷却时间被重置了！"
        )
        ctx = MessageContext(
            chat_id=-100,
            message_id=1,
            reply_to_msg_id=123,
            sender_id=999,
            text=text,
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )
        actions = await plugin.on_message(ctx)
        assert actions is not None
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].text, ".闭关修炼")
        self.assertEqual(actions[0].key, "biguan.next")
        self.assertEqual(actions[0].delay_seconds, 3)

    async def test_retries_when_feedback_missing_for_15_minutes(self) -> None:
        plugin = AutoBiguanPlugin(_dummy_config(), logging.getLogger("test"))
        scheduled: list[tuple[str, float, object]] = []
        send_calls: list[tuple[str, str, bool]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                scheduled.append((key, delay_seconds, action))

        async def _send(plugin_name: str, text: str, reply_to_topic: bool) -> int | None:
            send_calls.append((plugin_name, text, reply_to_topic))
            return 1

        await plugin.bootstrap(_FakeScheduler(), _send)
        await plugin._run_next()  # type: ignore[attr-defined]

        self.assertEqual(send_calls, [("biguan", ".闭关修炼", True)])
        timeout_key, _, timeout_action = next(
            entry for entry in scheduled if entry[0].startswith("biguan.feedback_timeout:")
        )
        self.assertTrue(timeout_key.startswith("biguan.feedback_timeout:"))

        expected_deadline = plugin._pending_feedback_deadline_at  # type: ignore[attr-defined]
        assert expected_deadline is not None
        with patch("xiuxian_bot.plugins.biguan.datetime", wraps=datetime) as mock_datetime:
            mock_datetime.now.return_value = expected_deadline + timedelta(seconds=1)
            await timeout_action()

        self.assertEqual(len(send_calls), 2)
        self.assertEqual(send_calls[-1], ("biguan", ".闭关修炼", True))

    async def test_valid_feedback_clears_watchdog_and_blocks_stale_retry(self) -> None:
        plugin = AutoBiguanPlugin(_dummy_config(), logging.getLogger("test"))
        scheduled: list[tuple[str, float, object]] = []
        send_calls: list[tuple[str, str, bool]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                scheduled.append((key, delay_seconds, action))

        async def _send(plugin_name: str, text: str, reply_to_topic: bool) -> int | None:
            send_calls.append((plugin_name, text, reply_to_topic))
            return 1

        await plugin.bootstrap(_FakeScheduler(), _send)
        await plugin._run_next()  # type: ignore[attr-defined]

        expected_deadline = plugin._pending_feedback_deadline_at  # type: ignore[attr-defined]
        assert expected_deadline is not None
        _, _, timeout_action = next(
            entry for entry in scheduled if entry[0].startswith("biguan.feedback_timeout:")
        )

        ctx = MessageContext(
            chat_id=-100,
            message_id=2,
            reply_to_msg_id=123,
            sender_id=999,
            text="@Me 打坐调息 10 分钟",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )
        await plugin.on_message(ctx)
        self.assertIsNone(plugin._pending_feedback_deadline_at)  # type: ignore[attr-defined]

        with patch("xiuxian_bot.plugins.biguan.datetime", wraps=datetime) as mock_datetime:
            mock_datetime.now.return_value = expected_deadline + timedelta(seconds=1)
            await timeout_action()

        self.assertEqual(len(send_calls), 1)


if __name__ == "__main__":
    unittest.main()
