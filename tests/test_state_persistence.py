import logging
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from xiuxian_bot.config import Config, IdentityProfile
from xiuxian_bot.core.state_store import SQLiteStateStore, serialize_datetime, serialize_date
from xiuxian_bot.plugins.biguan import AutoBiguanPlugin
from xiuxian_bot.plugins.chuangta import AutoChuangtaPlugin
from xiuxian_bot.plugins.garden import AutoGardenPlugin
from xiuxian_bot.plugins.lingxiaogong import AutoLingxiaogongPlugin
from xiuxian_bot.plugins.xinggong import AutoXinggongPlugin
from xiuxian_bot.plugins.yuanying import AutoYuanyingPlugin
from xiuxian_bot.plugins.zongmen import AutoZongmenPlugin


def _dummy_config(**overrides) -> Config:
    values = dict(
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
        xinggong_guanxing_target_username="salt9527",
        xinggong_guanxing_preview_advance_seconds=180,
        xinggong_guanxing_shift_advance_seconds=1,
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
        auto_return_main_after_avatar_action=True,
        auto_return_main_delay_seconds=120,
        status_command=".状态",
        status_identity_header_keyword="修士状态",
    )
    values.update(overrides)
    return Config(**values)


class TestStateStoreAndRestore(unittest.IsolatedAsyncioTestCase):
    def test_state_store_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.sqlite3"
            store = SQLiteStateStore(str(path))
            payload = {"count": 3, "name": "demo"}
            store.save_state("demo", payload)
            self.assertEqual(store.load_state("demo"), payload)
            store.close()

    async def test_biguan_bootstrap_restores_pending_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.sqlite3"
            store = SQLiteStateStore(str(path))
            target_at = datetime.now() + timedelta(minutes=5)
            store.save_state("biguan", {"next_attempt_at": serialize_datetime(target_at)})

            plugin = AutoBiguanPlugin(_dummy_config(enable_biguan=True), logging.getLogger("test"))
            plugin.set_state_store(store)
            plugin.restore_state()

            calls: list[tuple[str, float]] = []

            class _FakeScheduler:
                async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                    calls.append((key, delay_seconds))

            async def _send(_plugin: str, _text: str, _reply_to_topic: bool) -> int | None:
                return None

            await plugin.bootstrap(_FakeScheduler(), _send)
            self.assertEqual(calls[0][0], "biguan.next")
            self.assertGreater(calls[0][1], 290)
            self.assertLess(calls[0][1], 310)
            store.close()

    async def test_biguan_bootstrap_restores_pending_feedback_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.sqlite3"
            store = SQLiteStateStore(str(path))
            target_at = datetime.now() + timedelta(minutes=5)
            store.save_state(
                "biguan",
                {"pending_feedback_deadline_at": serialize_datetime(target_at)},
            )

            plugin = AutoBiguanPlugin(_dummy_config(enable_biguan=True), logging.getLogger("test"))
            plugin.set_state_store(store)
            plugin.restore_state()

            calls: list[tuple[str, float]] = []

            class _FakeScheduler:
                async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                    calls.append((key, delay_seconds))

            async def _send(_plugin: str, _text: str, _reply_to_topic: bool) -> int | None:
                return None

            await plugin.bootstrap(_FakeScheduler(), _send)
            self.assertTrue(calls[0][0].startswith("biguan.feedback_timeout:"))
            self.assertGreater(calls[0][1], 290)
            self.assertLess(calls[0][1], 310)
            store.close()

    async def test_garden_bootstrap_restores_poll_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.sqlite3"
            store = SQLiteStateStore(str(path))
            next_poll_at = datetime.now() + timedelta(seconds=90)
            store.save_state(
                "garden",
                {
                    "seed_insufficient": True,
                    "seed_insufficient_warned": True,
                    "sow_blocked_no_idle": True,
                    "next_poll_at": serialize_datetime(next_poll_at),
                },
            )

            plugin = AutoGardenPlugin(_dummy_config(enable_garden=True), logging.getLogger("test"))
            plugin.set_state_store(store)
            plugin.restore_state()

            calls: list[tuple[str, float]] = []

            class _FakeScheduler:
                async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                    calls.append((key, delay_seconds))

            async def _send(_plugin: str, _text: str, _reply_to_topic: bool) -> int | None:
                return None

            await plugin.bootstrap(_FakeScheduler(), _send)
            self.assertTrue(plugin._seed_insufficient)  # type: ignore[attr-defined]
            self.assertEqual(calls[0][0], "garden.poll")
            self.assertGreater(calls[0][1], 80)
            self.assertLess(calls[0][1], 100)
            store.close()

    async def test_yuanying_restore_state_affects_followup_loops(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.sqlite3"
            store = SQLiteStateStore(str(path))
            liefeng_at = datetime.now() + timedelta(minutes=10)
            chuqiao_at = datetime.now() + timedelta(minutes=20)
            store.save_state(
                "yuanying",
                {
                    "liefeng_blocked_until": serialize_datetime(liefeng_at),
                    "chuqiao_blocked_until": serialize_datetime(chuqiao_at),
                    "chuqiao_waiting_settle": True,
                },
            )

            plugin = AutoYuanyingPlugin(_dummy_config(enable_yuanying=True), logging.getLogger("test"))
            plugin.set_state_store(store)
            plugin.restore_state()

            calls: list[tuple[str, float]] = []

            class _FakeScheduler:
                async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                    calls.append((key, delay_seconds))

            async def _send(_plugin: str, _text: str, _reply_to_topic: bool) -> int | None:
                return None

            plugin._scheduler = _FakeScheduler()  # type: ignore[attr-defined]
            plugin._send = _send  # type: ignore[attr-defined]
            await plugin._liefeng_loop()  # type: ignore[attr-defined]
            await plugin._chuqiao_loop()  # type: ignore[attr-defined]

            delays = dict(calls)
            self.assertGreater(delays["yuanying.liefeng.loop"], 590)
            self.assertLess(delays["yuanying.liefeng.loop"], 610)
            self.assertGreater(delays["yuanying.chuqiao.loop"], 1190)
            self.assertLess(delays["yuanying.chuqiao.loop"], 1210)
            store.close()

    def test_yuanying_restore_state_keeps_escape_pause(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.sqlite3"
            store = SQLiteStateStore(str(path))
            store.save_state(
                "yuanying",
                {
                    "escape_pause_active": True,
                    "escape_pause_reason": "元婴遁逃暂停中，等待手动恢复",
                },
            )

            plugin = AutoYuanyingPlugin(_dummy_config(enable_yuanying=True), logging.getLogger("test"))
            plugin.set_state_store(store)
            plugin.restore_state()

            self.assertEqual(plugin.runtime_pause_reason(), "元婴遁逃暂停中，等待手动恢复")
            store.close()

    async def test_xinggong_bootstrap_restores_poll_wenan_and_claim_windows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.sqlite3"
            store = SQLiteStateStore(str(path))
            now = datetime.now()
            store.save_state(
                "xinggong",
                {
                    "cycle_date": serialize_date(now.date()),
                    "next_poll_at": serialize_datetime(now + timedelta(minutes=2)),
                    "wenan_next_at": serialize_datetime(now + timedelta(minutes=3)),
                    "qizhen_first_success_at": serialize_datetime(now - timedelta(hours=1)),
                    "guanxing_claim_active": True,
                    "guanxing_claim_event": "星辰异象",
                    "guanxing_settlement_at": serialize_datetime(now + timedelta(minutes=10)),
                    "guanxing_preview_sent": False,
                    "guanxing_shift_sent": False,
                },
            )

            plugin = AutoXinggongPlugin(
                _dummy_config(
                    enable_xinggong=True,
                    enable_xinggong_deep_biguan=True,
                    enable_xinggong_guanxing=True,
                    xinggong_qizhen_start_time="00:00",
                ),
                logging.getLogger("test"),
            )
            plugin.set_state_store(store)
            plugin.restore_state()

            calls: list[tuple[str, float]] = []

            class _FakeScheduler:
                async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                    calls.append((key, delay_seconds))

            async def _send(_plugin: str, _text: str, _reply_to_topic: bool, **_kwargs) -> int | None:
                return None

            await plugin.bootstrap(_FakeScheduler(), _send)
            keys = {key for key, _ in calls}
            self.assertIn("xinggong.poll", keys)
            self.assertIn("xinggong.qizhen.loop", keys)
            self.assertIn("xinggong.wenan.loop", keys)
            self.assertIn("xinggong.guanxing.preview", keys)
            self.assertIn("xinggong.guanxing.shift", keys)
            self.assertIn("xinggong.deep_biguan.status.now", keys)
            self.assertIn("xinggong.deep_biguan.status.midpoint", keys)
            store.close()

    async def test_chuangta_and_zongmen_restore_day_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.sqlite3"
            store = SQLiteStateStore(str(path))
            today = datetime.now().date()
            store.save_state(
                "chuangta",
                {
                    "current_day": serialize_date(today),
                    "done_today": True,
                    "pending_today": False,
                    "yuanying_out_of_body": True,
                },
            )
            store.save_state(
                "zongmen",
                {
                    "state_date": serialize_date(today),
                    "dianmao_done": True,
                    "chuangong_count": 3,
                    "chuangong_disabled": False,
                    "chuangong_pending": False,
                },
            )

            chuangta = AutoChuangtaPlugin(
                _dummy_config(enable_chuangta=True, enable_yuanying=True),
                logging.getLogger("test"),
            )
            chuangta.set_state_store(store)
            chuangta.restore_state()
            self.assertTrue(chuangta._done_today)  # type: ignore[attr-defined]
            self.assertTrue(chuangta._yuanying_out_of_body)  # type: ignore[attr-defined]

            zongmen = AutoZongmenPlugin(
                _dummy_config(
                    enable_zongmen=True,
                    zongmen_dianmao_time="09:37",
                    zongmen_chuangong_times="09:38,09:40,09:43",
                ),
                logging.getLogger("test"),
            )
            zongmen.set_state_store(store)
            zongmen.restore_state()

            async def _send(_plugin: str, _text: str, _reply_to_topic: bool, **_kwargs) -> int | None:
                raise AssertionError("should not send when daily count is already full")

            result = await zongmen._maybe_send_chuangong(_send)  # type: ignore[attr-defined]
            self.assertEqual(result, "skip")
            store.close()

    async def test_lingxiaogong_bootstrap_restores_pending_loops(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.sqlite3"
            store = SQLiteStateStore(str(path))
            now = datetime.now()
            store.save_state(
                "lingxiaogong",
                {
                    "current_day": serialize_date(now.date()),
                    "today_wenxin_done": True,
                    "seal_name": "澄明",
                    "next_status_at": serialize_datetime(now + timedelta(minutes=2)),
                    "next_climb_at": serialize_datetime(now + timedelta(minutes=5)),
                    "today_climb_count": 3,
                    "next_jiutian_at": serialize_datetime(now + timedelta(minutes=7)),
                    "jiutian_cooldown_until": serialize_datetime(now + timedelta(minutes=7)),
                },
            )

            plugin = AutoLingxiaogongPlugin(
                _dummy_config(enable_lingxiaogong=True),
                logging.getLogger("test"),
            )
            plugin.set_state_store(store)
            plugin.restore_state()

            calls: list[tuple[str, float]] = []
            sends: list[str] = []

            class _FakeScheduler:
                async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                    calls.append((key, delay_seconds))

            async def _send(_plugin: str, text: str, _reply_to_topic: bool) -> int | None:
                sends.append(text)
                return 9001

            await plugin.bootstrap(_FakeScheduler(), _send)
            keys = {key for key, _ in calls}
            self.assertEqual(sends, [".天阶状态"])
            self.assertIn("lingxiaogong.status.loop", keys)
            self.assertIn("lingxiaogong.climb.loop", keys)
            self.assertIn("lingxiaogong.jiutian.loop", keys)
            store.close()


if __name__ == "__main__":
    unittest.main()
