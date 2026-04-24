import logging
import unittest
from datetime import datetime, timedelta, timezone

from xiuxian_bot.config import Config
from xiuxian_bot.core.contracts import MessageContext
from xiuxian_bot.runtime import build_plugins
from xiuxian_bot.plugins.lingxiaogong import AutoLingxiaogongPlugin


def _dummy_config(
    *,
    enable_lingxiaogong: bool = True,
    enable_lingxiaogong_wenxintai: bool = True,
    enable_lingxiaogong_jiutian: bool = True,
    enable_lingxiaogong_dengtianjie: bool = True,
    lingxiaogong_poll_interval_seconds: int = 300,
    lingxiaogong_wenxintai_after_climb_count: int = 4,
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
        zongmen_chuangong_xinde_text="今日修行心得：稳中求进。",
        zongmen_catch_up=True,
        zongmen_action_spacing_seconds=20,
        enable_xinggong_wenan=True,
        enable_xinggong_deep_biguan=False,
        enable_xinggong_guanxing=False,
        enable_yuanying_liefeng=True,
        xinggong_guanxing_target_username="salt9527",
        xinggong_guanxing_preview_advance_seconds=180,
        xinggong_guanxing_shift_advance_seconds=1.0,
        xinggong_guanxing_watch_events="星辰异象,地磁暴动",
        global_send_min_interval_seconds=10,
        state_db_path="xiuxian_state.sqlite3",
        enable_chuangta=False,
        chuangta_time="14:15",
        enable_lingxiaogong=enable_lingxiaogong,
        enable_lingxiaogong_wenxintai=enable_lingxiaogong_wenxintai,
        enable_lingxiaogong_jiutian=enable_lingxiaogong_jiutian,
        enable_lingxiaogong_dengtianjie=enable_lingxiaogong_dengtianjie,
        lingxiaogong_poll_interval_seconds=lingxiaogong_poll_interval_seconds,
        lingxiaogong_wenxintai_after_climb_count=lingxiaogong_wenxintai_after_climb_count,
        enable_random_event_nanlonghou=True,
        random_event_nanlonghou_action=".交换 功法",
    )


class TestLingxiaogongPlugin(unittest.IsolatedAsyncioTestCase):
    def test_build_plugins_includes_lingxiaogong(self) -> None:
        plugins = build_plugins(_dummy_config(), logging.getLogger("test"))
        self.assertIn("lingxiaogong", {plugin.name for plugin in plugins})

    async def test_bootstrap_requests_status_immediately(self) -> None:
        plugin = AutoLingxiaogongPlugin(_dummy_config(), logging.getLogger("test"))

        sends: list[str] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                return None

        async def _send(_plugin: str, text: str, _reply_to_topic: bool) -> int | None:
            sends.append(text)
            return 1001

        await plugin.bootstrap(_FakeScheduler(), _send)
        self.assertEqual(sends, [".天阶状态"])

    async def test_status_without_wenxin_before_threshold_requests_climb(self) -> None:
        plugin = AutoLingxiaogongPlugin(_dummy_config(), logging.getLogger("test"))

        sends: list[str] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                return None

        async def _send(_plugin: str, text: str, _reply_to_topic: bool) -> int | None:
            sends.append(text)
            return 1001 if text == ".天阶状态" else 1002

        await plugin.bootstrap(_FakeScheduler(), _send)

        ctx = MessageContext(
            chat_id=-100,
            message_id=2001,
            reply_to_msg_id=1001,
            sender_id=999,
            text="""【凌霄宫·天阶状态】
当前云阶进度: 4 / 12
已完成周天: 0轮
罡风淬体: 1/12
下一目标: 第5阶
下轮奖阶: 云门初启
登阶冷却: 0秒
预计消耗: 238点修为
当前成功率: 63%
问心状态: 尚未问心
引九天罡风: 未解锁（需完成1轮周天）
借天门势: 未解锁（需完成3轮周天）
""",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )

        await plugin.on_message(ctx)
        self.assertEqual(sends[-1], ".登天阶")

    async def test_status_before_wenxin_threshold_requests_climb(self) -> None:
        plugin = AutoLingxiaogongPlugin(
            _dummy_config(lingxiaogong_wenxintai_after_climb_count=4),
            logging.getLogger("test"),
        )

        sends: list[str] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                return None

        async def _send(_plugin: str, text: str, _reply_to_topic: bool) -> int | None:
            sends.append(text)
            return 1001 if text == ".天阶状态" else 1002

        await plugin.bootstrap(_FakeScheduler(), _send)

        ctx = MessageContext(
            chat_id=-100,
            message_id=2002,
            reply_to_msg_id=1001,
            sender_id=999,
            text="""【凌霄云阶】
当前进度: 4 / 12 阶
已完成周天: 1 轮
罡风淬体: 3 / 12 层
下次目标: 第 5 阶
下轮奖阶: 云门初启
登阶冷却: 0秒
预计消耗: 255 点修为
当前成功率: 67%

问心状态: 今日尚未问心。可使用 .问心台 获取登阶加持。

凌霄神通:
 - .引九天罡风: 3小时3分钟16秒
 - .借天门势: 未解锁 (需完成 3 轮周天)
""",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )

        await plugin.on_message(ctx)
        self.assertEqual(sends[-1], ".登天阶")

    async def test_status_with_existing_seal_requests_climb(self) -> None:
        plugin = AutoLingxiaogongPlugin(_dummy_config(), logging.getLogger("test"))

        sends: list[str] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                return None

        async def _send(_plugin: str, text: str, _reply_to_topic: bool) -> int | None:
            sends.append(text)
            return 1101 if text == ".天阶状态" else 1102

        await plugin.bootstrap(_FakeScheduler(), _send)

        ctx = MessageContext(
            chat_id=-100,
            message_id=2101,
            reply_to_msg_id=1101,
            sender_id=999,
            text="""【凌霄宫·天阶状态】
当前云阶进度: 4 / 12
已完成周天: 0轮
罡风淬体: 1/12
登阶冷却: 0秒
预计消耗: 238点修为
当前成功率: 63%
问心状态: 【澄明】 - 下次登天阶时，成功率显著提升。
引九天罡风: 未解锁（需完成1轮周天）
借天门势: 未解锁（需完成3轮周天）
""",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )

        await plugin.on_message(ctx)
        self.assertEqual(sends[-1], ".登天阶")

    async def test_status_with_available_jiutian_requests_jiutian_first(self) -> None:
        plugin = AutoLingxiaogongPlugin(_dummy_config(), logging.getLogger("test"))

        sends: list[str] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                return None

        async def _send(_plugin: str, text: str, _reply_to_topic: bool) -> int | None:
            sends.append(text)
            return 1101 if text == ".天阶状态" else 1102

        await plugin.bootstrap(_FakeScheduler(), _send)

        ctx = MessageContext(
            chat_id=-100,
            message_id=2102,
            reply_to_msg_id=1101,
            sender_id=999,
            text="""【凌霄云阶】
当前进度: 0 / 12 阶
已完成周天: 1 轮
罡风淬体: 4 / 12 层
下次目标: 第 1 阶
下轮奖阶: 云门初启
登阶冷却: 0秒
预计消耗: 119 点修为
当前成功率: 81%

问心状态: 今日已问心，但道印已在登阶中耗尽。

凌霄神通:
 - .引九天罡风: 可用
 - .借天门势: 未解锁 (需完成 3 轮周天)
""",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )

        await plugin.on_message(ctx)
        self.assertEqual(sends[-1], ".引九天罡风")

    async def test_system_identity_status_feedback_matches_pending_request_without_reply(self) -> None:
        plugin = AutoLingxiaogongPlugin(_dummy_config(), logging.getLogger("test"))

        sends: list[str] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                return None

        async def _send(_plugin: str, text: str, _reply_to_topic: bool) -> int | None:
            sends.append(text)
            return 1601 if text == ".天阶状态" else 1602

        await plugin.bootstrap(_FakeScheduler(), _send)

        ctx = MessageContext(
            chat_id=-100,
            message_id=2601,
            reply_to_msg_id=7310786,
            sender_id=10001,
            text="""【凌霄云阶】
当前进度: 4 / 12 阶
已完成周天: 1 轮
罡风淬体: 3 / 12 层
下次目标: 第 5 阶
下轮奖阶: 云门初启
登阶冷却: 0秒
预计消耗: 255 点修为
当前成功率: 67%

问心状态: 今日尚未问心。可使用 .问心台 获取登阶加持。

凌霄神通:
 - .引九天罡风: 3小时3分钟16秒
 - .借天门势: 未解锁 (需完成 3 轮周天)
""",
            ts=datetime.now(timezone.utc),
            is_reply=False,
            is_reply_to_me=False,
            is_from_system_identity=True,
            is_system_reply=True,
        )

        await plugin.on_message(ctx)
        self.assertEqual(sends[-1], ".登天阶")

    async def test_wenxintai_unknown_seal_marks_done_and_schedules_refresh(self) -> None:
        plugin = AutoLingxiaogongPlugin(_dummy_config(), logging.getLogger("test"))

        scheduled: list[tuple[str, float]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                scheduled.append((key, delay_seconds))

        async def _send(_plugin: str, _text: str, _reply_to_topic: bool) -> int | None:
            return 1201

        plugin._scheduler = _FakeScheduler()  # type: ignore[attr-defined]
        plugin._send = _send  # type: ignore[attr-defined]
        plugin._current_day = datetime.now().date()  # type: ignore[attr-defined]
        plugin._wenxin_requested_at = datetime.now()  # type: ignore[attr-defined]
        plugin._wenxin_request_msg_id = 1201  # type: ignore[attr-defined]

        ctx = MessageContext(
            chat_id=-100,
            message_id=2201,
            reply_to_msg_id=1201,
            sender_id=999,
            text="""【问心台回响】
你于问心台前静坐良久，最终凝出一道【无相】之印。
你因此获得了 20 点宗门贡献。
""",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )

        await plugin.on_message(ctx)
        self.assertTrue(plugin._today_wenxin_done)  # type: ignore[attr-defined]
        self.assertEqual(plugin._seal_name, "无相")  # type: ignore[attr-defined]
        self.assertIn(("lingxiaogong.status.loop", 15.0), scheduled)

    async def test_status_with_cooldown_schedules_climb_retry(self) -> None:
        plugin = AutoLingxiaogongPlugin(_dummy_config(), logging.getLogger("test"))

        scheduled: list[tuple[str, float]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                scheduled.append((key, delay_seconds))

        async def _send(_plugin: str, text: str, _reply_to_topic: bool) -> int | None:
            return 1301 if text == ".天阶状态" else 1302

        await plugin.bootstrap(_FakeScheduler(), _send)

        ctx = MessageContext(
            chat_id=-100,
            message_id=2301,
            reply_to_msg_id=1301,
            sender_id=999,
            text="""【凌霄宫·天阶状态】
当前云阶进度: 4 / 12
已完成周天: 0轮
罡风淬体: 1/12
登阶冷却: 1小时14分10秒
预计消耗: 238点修为
当前成功率: 63%
今日已问心，但道印已在登阶中耗尽。
引九天罡风: 未解锁（需完成1轮周天）
借天门势: 未解锁（需完成3轮周天）
""",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )

        await plugin.on_message(ctx)
        self.assertTrue(plugin._today_wenxin_done)  # type: ignore[attr-defined]
        self.assertIsNone(plugin._seal_name)  # type: ignore[attr-defined]
        climb_delays = [delay for key, delay in scheduled if key == "lingxiaogong.climb.loop"]
        self.assertEqual(len(climb_delays), 1)
        self.assertGreater(climb_delays[0], 4449)
        self.assertLess(climb_delays[0], 4452)

    async def test_jiutian_feedback_schedules_status_refresh_and_next_retry(self) -> None:
        plugin = AutoLingxiaogongPlugin(_dummy_config(), logging.getLogger("test"))

        scheduled: list[tuple[str, float]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                scheduled.append((key, delay_seconds))

        async def _send(_plugin: str, _text: str, _reply_to_topic: bool) -> int | None:
            return 1501

        plugin._scheduler = _FakeScheduler()  # type: ignore[attr-defined]
        plugin._send = _send  # type: ignore[attr-defined]
        plugin._current_day = datetime.now().date()  # type: ignore[attr-defined]
        plugin._jiutian_requested_at = datetime.now()  # type: ignore[attr-defined]
        plugin._jiutian_request_msg_id = 1501  # type: ignore[attr-defined]

        ctx = MessageContext(
            chat_id=-100,
            message_id=2501,
            reply_to_msg_id=1501,
            sender_id=999,
            text="""【九天罡风】
你强引九天罡风贯体，消耗了 260 点修为。
【罡风淬体】提升至 6 / 12 层，并凝得一道【澄明】之印。
下一次登天阶的成功率将显著提高。
""",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )

        await plugin.on_message(ctx)
        self.assertFalse(plugin._today_wenxin_done)  # type: ignore[attr-defined]
        self.assertEqual(plugin._seal_name, "澄明")  # type: ignore[attr-defined]
        self.assertIn(("lingxiaogong.status.loop", 15.0), scheduled)
        jiutian_delays = [delay for key, delay in scheduled if key == "lingxiaogong.jiutian.loop"]
        self.assertEqual(len(jiutian_delays), 1)
        self.assertGreater(jiutian_delays[0], 43199)
        self.assertLess(jiutian_delays[0], 43202)

    async def test_climb_feedback_schedules_status_refresh(self) -> None:
        plugin = AutoLingxiaogongPlugin(_dummy_config(), logging.getLogger("test"))

        scheduled: list[tuple[str, float]] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                scheduled.append((key, delay_seconds))

        async def _send(_plugin: str, _text: str, _reply_to_topic: bool) -> int | None:
            return 1401

        plugin._scheduler = _FakeScheduler()  # type: ignore[attr-defined]
        plugin._send = _send  # type: ignore[attr-defined]
        plugin._current_day = datetime.now().date()  # type: ignore[attr-defined]
        plugin._climb_requested_at = datetime.now()  # type: ignore[attr-defined]
        plugin._climb_request_msg_id = 1401  # type: ignore[attr-defined]

        ctx = MessageContext(
            chat_id=-100,
            message_id=2401,
            reply_to_msg_id=1401,
            sender_id=999,
            text="""你消耗了 238 点修为，踏上了第 5 阶云阶。
你在阶前生出杂念，心魔趁虚而入，额外损失了 73 点修为。
当前云阶进度仍为 4 / 12，罡风淬体: 1 / 12
""",
            ts=datetime.now(timezone.utc),
            is_reply=True,
            is_reply_to_me=True,
        )

        await plugin.on_message(ctx)
        self.assertIn(("lingxiaogong.status.loop", 15.0), scheduled)


if __name__ == "__main__":
    unittest.main()
