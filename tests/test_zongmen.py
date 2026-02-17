import asyncio
import logging
import unittest
from datetime import datetime, timezone

from xiuxian_bot.config import Config
from xiuxian_bot.core.contracts import MessageContext
from xiuxian_bot.core.scheduler import Scheduler
from xiuxian_bot.plugins.zongmen import AutoZongmenPlugin


def _dummy_config(*, enable_zongmen: bool = True) -> Config:
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
        enable_zongmen=enable_zongmen,
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
        zongmen_dianmao_time="00:00",
        zongmen_chuangong_times="00:00,00:00,00:00",
        zongmen_chuangong_xinde_text="宗门传功",
        zongmen_catch_up=True,
        zongmen_action_spacing_seconds=0,
    )


class TestZongmenParser(unittest.IsolatedAsyncioTestCase):
    async def test_reply_hint_disables_chuangong(self) -> None:
        plugin = AutoZongmenPlugin(_dummy_config(), logging.getLogger("test"))

        ctx = MessageContext(
            chat_id=-100,
            message_id=1,
            reply_to_msg_id=123,
            sender_id=999,
            text="此神通需回复你的一条有价值的发言，方可为宗门记录功法。",
            ts=datetime.now(timezone.utc),
            is_reply=False,
            is_reply_to_me=False,
        )
        await plugin.on_message(ctx)

        # Internal flag should be set; next scheduled runs will skip.
        self.assertTrue(getattr(plugin, "_chuangong_disabled"))

    async def test_parse_chuangong_count(self) -> None:
        plugin = AutoZongmenPlugin(_dummy_config(), logging.getLogger("test"))

        ctx = MessageContext(
            chat_id=-100,
            message_id=2,
            reply_to_msg_id=123,
            sender_id=999,
            text="传功成功记录！你为宗门贡献了心得，获得了 30 点贡献。今日已传功 1/3 次。",
            ts=datetime.now(timezone.utc),
            is_reply=False,
            is_reply_to_me=False,
        )
        await plugin.on_message(ctx)
        self.assertEqual(getattr(plugin, "_chuangong_count"), 1)


class TestZongmenBootstrap(unittest.IsolatedAsyncioTestCase):
    async def test_bootstrap_catchup_sends_dianmao_and_chuangong(self) -> None:
        logger = logging.getLogger("test")
        scheduler = Scheduler(logger)
        plugin = AutoZongmenPlugin(_dummy_config(), logger)

        calls: list[tuple[str, str, bool, int | None]] = []
        next_id = 1000

        async def fake_send(plugin_name: str, text: str, reply_to_topic: bool, *, reply_to_msg_id=None):
            nonlocal next_id
            calls.append((plugin_name, text, reply_to_topic, reply_to_msg_id))
            next_id += 1
            return next_id

        await plugin.bootstrap(scheduler, fake_send)

        # Let scheduled tasks run.
        await asyncio.sleep(0.2)
        await scheduler.cancel_all()

        # Expect 1 dianmao + 3*(xinde + command) = 7 sends.
        self.assertEqual(len(calls), 7)

        dianmao = [c for c in calls if c[1] == "宗门点卯"]
        self.assertEqual(len(dianmao), 1)

        xinde = [c for c in calls if c[1].startswith("心得：")]
        self.assertEqual(len(xinde), 3)

        cmds = [c for c in calls if c[1] == "宗门传功" and c[3] is not None]
        self.assertEqual(len(cmds), 3)
