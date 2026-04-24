import logging
import unittest
from datetime import datetime, timezone

from xiuxian_bot.config import Config, IdentityProfile
from xiuxian_bot.core.contracts import MessageContext
from xiuxian_bot.plugins.random_event import AutoRandomEventPlugin


def _dummy_config(**overrides) -> Config:
    values = dict(
        tg_api_id=1,
        tg_api_hash="hash",
        tg_session_name="session",
        game_chat_id=-100,
        topic_id=123,
        my_name="fanrenthree",
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
        enable_lingxiaogong=False,
        enable_lingxiaogong_wenxintai=True,
        enable_lingxiaogong_jiutian=True,
        enable_lingxiaogong_dengtianjie=True,
        lingxiaogong_poll_interval_seconds=300,
        lingxiaogong_wenxintai_after_climb_count=4,
        enable_random_event_nanlonghou=True,
        random_event_nanlonghou_action=".交换 功法",
        enable_random_event_jiyin=True,
        random_event_jiyin_action=".献上魂魄",
        account_id="default",
        account_name="default",
        identity_profiles=(
            IdentityProfile(
                key="main",
                kind="main",
                my_name="fanrenthree",
                switch_target="主魂",
                display_name="主魂",
                tg_username="fanrenthree",
            ),
        ),
        active_identity_key="main",
        switch_command_template=".切换 {target}",
        switch_list_command=".切换",
        switch_back_target="主魂",
        switch_success_keywords="切换成功,神念已附着",
        switch_back_success_keywords="神念重归主魂肉身",
        switch_failure_keywords="未找到道号或ID",
        auto_return_main_after_avatar_action=True,
        auto_return_main_delay_seconds=120,
        status_command=".状态",
        status_identity_header_keyword="修士状态",
    )
    values.update(overrides)
    return Config(**values)


def _ctx(text: str, *, message_id: int = 1001) -> MessageContext:
    return MessageContext(
        chat_id=-100,
        message_id=message_id,
        reply_to_msg_id=None,
        sender_id=999,
        text=text,
        ts=datetime.now(timezone.utc),
        is_reply=False,
        is_reply_to_me=False,
    )


CHOICE_TEXT = """@fanrenthree！你感到一股无法抗拒的威压降临洞府！南陇侯的身影竟直接出现在你面前。

你有 10分钟 内做出抉择：
1. 回复本消息 .交换 法宝
2. 回复本消息 .交换 功法
3. 回复本消息 .拒绝交易
"""

JIYIN_CHOICE_TEXT = """@fanrenthree！你感到一股无法抗拒的意志锁定了你的神魂！
一个沙哑的声音在你脑海中响起：“小辈，让老夫看看你的成色...”

你必须在 180 分钟 内做出抉择：
1. 回复本消息 .献上魂魄 (高风险，高回报)
2. 回复本消息 .收敛气息 (低风险，低回报)
"""


class TestRandomEventPlugin(unittest.IsolatedAsyncioTestCase):
    async def test_nanlonghou_choice_replies_to_choice_message(self) -> None:
        plugin = AutoRandomEventPlugin(_dummy_config(), logging.getLogger("test"))

        actions = await plugin.on_message(_ctx(CHOICE_TEXT, message_id=321))

        self.assertIsNotNone(actions)
        assert actions is not None
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].plugin, "random_event")
        self.assertEqual(actions[0].text, ".交换 功法")
        self.assertEqual(actions[0].reply_to_msg_id, 321)

    async def test_nanlonghou_ignores_other_identity(self) -> None:
        plugin = AutoRandomEventPlugin(_dummy_config(), logging.getLogger("test"))
        text = CHOICE_TEXT.replace("@fanrenthree", "@otheruser")

        actions = await plugin.on_message(_ctx(text))

        self.assertIsNone(actions)

    async def test_nanlonghou_does_not_repeat_same_choice_message(self) -> None:
        plugin = AutoRandomEventPlugin(_dummy_config(), logging.getLogger("test"))

        first = await plugin.on_message(_ctx(CHOICE_TEXT, message_id=321))
        second = await plugin.on_message(_ctx(CHOICE_TEXT, message_id=321))

        self.assertIsNotNone(first)
        self.assertIsNone(second)

    async def test_nanlonghou_can_be_disabled_by_identity_override(self) -> None:
        base = _dummy_config(
            identity_profiles=(
                IdentityProfile(
                    key="main",
                    kind="main",
                    my_name="fanrenthree",
                    switch_target="主魂",
                    display_name="主魂",
                ),
                IdentityProfile(
                    key="avatar",
                    kind="avatar",
                    my_name="fanrenthree",
                    switch_target="fanrenthree",
                    display_name="fanrenthree",
                    config_overrides={"enable_random_event_nanlonghou": False},
                ),
            ),
            active_identity_key="avatar",
        )
        plugin = AutoRandomEventPlugin(base.apply_identity("avatar"), logging.getLogger("test"))

        self.assertTrue(plugin.enabled)
        actions = await plugin.on_message(_ctx(CHOICE_TEXT))
        self.assertIsNone(actions)

    async def test_jiyin_choice_replies_to_choice_message(self) -> None:
        plugin = AutoRandomEventPlugin(_dummy_config(), logging.getLogger("test"))

        actions = await plugin.on_message(_ctx(JIYIN_CHOICE_TEXT, message_id=654))

        self.assertIsNotNone(actions)
        assert actions is not None
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].plugin, "random_event")
        self.assertEqual(actions[0].text, ".献上魂魄")
        self.assertEqual(actions[0].reply_to_msg_id, 654)

    async def test_jiyin_ignores_other_identity(self) -> None:
        plugin = AutoRandomEventPlugin(_dummy_config(), logging.getLogger("test"))
        text = JIYIN_CHOICE_TEXT.replace("@fanrenthree", "@otheruser")

        actions = await plugin.on_message(_ctx(text))

        self.assertIsNone(actions)

    async def test_jiyin_can_be_disabled_by_identity_override(self) -> None:
        base = _dummy_config(
            identity_profiles=(
                IdentityProfile(
                    key="main",
                    kind="main",
                    my_name="fanrenthree",
                    switch_target="主魂",
                    display_name="主魂",
                ),
                IdentityProfile(
                    key="avatar",
                    kind="avatar",
                    my_name="fanrenthree",
                    switch_target="fanrenthree",
                    display_name="fanrenthree",
                    config_overrides={
                        "enable_random_event_nanlonghou": False,
                        "enable_random_event_jiyin": False,
                    },
                ),
            ),
            active_identity_key="avatar",
        )
        plugin = AutoRandomEventPlugin(base.apply_identity("avatar"), logging.getLogger("test"))

        self.assertFalse(plugin.enabled)
