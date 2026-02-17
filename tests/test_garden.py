import logging
import unittest
from datetime import datetime, timezone

from xiuxian_bot.config import Config
from xiuxian_bot.core.contracts import MessageContext
from xiuxian_bot.domain.garden import parse_garden_status
from xiuxian_bot.plugins.garden import AutoGardenPlugin


def _dummy_config(*, enable_garden: bool = True) -> Config:
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
        global_sends_per_minute=6,
        plugin_sends_per_minute=3,
        enable_biguan=False,
        enable_daily=False,
        enable_garden=enable_garden,
        enable_xinggong=False,
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
        zongmen_cmd_dianmao="宗门点卯",
        zongmen_cmd_chuangong="宗门传功",
        zongmen_dianmao_time=None,
        zongmen_chuangong_times=None,
        zongmen_chuangong_xinde_text="今日修行心得：稳中求进。",
        zongmen_catch_up=True,
        zongmen_action_spacing_seconds=20,
    )


class TestGardenParser(unittest.TestCase):
    def test_parse_garden_status_rejects_unrelated(self) -> None:
        self.assertIsNone(parse_garden_status("hello world"))

    def test_parse_garden_status_flags(self) -> None:
        text = """【黄枫谷·小药园】(灵田总数: 3块)
1号灵田: 清灵草种子-生长中 🌱 (剩余: 5小时26分钟47秒)
2号灵田: 清灵草种子-害虫侵扰 🐛
3号灵田: 清灵草种子-已成熟 ✨
"""
        status = parse_garden_status(text)
        assert status is not None
        self.assertTrue(status.has_growing)
        self.assertTrue(status.has_insect)
        self.assertTrue(status.has_mature)
        self.assertFalse(status.has_idle)
        self.assertEqual(status.min_remaining_seconds, 19607)

    def test_parse_garden_status_idle(self) -> None:
        text = """【黄枫谷·小药园】(灵田总数: 3块)
1号灵田: 空闲
2号灵田: 空闲
3号灵田: 清灵草种子-生长中 🌱 (剩余: 1小时)
"""
        status = parse_garden_status(text)
        assert status is not None
        self.assertTrue(status.has_idle)
        self.assertTrue(status.has_growing)
        self.assertFalse(status.has_mature)
        self.assertEqual(status.min_remaining_seconds, 3600)


class TestGardenPlugin(unittest.IsolatedAsyncioTestCase):
    async def test_status_schedules_poll_near_maturity(self) -> None:
        plugin = AutoGardenPlugin(_dummy_config(), logging.getLogger("test"))

        text = """【黄枫谷·小药园】(灵田总数: 3块)
1号灵田: 清灵草种子-生长中 🌱 (剩余: 14分钟)
2号灵田: 清灵草种子-生长中 🌱 (剩余: 2小时)
3号灵田: 清灵草种子-生长中 🌱 (剩余: 3小时)
"""
        ctx = MessageContext(
            chat_id=-100,
            message_id=9,
            reply_to_msg_id=123,
            sender_id=999,
            text=text,
            ts=datetime.now(timezone.utc),
            is_reply=False,
            is_reply_to_me=False,
        )

        actions = await plugin.on_message(ctx)
        assert actions is not None

        self.assertEqual(actions[0].text, ".小药园")
        self.assertEqual(actions[0].key, "garden.poll")
        # 14 minutes + buffer (10s) should be earlier than the base poll (3600s).
        self.assertEqual(actions[0].delay_seconds, 850.0)

    async def test_status_schedules_maintenance_and_harvest(self) -> None:
        plugin = AutoGardenPlugin(_dummy_config(), logging.getLogger("test"))

        text = """【黄枫谷·小药园】(灵田总数: 3块)
1号灵田: 清灵草种子-杂草横生 🌿
2号灵田: 清灵草种子-害虫侵扰 🐛
3号灵田: 清灵草种子-灵气干涸 🍂 已成熟 ✨
"""
        ctx = MessageContext(
            chat_id=-100,
            message_id=1,
            reply_to_msg_id=123,
            sender_id=999,
            text=text,
            ts=datetime.now(timezone.utc),
            is_reply=False,
            is_reply_to_me=False,
        )

        actions = await plugin.on_message(ctx)
        assert actions is not None

        # Always schedules the next poll.
        self.assertEqual(actions[0].text, ".小药园")
        self.assertEqual(actions[0].key, "garden.poll")
        self.assertEqual(actions[0].delay_seconds, 3600.0)

        # Then actions are spaced to respect rate limits.
        self.assertEqual([a.text for a in actions[1:]], [".除虫", ".除草", ".浇水", ".采药"])
        self.assertEqual([a.delay_seconds for a in actions[1:]], [0.0, 25.0, 50.0, 75.0])

        # No sow on the same tick when there are mature crops (sow happens after harvest reply).
        self.assertNotIn(".播种", " ".join(a.text for a in actions))

    async def test_status_sows_when_idle_and_no_mature(self) -> None:
        plugin = AutoGardenPlugin(_dummy_config(), logging.getLogger("test"))

        text = """【黄枫谷·小药园】(灵田总数: 3块)
1号灵田: 空闲
2号灵田: 清灵草种子-生长中 🌱 (剩余: 1小时)
3号灵田: 清灵草种子-生长中 🌱 (剩余: 2小时)
"""
        ctx = MessageContext(
            chat_id=-100,
            message_id=2,
            reply_to_msg_id=123,
            sender_id=999,
            text=text,
            ts=datetime.now(timezone.utc),
            is_reply=False,
            is_reply_to_me=False,
        )

        actions = await plugin.on_message(ctx)
        assert actions is not None
        self.assertEqual(actions[-1].text, ".播种 清灵草种子")

    async def test_harvest_reply_triggers_sow(self) -> None:
        plugin = AutoGardenPlugin(_dummy_config(), logging.getLogger("test"))

        ctx = MessageContext(
            chat_id=-100,
            message_id=3,
            reply_to_msg_id=123,
            sender_id=999,
            text="一键采药完成！你从 11 块灵田中总计收获了：【凝血草】x25！",
            ts=datetime.now(timezone.utc),
            is_reply=False,
            is_reply_to_me=False,
        )

        actions = await plugin.on_message(ctx)
        assert actions is not None
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].text, ".播种 清灵草种子")
        self.assertEqual(actions[0].delay_seconds, 25.0)
