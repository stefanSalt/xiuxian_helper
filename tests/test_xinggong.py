import logging
import unittest
from datetime import datetime, timedelta, timezone

from xiuxian_bot.config import Config
from xiuxian_bot.core.contracts import MessageContext
from xiuxian_bot.domain.xinggong import parse_xinggong_observatory
from xiuxian_bot.plugins.xinggong import AutoXinggongPlugin


def _dummy_config(*, enable_xinggong: bool = True) -> Config:
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
        enable_xinggong=enable_xinggong,
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
        zongmen_chuangong_xinde_text="今日修行心得：稳中求进。",
        zongmen_catch_up=True,
        zongmen_action_spacing_seconds=20,
    )


class TestXinggongParser(unittest.TestCase):
    def test_parse_observatory_idle(self) -> None:
        text = """【星宫 · 观星台】 (引星盘总数: 3座)
1号引星盘: 空闲
2号引星盘: 空闲
3号引星盘: 空闲
使用.牵引星辰 <引星盘> <星辰>来凝聚资源。
"""
        status = parse_xinggong_observatory(text)
        assert status is not None
        self.assertEqual(status.total_disks, 3)
        self.assertEqual(status.idle_disks, [1, 2, 3])
        self.assertEqual(status.abnormal_disks, [])
        self.assertIsNone(status.min_remaining_seconds)

    def test_parse_observatory_remaining_and_abnormal(self) -> None:
        text = """【星宫·观星台】 (引[星盘总数:8座)
1号引星盘:天雷星-凝聚中 (剩余:5小时38分钟56秒)
2号引[星盘:天雷星-凝聚中 (剩余:5小时38分钟56秒)
3号引星盘:天雷星-凝聚中  (剩余:5小时38分钟56秒)
4号引星盘:天雷星-凝聚中(剩余:5小时38分钟56秒)
5号引星盘:天雷星－元磁紊乱
6号引星盘:天雷星-凝聚中(剩余:5小时38分钟56秒)
7号引星盘：天雷星·星光黯淡！
8号引星盘:天雷星-凝聚中(剩余:5小时38分钟56秒)
使用.牵引星辰<引星盘><星辰>来凝聚资源。
"""
        status = parse_xinggong_observatory(text)
        assert status is not None
        self.assertEqual(status.total_disks, 8)
        self.assertEqual(status.abnormal_disks, [5, 7])
        self.assertEqual(status.min_remaining_seconds, 20336)


class TestXinggongPlugin(unittest.IsolatedAsyncioTestCase):
    async def test_bootstrap_schedules_wenan_loop(self) -> None:
        plugin = AutoXinggongPlugin(_dummy_config(), logging.getLogger("test"))

        calls: list[tuple[str, float]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                calls.append((key, delay_seconds))

        async def _send(_plugin: str, _text: str, _reply_to_topic: bool) -> int | None:
            return None

        await plugin.bootstrap(_FakeScheduler(), _send)
        keys = {k for k, _ in calls}
        self.assertIn("xinggong.qizhen.loop", keys)
        self.assertIn("xinggong.wenan.loop", keys)

    async def test_status_sows_when_idle(self) -> None:
        plugin = AutoXinggongPlugin(_dummy_config(), logging.getLogger("test"))

        text = """【星宫 · 观星台】 (引星盘总数: 3座)
1号引星盘: 空闲
2号引星盘: 空闲
3号引星盘: 空闲
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

        self.assertEqual(actions[0].text, ".观星台")
        self.assertEqual(actions[0].key, "xinggong.poll")
        self.assertEqual(actions[0].delay_seconds, 3600.0)
        self.assertEqual([a.text for a in actions[1:]], [".牵引星辰 庚金星"])
        self.assertEqual([a.delay_seconds for a in actions[1:]], [0.0])

    async def test_status_abnormal_triggers_soothe(self) -> None:
        plugin = AutoXinggongPlugin(_dummy_config(), logging.getLogger("test"))

        text = """【星宫·观星台】 (引星盘总数: 2座)
1号引星盘: 庚金星－元磁紊乱
2号引星盘: 空闲
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
        self.assertEqual(actions[1].text, ".安抚星辰")

    async def test_qizhen_success_via_invite_edit(self) -> None:
        plugin = AutoXinggongPlugin(_dummy_config(), logging.getLogger("test"))
        # Mimic the runtime: cycle is already initialized by the scheduled loop before messages arrive.
        now = datetime.now()
        setattr(plugin, "_cycle_date", plugin._cycle_date_for(now))  # type: ignore[attr-defined]
        setattr(plugin, "_qizhen_pending_slot", 1)

        invite = MessageContext(
            chat_id=-100,
            message_id=10,
            reply_to_msg_id=123,
            sender_id=999,
            text="【周天星斗大阵-启】\n【星宫】弟子 @Me 正在布设大阵，尚需1 位同门相助!",
            ts=datetime.now(timezone.utc),
            is_reply=False,
            is_reply_to_me=False,
        )
        await plugin.on_message(invite)
        self.assertEqual(getattr(plugin, "_qizhen_last_invite_msg_id"), 10)

        success = MessageContext(
            chat_id=-100,
            message_id=10,
            reply_to_msg_id=123,
            sender_id=999,
            text="【周天星斗大阵-成】星光汇聚，大阵已成!",
            ts=datetime.now(timezone.utc),
            is_reply=False,
            is_reply_to_me=False,
        )
        await plugin.on_message(success)
        self.assertIsNotNone(getattr(plugin, "_qizhen_first_success_at"))

    async def test_qizhen_cooldown_reply_updates_blocked_until(self) -> None:
        plugin = AutoXinggongPlugin(_dummy_config(), logging.getLogger("test"))

        start = datetime.now()
        ctx = MessageContext(
            chat_id=-100,
            message_id=20,
            reply_to_msg_id=19,
            sender_id=999,
            text="你刚刚参与过布阵，心神消耗巨大，请在1小时2分钟3秒后再次启阵。",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )
        await plugin.on_message(ctx)
        blocked_until = getattr(plugin, "_qizhen_blocked_until")
        self.assertIsNotNone(blocked_until)
        delta = (blocked_until - start).total_seconds()
        # Remaining seconds (3723) + buffer (>=5s) with a bit of timing slack.
        self.assertGreater(delta, 3723)
        self.assertLess(delta, 3800)

    async def test_qizhen_loop_respects_blocked_until(self) -> None:
        plugin = AutoXinggongPlugin(_dummy_config(), logging.getLogger("test"))

        scheduled: list[tuple[str, float]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                scheduled.append((key, delay_seconds))

        sends: list[str] = []

        async def _send(_plugin: str, text: str, _reply_to_topic: bool) -> int | None:
            sends.append(text)
            return None

        plugin._scheduler = _FakeScheduler()  # type: ignore[attr-defined]
        plugin._send = _send  # type: ignore[attr-defined]
        plugin._qizhen_blocked_until = datetime.now() + timedelta(hours=10)  # type: ignore[attr-defined]

        await plugin._qizhen_loop()  # type: ignore[attr-defined]
        self.assertEqual(sends, [])
        self.assertTrue(scheduled)
        key, delay = scheduled[-1]
        self.assertEqual(key, "xinggong.qizhen.loop")
        self.assertGreater(delay, 9 * 3600)


if __name__ == "__main__":
    unittest.main()
