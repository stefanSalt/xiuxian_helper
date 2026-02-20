import logging
import unittest
from datetime import datetime, timezone

from xiuxian_bot.config import Config
from xiuxian_bot.core.contracts import MessageContext
from xiuxian_bot.plugins.biguan import AutoBiguanPlugin


def _dummy_config(*, enable_biguan: bool = True) -> Config:
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
        enable_xinggong=False,
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
        zongmen_cmd_dianmao=".宗门点卯",
        zongmen_cmd_chuangong=".宗门传功",
        zongmen_dianmao_time=None,
        zongmen_chuangong_times=None,
        zongmen_chuangong_xinde_text="宗门传功",
        zongmen_catch_up=True,
        zongmen_action_spacing_seconds=20,
    )


class TestBiguanPlugin(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()

