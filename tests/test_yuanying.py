import logging
import unittest
from datetime import datetime, timedelta, timezone

from xiuxian_bot.config import Config
from xiuxian_bot.core.contracts import MessageContext
from xiuxian_bot.plugins.yuanying import AutoYuanyingPlugin


def _dummy_config(*, enable_yuanying: bool = True) -> Config:
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
        zongmen_cmd_dianmao=".宗门点卯",
        zongmen_cmd_chuangong=".宗门传功",
        zongmen_dianmao_time=None,
        zongmen_chuangong_times=None,
        zongmen_chuangong_xinde_text="宗门传功",
        zongmen_catch_up=True,
        zongmen_action_spacing_seconds=20,
    )


class TestYuanyingPlugin(unittest.IsolatedAsyncioTestCase):
    async def test_bootstrap_schedules_two_loops(self) -> None:
        plugin = AutoYuanyingPlugin(_dummy_config(), logging.getLogger("test"))
        calls: list[tuple[str, float]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                calls.append((key, delay_seconds))

        async def _send(_plugin: str, _text: str, _reply_to_topic: bool) -> int | None:
            return None

        await plugin.bootstrap(_FakeScheduler(), _send)
        self.assertIn(("yuanying.liefeng.loop", 0.0), calls)
        self.assertIn(("yuanying.chuqiao.loop", 0.0), calls)

    async def test_liefeng_cooldown_updates_next_time(self) -> None:
        plugin = AutoYuanyingPlugin(_dummy_config(), logging.getLogger("test"))
        start = datetime.now()
        ctx = MessageContext(
            chat_id=-100,
            message_id=1,
            reply_to_msg_id=10,
            sender_id=999,
            text="空间裂缝尚未稳定，其中的空间风暴仍在肆虐。请在11小时58分钟35秒后再行探寻。",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )
        await plugin.on_message(ctx)
        blocked_until = getattr(plugin, "_liefeng_blocked_until")
        self.assertIsNotNone(blocked_until)
        delta = (blocked_until - start).total_seconds()
        self.assertGreater(delta, 11 * 3600)
        self.assertLess(delta, 12 * 3600 + 60)

    async def test_liefeng_failure_retries_quickly(self) -> None:
        plugin = AutoYuanyingPlugin(_dummy_config(), logging.getLogger("test"))
        ctx = MessageContext(
            chat_id=-100,
            message_id=2,
            reply_to_msg_id=11,
            sender_id=999,
            text="【遭遇风暴】空间裂缝中风暴肆虐，你的元婴受创，被迫逃回！",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )
        actions = await plugin.on_message(ctx)
        assert actions is not None
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].text, ".探寻裂缝")
        self.assertEqual(actions[0].delay_seconds, 5.0)

    async def test_chuqiao_reply_syncs_next_run(self) -> None:
        plugin = AutoYuanyingPlugin(_dummy_config(), logging.getLogger("test"))
        start = datetime.now()
        ctx = MessageContext(
            chat_id=-100,
            message_id=3,
            reply_to_msg_id=12,
            sender_id=999,
            text="你心念一动，丹田中的元婴化作一道流光飞出，消失在天际。它将在外云游8小时，为你寻觅天地奇珍。下一次发言时若已归来，将自动结算收获。",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )
        await plugin.on_message(ctx)
        blocked_until = getattr(plugin, "_chuqiao_blocked_until")
        self.assertIsNotNone(blocked_until)
        delta = (blocked_until - start).total_seconds()
        self.assertGreater(delta, 8 * 3600 - 5)
        self.assertLess(delta, 8 * 3600 + 10)

    async def test_liefeng_weakness_waits_for_recovery(self) -> None:
        plugin = AutoYuanyingPlugin(_dummy_config(), logging.getLogger("test"))
        start = datetime.now()
        ctx = MessageContext(
            chat_id=-100,
            message_id=4,
            reply_to_msg_id=13,
            sender_id=999,
            text="【元婴遁逃·虚弱】千钧一发之际，你的元婴带着你的三魂七魄，从破碎的肉身中遁出！但你的神魂遭受重创，已陷入6小时的【虚弱期】！",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )
        await plugin.on_message(ctx)
        blocked_until = getattr(plugin, "_liefeng_blocked_until")
        self.assertIsNotNone(blocked_until)
        delta = (blocked_until - start).total_seconds()
        self.assertGreater(delta, 6 * 3600)
        self.assertLess(delta, 6 * 3600 + 15)

    async def test_chuqiao_busy_reply_does_not_reset_wait_time(self) -> None:
        plugin = AutoYuanyingPlugin(_dummy_config(), logging.getLogger("test"))
        original = datetime.now() + timedelta(hours=3)
        plugin._chuqiao_blocked_until = original  # type: ignore[attr-defined]
        ctx = MessageContext(
            chat_id=-100,
            message_id=5,
            reply_to_msg_id=14,
            sender_id=999,
            text='你的元婴正在执行"元神出窍"任务，无法分身。请先使用.元婴归窍将其召回。',
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )
        actions = await plugin.on_message(ctx)
        self.assertIsNone(actions)
        self.assertEqual(getattr(plugin, "_chuqiao_blocked_until"), original)


if __name__ == "__main__":
    unittest.main()
