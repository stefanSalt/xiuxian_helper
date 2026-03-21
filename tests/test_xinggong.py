import logging
import unittest
from datetime import datetime, timedelta, timezone

from xiuxian_bot.config import Config
from xiuxian_bot.core.contracts import MessageContext
from xiuxian_bot.domain.xinggong import parse_xinggong_observatory
from xiuxian_bot.plugins.xinggong import AutoXinggongPlugin


def _dummy_config(
    *,
    enable_xinggong: bool = True,
    enable_xinggong_wenan: bool = True,
    enable_xinggong_deep_biguan: bool = False,
    enable_xinggong_guanxing: bool = False,
    xinggong_wenan_interval_seconds: int = 43200,
    xinggong_qizhen_start_time: str = "07:00",
    xinggong_guanxing_target_username: str = "salt9527",
    xinggong_guanxing_preview_advance_seconds: int = 180,
    xinggong_guanxing_shift_advance_seconds: int = 1,
    xinggong_guanxing_watch_events: str = "星辰异象,地磁暴动",
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
        enable_xinggong=enable_xinggong,
        enable_yuanying=False,
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
        xinggong_qizhen_start_time=xinggong_qizhen_start_time,
        xinggong_qizhen_retry_interval_seconds=120,
        xinggong_qizhen_second_offset_seconds=43500,
        xinggong_wenan_interval_seconds=xinggong_wenan_interval_seconds,
        yuanying_liefeng_interval_seconds=43200,
        yuanying_chuqiao_interval_seconds=28800,
        zongmen_cmd_dianmao=".宗门点卯",
        zongmen_cmd_chuangong=".宗门传功",
        zongmen_dianmao_time=None,
        zongmen_chuangong_times=None,
        zongmen_chuangong_xinde_text="今日修行心得：稳中求进。",
        zongmen_catch_up=True,
        zongmen_action_spacing_seconds=20,
        enable_xinggong_wenan=enable_xinggong_wenan,
        enable_xinggong_deep_biguan=enable_xinggong_deep_biguan,
        enable_xinggong_guanxing=enable_xinggong_guanxing,
        xinggong_guanxing_target_username=xinggong_guanxing_target_username,
        xinggong_guanxing_preview_advance_seconds=xinggong_guanxing_preview_advance_seconds,
        xinggong_guanxing_shift_advance_seconds=xinggong_guanxing_shift_advance_seconds,
        xinggong_guanxing_watch_events=xinggong_guanxing_watch_events,
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

    async def test_bootstrap_skips_wenan_loop_when_disabled(self) -> None:
        plugin = AutoXinggongPlugin(
            _dummy_config(enable_xinggong_wenan=False),
            logging.getLogger("test"),
        )

        calls: list[tuple[str, float]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                calls.append((key, delay_seconds))

        async def _send(_plugin: str, _text: str, _reply_to_topic: bool) -> int | None:
            return None

        await plugin.bootstrap(_FakeScheduler(), _send)
        keys = {k for k, _ in calls}
        self.assertIn("xinggong.qizhen.loop", keys)
        self.assertNotIn("xinggong.wenan.loop", keys)

    async def test_wenan_loop_uses_configured_interval(self) -> None:
        plugin = AutoXinggongPlugin(
            _dummy_config(xinggong_wenan_interval_seconds=777),
            logging.getLogger("test"),
        )

        calls: list[tuple[str, float]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                calls.append((key, delay_seconds))

        async def _send(_plugin: str, _text: str, _reply_to_topic: bool) -> int | None:
            return None

        plugin._scheduler = _FakeScheduler()  # type: ignore[attr-defined]
        plugin._send = _send  # type: ignore[attr-defined]
        await plugin._wenan_loop()  # type: ignore[attr-defined]
        self.assertIn(("xinggong.wenan.loop", 777.0), calls)

    async def test_high_value_preview_schedules_preview_and_shift(self) -> None:
        plugin = AutoXinggongPlugin(
            _dummy_config(enable_xinggong_guanxing=True),
            logging.getLogger("test"),
        )

        scheduled: list[tuple[str, float]] = []
        sends: list[tuple[str, str, bool, int | None]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                scheduled.append((key, delay_seconds))

        async def _send(_plugin: str, text: str, reply_to_topic: bool, *, reply_to_msg_id=None):
            sends.append((_plugin, text, reply_to_topic, reply_to_msg_id))
            return 7001

        plugin._scheduler = _FakeScheduler()  # type: ignore[attr-defined]
        plugin._send = _send  # type: ignore[attr-defined]
        plugin._should_ignore_external_guanxing_preview = lambda _now: False  # type: ignore[attr-defined]

        ctx = MessageContext(
            chat_id=-100,
            message_id=1001,
            reply_to_msg_id=123,
            sender_id=999,
            text="【星盘显化】@intoso 闭目凝神，推演天机...星盘之上，天机已然显现！\n下一次天道演化将是：【Good·星辰异象】\n当前天命所归：@mutourenazz",
            ts=datetime.now(timezone.utc),
            is_reply=False,
            is_reply_to_me=False,
        )
        actions = await plugin.on_message(ctx)

        self.assertIsNone(actions)
        self.assertEqual(sends, [])
        keys = {key for key, _ in scheduled}
        self.assertIn("xinggong.guanxing.preview", keys)
        self.assertIn("xinggong.guanxing.shift", keys)
        self.assertIsNone(getattr(plugin, "_guanxing_own_command_msg_id"))
        self.assertTrue(getattr(plugin, "_guanxing_claim_active"))

    async def test_guanxing_preview_loop_sends_command(self) -> None:
        plugin = AutoXinggongPlugin(
            _dummy_config(enable_xinggong_guanxing=True),
            logging.getLogger("test"),
        )

        sends: list[tuple[str, str, bool, int | None]] = []

        async def _send(_plugin: str, text: str, reply_to_topic: bool, *, reply_to_msg_id=None):
            sends.append((_plugin, text, reply_to_topic, reply_to_msg_id))
            return 7001

        plugin._send = _send  # type: ignore[attr-defined]
        plugin._guanxing_claim_active = True  # type: ignore[attr-defined]
        plugin._guanxing_settlement_at = datetime.now() + timedelta(minutes=3)  # type: ignore[attr-defined]

        await plugin._send_guanxing_preview()  # type: ignore[attr-defined]

        self.assertEqual(sends, [("xinggong", ".观星", True, None)])
        self.assertEqual(getattr(plugin, "_guanxing_own_command_msg_id"), 7001)
        self.assertTrue(getattr(plugin, "_guanxing_preview_sent"))

    async def test_personal_preview_reply_becomes_shift_reply_target(self) -> None:
        plugin = AutoXinggongPlugin(
            _dummy_config(enable_xinggong_guanxing=True),
            logging.getLogger("test"),
        )
        now = datetime.now()
        plugin._guanxing_settlement_at = now + timedelta(minutes=1)  # type: ignore[attr-defined]
        plugin._guanxing_claim_active = True  # type: ignore[attr-defined]
        plugin._guanxing_own_command_msg_id = 8001  # type: ignore[attr-defined]

        ctx = MessageContext(
            chat_id=-100,
            message_id=8002,
            reply_to_msg_id=8001,
            sender_id=999,
            text="【星盘显化】@Me 闭目凝神，推演天机...星盘之上，天机已然显现！\n下一次天道演化将是：【Good·星辰异象】\n当前天命所归：@someone",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )
        actions = await plugin.on_message(ctx)

        self.assertIsNone(actions)
        self.assertEqual(getattr(plugin, "_guanxing_own_preview_msg_id"), 8002)

    async def test_shift_send_replies_to_personal_preview_message(self) -> None:
        plugin = AutoXinggongPlugin(
            _dummy_config(enable_xinggong_guanxing=True),
            logging.getLogger("test"),
        )

        sends: list[tuple[str, str, bool, int | None]] = []

        async def _send(_plugin: str, text: str, reply_to_topic: bool, *, reply_to_msg_id=None):
            sends.append((_plugin, text, reply_to_topic, reply_to_msg_id))
            return 9001

        plugin._send = _send  # type: ignore[attr-defined]
        plugin._guanxing_claim_active = True  # type: ignore[attr-defined]
        plugin._guanxing_settlement_at = datetime.now() + timedelta(seconds=1)  # type: ignore[attr-defined]
        plugin._guanxing_own_preview_msg_id = 9000  # type: ignore[attr-defined]

        await plugin._send_guanxing_shift()  # type: ignore[attr-defined]
        self.assertEqual(
            sends,
            [("xinggong", ".改换星移 @salt9527", True, 9000)],
        )

    def test_send_block_delay_seconds_only_blocks_noncritical_in_claim_window(self) -> None:
        plugin = AutoXinggongPlugin(
            _dummy_config(enable_xinggong_guanxing=True, xinggong_guanxing_shift_advance_seconds=1),
            logging.getLogger("test"),
        )
        settlement_at = datetime.now() + timedelta(seconds=5)
        plugin._guanxing_claim_active = True  # type: ignore[attr-defined]
        plugin._guanxing_settlement_at = settlement_at  # type: ignore[attr-defined]

        blocked = plugin.send_block_delay_seconds("garden", ".小药园", now=datetime.now())
        allowed = plugin.send_block_delay_seconds("xinggong", ".改换星移 @salt9527", now=datetime.now())

        self.assertGreater(blocked, 0.0)
        self.assertEqual(allowed, 0.0)

    async def test_guanxing_failure_cancels_claim_window(self) -> None:
        plugin = AutoXinggongPlugin(
            _dummy_config(enable_xinggong_guanxing=True),
            logging.getLogger("test"),
        )
        plugin._guanxing_claim_active = True  # type: ignore[attr-defined]
        plugin._guanxing_settlement_at = datetime.now() + timedelta(minutes=1)  # type: ignore[attr-defined]
        plugin._guanxing_own_command_msg_id = 9100  # type: ignore[attr-defined]

        ctx = MessageContext(
            chat_id=-100,
            message_id=9101,
            reply_to_msg_id=9100,
            sender_id=999,
            text="你今日已观星一次，天机不可多泄，请明日再来",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )
        actions = await plugin.on_message(ctx)

        self.assertIsNone(actions)
        self.assertFalse(getattr(plugin, "_guanxing_claim_active"))

    async def test_external_preview_in_new_window_grace_period_is_ignored(self) -> None:
        plugin = AutoXinggongPlugin(
            _dummy_config(enable_xinggong_guanxing=True),
            logging.getLogger("test"),
        )

        plugin._next_guanxing_settlement_at = lambda now: now + timedelta(hours=3)  # type: ignore[attr-defined]

        ctx = MessageContext(
            chat_id=-100,
            message_id=9201,
            reply_to_msg_id=123,
            sender_id=999,
            text="【星盘显化】@other 闭目凝神，推演天机...星盘之上，天机已然显现！\n下一次天道演化将是：【Good·星辰异象】\n当前天命所归：@someone",
            ts=datetime.now(timezone.utc),
            is_reply=False,
            is_reply_to_me=False,
        )
        actions = await plugin.on_message(ctx)

        self.assertIsNone(actions)
        self.assertFalse(getattr(plugin, "_guanxing_claim_active"))

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

    async def test_qizhen_existing_invite_reply_waits_210_seconds(self) -> None:
        plugin = AutoXinggongPlugin(_dummy_config(), logging.getLogger("test"))

        calls: list[tuple[str, float]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                calls.append((key, delay_seconds))

        plugin._scheduler = _FakeScheduler()  # type: ignore[attr-defined]
        now = datetime.now()
        setattr(plugin, "_cycle_date", plugin._cycle_date_for(now))  # type: ignore[attr-defined]
        setattr(plugin, "_qizhen_pending_slot", 1)
        setattr(plugin, "_qizhen_last_sent_at", now - timedelta(seconds=180))

        ctx = MessageContext(
            chat_id=-100,
            message_id=21,
            reply_to_msg_id=None,
            sender_id=999,
            text="你已发布启阵邀请，请勿重复操作，等待同门响应或邀请超时。",
            ts=datetime.now(timezone.utc),
            is_reply=False,
            is_reply_to_me=False,
        )
        await plugin.on_message(ctx)
        pending_until = getattr(plugin, "_qizhen_existing_invite_until")
        self.assertIsNotNone(pending_until)
        self.assertEqual(getattr(plugin, "_qizhen_pending_slot"), 1)
        qizhen_delays = [delay for key, delay in calls if key == "xinggong.qizhen.loop"]
        self.assertEqual(len(qizhen_delays), 1)
        self.assertGreater(qizhen_delays[0], 209)
        self.assertLess(qizhen_delays[0], 211)

    async def test_qizhen_cooldown_reply_recovers_first_success_and_future_midpoint(self) -> None:
        plugin = AutoXinggongPlugin(
            _dummy_config(enable_xinggong_deep_biguan=True),
            logging.getLogger("test"),
        )

        calls: list[tuple[str, float]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                calls.append((key, delay_seconds))

        plugin._scheduler = _FakeScheduler()  # type: ignore[attr-defined]

        start = datetime.now()
        ctx = MessageContext(
            chat_id=-100,
            message_id=21,
            reply_to_msg_id=20,
            sender_id=999,
            text="你刚刚参与过布阵，心神消耗巨大，请在11小时0分钟0秒后再次启阵。",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )
        await plugin.on_message(ctx)
        recovered_at = getattr(plugin, "_qizhen_first_success_at")
        self.assertIsNotNone(recovered_at)
        self.assertIsNone(getattr(plugin, "_qizhen_second_success_at"))
        elapsed = (start - recovered_at).total_seconds()
        self.assertGreater(elapsed, 3590)
        self.assertLess(elapsed, 3610)
        self.assertIn(("xinggong.deep_biguan.status.now", 0.0), calls)
        midpoint_delays = [delay for key, delay in calls if key == "xinggong.deep_biguan.status.midpoint"]
        self.assertEqual(len(midpoint_delays), 1)
        self.assertGreater(midpoint_delays[0], 14390)
        self.assertLess(midpoint_delays[0], 14410)

    async def test_qizhen_cooldown_reply_without_reply_still_matches_within_210_seconds(self) -> None:
        plugin = AutoXinggongPlugin(_dummy_config(), logging.getLogger("test"))

        now = datetime.now()
        setattr(plugin, "_cycle_date", plugin._cycle_date_for(now))  # type: ignore[attr-defined]
        setattr(plugin, "_qizhen_pending_slot", 1)
        setattr(plugin, "_qizhen_last_sent_at", now - timedelta(seconds=180))

        ctx = MessageContext(
            chat_id=-100,
            message_id=22,
            reply_to_msg_id=None,
            sender_id=999,
            text="你刚刚参与过布阵，心神消耗巨大，请在11小时0分钟0秒后再次启阵。",
            ts=datetime.now(timezone.utc),
            is_reply=False,
            is_reply_to_me=False,
        )
        await plugin.on_message(ctx)
        self.assertIsNotNone(getattr(plugin, "_qizhen_blocked_until"))
        self.assertIsNotNone(getattr(plugin, "_qizhen_first_success_at"))

    async def test_qizhen_cooldown_reply_for_pending_second_slot_recovers_second_success(self) -> None:
        plugin = AutoXinggongPlugin(_dummy_config(), logging.getLogger("test"))

        now = datetime.now()
        first_success = now - timedelta(hours=13)
        setattr(plugin, "_cycle_date", plugin._cycle_date_for(now))  # type: ignore[attr-defined]
        setattr(plugin, "_qizhen_first_success_at", first_success)
        setattr(plugin, "_qizhen_pending_slot", 2)
        setattr(plugin, "_qizhen_last_sent_at", now - timedelta(seconds=10))

        ctx = MessageContext(
            chat_id=-100,
            message_id=23,
            reply_to_msg_id=22,
            sender_id=999,
            text="你刚刚参与过布阵，心神消耗巨大，请在11小时0分钟0秒后再次启阵。",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )
        await plugin.on_message(ctx)
        self.assertIsNotNone(getattr(plugin, "_qizhen_second_success_at"))
        self.assertEqual(getattr(plugin, "_qizhen_pending_slot"), None)

    async def test_qizhen_cooldown_reply_recovers_passed_midpoint_immediately(self) -> None:
        plugin = AutoXinggongPlugin(
            _dummy_config(enable_xinggong_deep_biguan=True),
            logging.getLogger("test"),
        )

        calls: list[tuple[str, float]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                calls.append((key, delay_seconds))

        plugin._scheduler = _FakeScheduler()  # type: ignore[attr-defined]

        ctx = MessageContext(
            chat_id=-100,
            message_id=22,
            reply_to_msg_id=21,
            sender_id=999,
            text="你刚刚参与过布阵，心神消耗巨大，请在6小时0分钟0秒后再次启阵。",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )
        await plugin.on_message(ctx)
        self.assertNotIn(("xinggong.deep_biguan.status.now", 0.0), calls)
        self.assertIn(("xinggong.deep_biguan.status.midpoint", 0.0), calls)

    async def test_qizhen_success_schedules_deep_biguan_checks_when_enabled(self) -> None:
        plugin = AutoXinggongPlugin(
            _dummy_config(enable_xinggong_deep_biguan=True),
            logging.getLogger("test"),
        )

        calls: list[tuple[str, float]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                calls.append((key, delay_seconds))

        plugin._scheduler = _FakeScheduler()  # type: ignore[attr-defined]
        now = datetime.now()
        setattr(plugin, "_cycle_date", plugin._cycle_date_for(now))  # type: ignore[attr-defined]
        setattr(plugin, "_qizhen_pending_slot", 1)
        setattr(plugin, "_qizhen_last_invite_msg_id", 88)

        success = MessageContext(
            chat_id=-100,
            message_id=88,
            reply_to_msg_id=123,
            sender_id=999,
            text="【周天星斗大阵-成】星光汇聚，大阵已成!",
            ts=datetime.now(timezone.utc),
            is_reply=False,
            is_reply_to_me=False,
        )
        await plugin.on_message(success)
        self.assertIn(("xinggong.qizhen.loop", 0.0), calls)
        self.assertIn(("xinggong.deep_biguan.status.now", 0.0), calls)
        self.assertIn(("xinggong.deep_biguan.status.midpoint", 18000.0), calls)

    async def test_biguan_status_reply_enters_deep_biguan_when_inactive(self) -> None:
        plugin = AutoXinggongPlugin(
            _dummy_config(enable_xinggong_deep_biguan=True),
            logging.getLogger("test"),
        )
        now = datetime.now()
        setattr(plugin, "_cycle_date", plugin._cycle_date_for(now))  # type: ignore[attr-defined]
        setattr(plugin, "_deep_biguan_status_reason", "qizhen_success")
        setattr(plugin, "_deep_biguan_status_requested_at", now)
        setattr(plugin, "_deep_biguan_status_msg_id", 55)

        ctx = MessageContext(
            chat_id=-100,
            message_id=56,
            reply_to_msg_id=55,
            sender_id=999,
            text="你并未处于深度闭关之中",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=False,
        )
        actions = await plugin.on_message(ctx)
        assert actions is not None
        self.assertEqual([a.text for a in actions], [".深度闭关"])

    async def test_biguan_status_reply_restarts_deep_biguan_when_active(self) -> None:
        plugin = AutoXinggongPlugin(
            _dummy_config(enable_xinggong_deep_biguan=True),
            logging.getLogger("test"),
        )
        now = datetime.now()
        setattr(plugin, "_cycle_date", plugin._cycle_date_for(now))  # type: ignore[attr-defined]
        setattr(plugin, "_deep_biguan_status_reason", "midpoint")
        setattr(plugin, "_deep_biguan_status_requested_at", now)
        setattr(plugin, "_deep_biguan_status_msg_id", 66)

        ctx = MessageContext(
            chat_id=-100,
            message_id=67,
            reply_to_msg_id=66,
            sender_id=999,
            text="你正在深度闭关，预计还需 4小时59分钟58秒即可功成圆满。",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=False,
        )
        actions = await plugin.on_message(ctx)
        assert actions is not None
        self.assertEqual([a.text for a in actions], [".强行出关", ".深度闭关"])
        self.assertEqual(actions[1].delay_seconds, 25.0)

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

    async def test_qizhen_loop_waits_for_second_success_cooldown_end_before_next_cycle(self) -> None:
        now = datetime.now()
        future_start = (now + timedelta(hours=1)).strftime("%H:%M")
        plugin = AutoXinggongPlugin(
            _dummy_config(xinggong_qizhen_start_time=future_start),
            logging.getLogger("test"),
        )

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
        setattr(plugin, "_cycle_date", plugin._cycle_date_for(now))  # type: ignore[attr-defined]
        plugin._qizhen_first_success_at = now - timedelta(hours=13)  # type: ignore[attr-defined]
        plugin._qizhen_second_success_at = now - timedelta(minutes=1)  # type: ignore[attr-defined]
        plugin._qizhen_blocked_until = now + timedelta(minutes=10)  # type: ignore[attr-defined]
        plugin._qizhen_next_cycle_at = plugin._qizhen_blocked_until  # type: ignore[attr-defined]

        await plugin._qizhen_loop()  # type: ignore[attr-defined]
        self.assertEqual(sends, [])
        self.assertTrue(scheduled)
        key, delay = scheduled[-1]
        self.assertEqual(key, "xinggong.qizhen.loop")
        self.assertGreater(delay, 590)
        self.assertLess(delay, 610)

    async def test_qizhen_loop_restarts_immediately_after_second_success_cooldown(self) -> None:
        now = datetime.now()
        future_start = (now + timedelta(hours=1)).strftime("%H:%M")
        plugin = AutoXinggongPlugin(
            _dummy_config(xinggong_qizhen_start_time=future_start),
            logging.getLogger("test"),
        )

        scheduled: list[tuple[str, float]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                scheduled.append((key, delay_seconds))

        sends: list[str] = []

        async def _send(_plugin: str, text: str, _reply_to_topic: bool) -> int | None:
            sends.append(text)
            return 999

        plugin._scheduler = _FakeScheduler()  # type: ignore[attr-defined]
        plugin._send = _send  # type: ignore[attr-defined]
        setattr(plugin, "_cycle_date", plugin._cycle_date_for(now))  # type: ignore[attr-defined]
        plugin._qizhen_first_success_at = now - timedelta(hours=13)  # type: ignore[attr-defined]
        plugin._qizhen_second_success_at = now - timedelta(hours=12, seconds=10)  # type: ignore[attr-defined]
        plugin._qizhen_blocked_until = now - timedelta(seconds=1)  # type: ignore[attr-defined]
        plugin._qizhen_next_cycle_at = now - timedelta(seconds=1)  # type: ignore[attr-defined]

        await plugin._qizhen_loop()  # type: ignore[attr-defined]
        self.assertEqual(sends, [".启阵"])
        self.assertEqual(getattr(plugin, "_qizhen_pending_slot"), 1)
        self.assertEqual(getattr(plugin, "_cycle_date"), now.date())
        self.assertIn(("xinggong.qizhen.loop", 120.0), scheduled)


if __name__ == "__main__":
    unittest.main()
