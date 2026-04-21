import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from xiuxian_bot.config import Config, IdentityProfile
from xiuxian_bot.tg_adapter import TGAdapter


def _dummy_config(*, topic_id: int = 7310786) -> Config:
    return Config(
        tg_api_id=1,
        tg_api_hash="hash",
        tg_session_name="session",
        game_chat_id=-100,
        topic_id=topic_id,
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
        account_id="default",
        account_name="default",
        identity_profiles=(
            IdentityProfile(
                key="main",
                kind="main",
                my_name="Me",
                switch_target="主魂",
                display_name="主魂",
            ),
        ),
        active_identity_key="main",
        switch_command_template=".切换 {target}",
        switch_list_command=".切换",
        switch_back_target="主魂",
        switch_success_keywords="切换成功,神念已附着",
        switch_back_success_keywords="神念重归主魂肉身",
        switch_failure_keywords="未找到道号或ID",
        status_command=".状态",
        status_identity_header_keyword="修士状态",
    )


class TestTGAdapter(unittest.IsolatedAsyncioTestCase):
    async def test_start_resolves_configured_system_reply_source_ids(self) -> None:
        adapter = TGAdapter(
            _dummy_config().with_identity(account_id="1", account_name="alpha").__class__(
                **{
                    **_dummy_config().to_dict(),
                    "system_reply_source_usernames": "https://t.me/hantianzunhl,@other_source",
                }
            ),
            logging.getLogger("test.tg_adapter"),
        )
        adapter._client = AsyncMock()
        adapter._client.get_me.return_value = SimpleNamespace(id=777)
        adapter._client.get_input_entity.return_value = object()
        adapter._client.get_entity.side_effect = [
            SimpleNamespace(id=10001),
            SimpleNamespace(id=10002),
        ]

        await adapter.start()

        self.assertEqual(adapter.me_id, 777)
        self.assertEqual(adapter._system_reply_source_ids, {10001, 10002})  # type: ignore[attr-defined]
        adapter._client.get_entity.assert_any_call("hantianzunhl")
        adapter._client.get_entity.assert_any_call("other_source")

    async def test_send_message_to_topic_uses_topic_root_as_reply_anchor(self) -> None:
        adapter = TGAdapter(_dummy_config(), logging.getLogger("test.tg_adapter"))
        adapter._peer = object()
        adapter._client = AsyncMock(return_value=SimpleNamespace(id=321))

        mid = await adapter.send_message(".元婴状态", reply_to_topic=True)

        self.assertEqual(mid, 321)
        request = adapter._client.call_args.args[0]
        self.assertEqual(request.reply_to.reply_to_msg_id, 7310786)
        self.assertEqual(request.reply_to.top_msg_id, 7310786)

    async def test_send_message_to_specific_topic_reply_keeps_message_and_topic_ids(self) -> None:
        adapter = TGAdapter(_dummy_config(), logging.getLogger("test.tg_adapter"))
        adapter._peer = object()
        adapter._client = AsyncMock(return_value=SimpleNamespace(id=654))

        mid = await adapter.send_message(".宗门传功", reply_to_topic=True, reply_to_msg_id=888888)

        self.assertEqual(mid, 654)
        request = adapter._client.call_args.args[0]
        self.assertEqual(request.reply_to.reply_to_msg_id, 888888)
        self.assertEqual(request.reply_to.top_msg_id, 7310786)

    async def test_build_context_marks_system_identity_sender(self) -> None:
        adapter = TGAdapter(_dummy_config(), logging.getLogger("test.tg_adapter"))
        adapter._me_id = 1
        adapter._system_reply_source_ids = {10001}  # type: ignore[attr-defined]
        event = SimpleNamespace(
            raw_text="@Me 系统回包",
            reply_to_msg_id=7310786,
            is_reply=False,
            chat_id=-100,
            sender_id=10001,
            message=SimpleNamespace(id=321, date=None),
        )

        ctx = await adapter.build_context(event)

        self.assertEqual(ctx.sender_id, 10001)
        self.assertTrue(ctx.is_from_system_identity)
        self.assertFalse(ctx.is_reply_to_me)
        self.assertTrue(ctx.is_system_reply)

    async def test_build_context_marks_bot_mention_as_system_reply(self) -> None:
        adapter = TGAdapter(_dummy_config(), logging.getLogger("test.tg_adapter"))
        adapter._me_id = 1
        event = SimpleNamespace(
            raw_text="@Me 原 bot 系统回包",
            reply_to_msg_id=7310786,
            is_reply=False,
            chat_id=-100,
            sender_id=20002,
            sender=SimpleNamespace(bot=True),
            message=SimpleNamespace(id=654, date=None),
        )

        ctx = await adapter.build_context(event)

        self.assertEqual(ctx.sender_id, 20002)
        self.assertFalse(ctx.is_from_system_identity)
        self.assertFalse(ctx.is_reply_to_me)
        self.assertTrue(ctx.is_system_reply)


if __name__ == "__main__":
    unittest.main()
