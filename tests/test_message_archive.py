import asyncio
import importlib.util
import logging
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from xiuxian_bot.config import Config, IdentityProfile, SystemConfig
from xiuxian_bot.core.account_repository import AccountRepository


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
        global_sends_per_minute=6,
        plugin_sends_per_minute=3,
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
        state_db_path="xiuxian_app.sqlite3",
        enable_chuangta=False,
        chuangta_time="14:15",
        enable_lingxiaogong=False,
        enable_lingxiaogong_wenxintai=True,
        enable_lingxiaogong_dengtianjie=True,
        lingxiaogong_poll_interval_seconds=300,
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
    values.update(overrides)
    return Config(**values)


HAS_RUNTIME_DEPS = importlib.util.find_spec("telethon") is not None
HAS_WEB_DEPS = (
    importlib.util.find_spec("fastapi") is not None
    and importlib.util.find_spec("httpx") is not None
    and HAS_RUNTIME_DEPS
)


class TestMessageArchiveRepository(unittest.TestCase):
    def test_archive_and_search_preserves_edit_history(self) -> None:
        from xiuxian_bot.core.message_archive_repository import (
            MessageArchiveInput,
            MessageArchiveRepository,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "app.sqlite3"
            repo = MessageArchiveRepository(str(path), logging.getLogger("test"))
            ts = datetime.now(timezone.utc)

            repo.archive_message(
                MessageArchiveInput(
                    account_id=1,
                    chat_id=-100,
                    topic_id=900,
                    message_id=2001,
                    reply_to_msg_id=900,
                    sender_id=777,
                    sender_name="tester",
                    identity_key=None,
                    raw_text="星 盘 显 化",
                    event_type="new",
                    message_ts=ts,
                    is_reply=False,
                    is_topic_message=True,
                )
            )
            repo.archive_message(
                MessageArchiveInput(
                    account_id=1,
                    chat_id=-100,
                    topic_id=900,
                    message_id=2001,
                    reply_to_msg_id=900,
                    sender_id=777,
                    sender_name="tester",
                    identity_key=None,
                    raw_text="星盘显化 已编辑",
                    event_type="edit",
                    message_ts=ts,
                    is_reply=False,
                    is_topic_message=True,
                )
            )

            rows = repo.search_messages(query="星盘显化", account_id=1)
            self.assertEqual(len(rows), 2)
            self.assertEqual([row.edit_version for row in rows], [1, 0])
            self.assertEqual(repo.count_messages(query="已编辑", account_id=1), 1)
            self.assertEqual(repo.search_messages(query="已编辑", event_type="edit", account_id=1)[0].event_type, "edit")
            repo.close()

    def test_stats_summary_uses_scope_and_beijing_day_window(self) -> None:
        from xiuxian_bot.core.message_archive_repository import (
            MessageArchiveInput,
            MessageArchiveRepository,
        )

        fixed_now = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "app.sqlite3"
            repo = MessageArchiveRepository(str(path), logging.getLogger("test"))
            for account_id, message_id in (
                (1, 1001),
                (1, 1002),
                (1, 1003),
                (1, 1004),
                (2, 2001),
            ):
                repo.archive_message(
                    MessageArchiveInput(
                        account_id=account_id,
                        chat_id=-100,
                        topic_id=900,
                        message_id=message_id,
                        reply_to_msg_id=900,
                        sender_id=777,
                        sender_name="tester",
                        identity_key=None,
                        raw_text=f"msg-{message_id}",
                        event_type="new",
                        message_ts=fixed_now,
                        is_reply=False,
                        is_topic_message=True,
                    )
                )
            repo.close()

            conn = sqlite3.connect(str(path))
            conn.execute(
                "UPDATE message_archive SET captured_at = ? WHERE message_id = ?",
                (fixed_now.isoformat(timespec="seconds"), 1001),
            )
            conn.execute(
                "UPDATE message_archive SET captured_at = ? WHERE message_id = ?",
                ((fixed_now - timedelta(days=3)).isoformat(timespec="seconds"), 1002),
            )
            conn.execute(
                "UPDATE message_archive SET captured_at = ? WHERE message_id = ?",
                ((fixed_now - timedelta(days=20)).isoformat(timespec="seconds"), 1003),
            )
            conn.execute(
                "UPDATE message_archive SET captured_at = ? WHERE message_id = ?",
                ((fixed_now - timedelta(days=40)).isoformat(timespec="seconds"), 1004),
            )
            conn.execute(
                "UPDATE message_archive SET captured_at = ? WHERE message_id = ?",
                ((fixed_now - timedelta(hours=1)).isoformat(timespec="seconds"), 2001),
            )
            conn.commit()
            conn.close()

            repo = MessageArchiveRepository(str(path), logging.getLogger("test"))
            global_stats = repo.get_stats(now=fixed_now)
            account_stats = repo.get_stats(account_id=1, now=fixed_now)

            self.assertEqual(global_stats.total_count, 5)
            self.assertEqual(global_stats.today_count, 2)
            self.assertEqual(global_stats.last_7_days_count, 3)
            self.assertEqual(global_stats.last_30_days_count, 4)

            self.assertEqual(account_stats.total_count, 4)
            self.assertEqual(account_stats.today_count, 1)
            self.assertEqual(account_stats.last_7_days_count, 2)
            self.assertEqual(account_stats.last_30_days_count, 3)
            repo.close()

    def test_cleanup_old_messages_respects_beijing_retention_window(self) -> None:
        from xiuxian_bot.core.message_archive_repository import (
            MessageArchiveInput,
            MessageArchiveRepository,
        )

        fixed_now = datetime(2026, 4, 11, 4, 0, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "app.sqlite3"
            repo = MessageArchiveRepository(str(path), logging.getLogger("test"))
            for message_id in (3001, 3002, 3003):
                repo.archive_message(
                    MessageArchiveInput(
                        account_id=1,
                        chat_id=-100,
                        topic_id=900,
                        message_id=message_id,
                        reply_to_msg_id=900,
                        sender_id=777,
                        sender_name="tester",
                        identity_key=None,
                        raw_text=f"msg-{message_id}",
                        event_type="new",
                        message_ts=fixed_now,
                        is_reply=False,
                        is_topic_message=True,
                    )
                )
            repo.close()

            conn = sqlite3.connect(str(path))
            conn.execute(
                "UPDATE message_archive SET captured_at = ? WHERE message_id = ?",
                (datetime(2026, 3, 13, 0, 0, tzinfo=timezone.utc).isoformat(timespec="seconds"), 3001),
            )
            conn.execute(
                "UPDATE message_archive SET captured_at = ? WHERE message_id = ?",
                (datetime(2026, 3, 12, 15, 59, tzinfo=timezone.utc).isoformat(timespec="seconds"), 3002),
            )
            conn.execute(
                "UPDATE message_archive SET captured_at = ? WHERE message_id = ?",
                (datetime(2026, 4, 10, 23, 0, tzinfo=timezone.utc).isoformat(timespec="seconds"), 3003),
            )
            conn.commit()
            conn.close()

            repo = MessageArchiveRepository(str(path), logging.getLogger("test"))
            result = repo.cleanup_old_messages(
                retention_days=30,
                now=fixed_now,
                vacuum=False,
            )

            self.assertEqual(result.before_count, 3)
            self.assertEqual(result.deleted_count, 1)
            self.assertEqual(result.after_count, 2)
            remaining = {row.message_id for row in repo.search_messages(account_id=1, limit=10)}
            self.assertEqual(remaining, {3001, 3003})
            repo.close()

    def test_archive_message_auto_migrates_identity_key_column(self) -> None:
        from xiuxian_bot.core.message_archive_repository import (
            MessageArchiveInput,
            MessageArchiveRepository,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "app.sqlite3"
            conn = sqlite3.connect(str(path))
            conn.execute(
                """
                CREATE TABLE message_archive (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    topic_id INTEGER,
                    message_id INTEGER NOT NULL,
                    reply_to_msg_id INTEGER,
                    sender_id INTEGER,
                    sender_name TEXT,
                    raw_text TEXT NOT NULL,
                    normalized_text TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message_ts TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    edit_version INTEGER NOT NULL,
                    is_reply INTEGER NOT NULL DEFAULT 0,
                    is_topic_message INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.commit()
            conn.close()

            repo = MessageArchiveRepository(str(path), logging.getLogger("test"))
            repo.archive_message(
                MessageArchiveInput(
                    account_id=1,
                    chat_id=-100,
                    topic_id=900,
                    message_id=4001,
                    reply_to_msg_id=900,
                    sender_id=777,
                    sender_name="tester",
                    identity_key="avatar",
                    raw_text=".引九天罡风",
                    event_type="new",
                    message_ts=datetime.now(timezone.utc),
                    is_reply=False,
                    is_topic_message=True,
                )
            )
            rows = repo.search_messages(account_id=1)
            self.assertEqual(rows[0].identity_key, "avatar")
            repo.close()


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requires telethon runtime dependencies")
class TestRuntimeMessageArchive(unittest.IsolatedAsyncioTestCase):
    def test_build_archivable_text_preserves_media_placeholder_and_text(self) -> None:
        from xiuxian_bot.runtime import _build_archivable_text

        event = SimpleNamespace(
            message=SimpleNamespace(
                photo=object(),
                sticker=None,
                voice=None,
                audio=None,
                video_note=None,
                video=None,
                gif=None,
                poll=None,
                document=None,
                media=object(),
            )
        )

        self.assertEqual(_build_archivable_text(event, "带图说明"), "[image]\n带图说明")

    def test_build_archivable_text_keeps_media_only_placeholder(self) -> None:
        from xiuxian_bot.runtime import _build_archivable_text

        event = SimpleNamespace(
            message=SimpleNamespace(
                photo=object(),
                sticker=None,
                voice=None,
                audio=None,
                video_note=None,
                video=None,
                gif=None,
                poll=None,
                document=None,
                media=object(),
            )
        )

        self.assertEqual(_build_archivable_text(event, ""), "[image]")

    async def test_account_runner_archives_out_of_scope_topic_message(self) -> None:
        from xiuxian_bot.core.message_archive_repository import MessageArchiveRepository
        from xiuxian_bot.core.contracts import MessageContext
        from xiuxian_bot.runtime import AccountRunner

        class FakeScheduler:
            def __init__(self, logger) -> None:  # type: ignore[no-untyped-def]
                self.logger = logger

            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                return None

            async def cancel_all(self) -> None:
                return None

        class FakeSender:
            def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
                self.kwargs = kwargs

            async def send(
                self,
                plugin: str,
                text: str,
                reply_to_topic: bool,
                *,
                reply_to_msg_id: int | None = None,
                identity_key: str | None = None,
            ) -> int | None:
                _ = (plugin, text, reply_to_topic, reply_to_msg_id, identity_key)
                return 1

        event = SimpleNamespace(
            chat_id=-100,
            sender_id=999,
            reply_to_msg_id=900,
            raw_text="旁观话题消息",
            message=SimpleNamespace(
                id=701,
                date=datetime.now(timezone.utc),
                media=None,
                forum_topic=False,
                reply_to=SimpleNamespace(reply_to_top_id=None, forum_topic=True),
                sender=None,
            ),
        )

        class FakeAdapter:
            def __init__(self, config, logger, **kwargs) -> None:  # type: ignore[no-untyped-def]
                _ = kwargs
                self.config = config
                self.logger = logger
                self.me_id = 1
                self._handler = None

            def on_new_message(self, handler) -> None:  # type: ignore[no-untyped-def]
                self._handler = handler

            def on_message_edited(self, handler) -> None:  # type: ignore[no-untyped-def]
                _ = handler

            async def start(self) -> None:
                assert self._handler is not None
                asyncio.create_task(self._handler(event))

            async def send_message(
                self,
                text: str,
                *,
                reply_to_topic: bool = True,
                reply_to_msg_id: int | None = None,
            ) -> int | None:
                _ = (text, reply_to_topic, reply_to_msg_id)
                return 1

            async def build_context(self, raw_event) -> MessageContext:  # type: ignore[no-untyped-def]
                _ = raw_event
                return MessageContext(
                    chat_id=-100,
                    message_id=701,
                    reply_to_msg_id=900,
                    sender_id=999,
                    text="旁观话题消息",
                    ts=datetime.now(timezone.utc),
                    is_reply=False,
                    is_reply_to_me=False,
                )

            async def run_forever(self) -> None:
                await asyncio.Future()

            async def stop(self) -> None:
                return None

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "app.sqlite3"
            repo = AccountRepository(str(path), logging.getLogger("test"))
            record = repo.create_account("alpha", _dummy_config(account_name="alpha"), enabled=True)
            system_config = SystemConfig(app_db_path=str(path), log_dir=str(Path(tmpdir) / "logs"))
            runner = AccountRunner(record, system_config)

            with patch("xiuxian_bot.runtime.Scheduler", FakeScheduler), patch(
                "xiuxian_bot.runtime.ReliableSender",
                FakeSender,
            ), patch("xiuxian_bot.runtime.TGAdapter", FakeAdapter), patch(
                "xiuxian_bot.runtime.build_plugins",
                return_value=[],
            ):
                await runner.start()
                await asyncio.sleep(0.05)
                await runner.stop()

            archive = MessageArchiveRepository(str(path), logging.getLogger("test"))
            rows = archive.search_messages(account_id=record.id)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].raw_text, "旁观话题消息")
            self.assertEqual(rows[0].topic_id, 900)
            self.assertEqual(rows[0].event_type, "new")
            archive.close()
            repo.close()

    async def test_account_runner_skips_archive_when_account_toggle_disabled(self) -> None:
        from xiuxian_bot.core.message_archive_repository import MessageArchiveRepository
        from xiuxian_bot.core.contracts import MessageContext
        from xiuxian_bot.runtime import AccountRunner

        class FakeScheduler:
            def __init__(self, logger) -> None:  # type: ignore[no-untyped-def]
                self.logger = logger

            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                return None

            async def cancel_all(self) -> None:
                return None

        class FakeSender:
            def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
                self.kwargs = kwargs

            async def send(
                self,
                plugin: str,
                text: str,
                reply_to_topic: bool,
                *,
                reply_to_msg_id: int | None = None,
                identity_key: str | None = None,
            ) -> int | None:
                _ = (plugin, text, reply_to_topic, reply_to_msg_id, identity_key)
                return 1

        event = SimpleNamespace(
            chat_id=-100,
            sender_id=999,
            reply_to_msg_id=900,
            raw_text="不应归档",
            message=SimpleNamespace(
                id=703,
                date=datetime.now(timezone.utc),
                media=None,
                forum_topic=False,
                reply_to=SimpleNamespace(reply_to_top_id=None, forum_topic=True),
                sender=None,
            ),
        )

        class FakeAdapter:
            def __init__(self, config, logger, **kwargs) -> None:  # type: ignore[no-untyped-def]
                _ = kwargs
                self.config = config
                self.logger = logger
                self.me_id = 1
                self._handler = None

            def on_new_message(self, handler) -> None:  # type: ignore[no-untyped-def]
                self._handler = handler

            def on_message_edited(self, handler) -> None:  # type: ignore[no-untyped-def]
                _ = handler

            async def start(self) -> None:
                assert self._handler is not None
                asyncio.create_task(self._handler(event))

            async def send_message(
                self,
                text: str,
                *,
                reply_to_topic: bool = True,
                reply_to_msg_id: int | None = None,
            ) -> int | None:
                _ = (text, reply_to_topic, reply_to_msg_id)
                return 1

            async def build_context(self, raw_event) -> MessageContext:  # type: ignore[no-untyped-def]
                _ = raw_event
                return MessageContext(
                    chat_id=-100,
                    message_id=703,
                    reply_to_msg_id=900,
                    sender_id=999,
                    text="不应归档",
                    ts=datetime.now(timezone.utc),
                    is_reply=False,
                    is_reply_to_me=False,
                )

            async def run_forever(self) -> None:
                await asyncio.Future()

            async def stop(self) -> None:
                return None

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "app.sqlite3"
            repo = AccountRepository(str(path), logging.getLogger("test"))
            record = repo.create_account(
                "alpha",
                _dummy_config(account_name="alpha", enable_message_archive=False),
                enabled=True,
            )
            system_config = SystemConfig(app_db_path=str(path), log_dir=str(Path(tmpdir) / "logs"))
            runner = AccountRunner(record, system_config)

            with patch("xiuxian_bot.runtime.Scheduler", FakeScheduler), patch(
                "xiuxian_bot.runtime.ReliableSender",
                FakeSender,
            ), patch("xiuxian_bot.runtime.TGAdapter", FakeAdapter), patch(
                "xiuxian_bot.runtime.build_plugins",
                return_value=[],
            ):
                await runner.start()
                await asyncio.sleep(0.05)
                await runner.stop()

            archive = MessageArchiveRepository(str(path), logging.getLogger("test"))
            rows = archive.search_messages(account_id=record.id)
            self.assertEqual(rows, [])
            archive.close()
            repo.close()

    async def test_account_runner_archives_edited_media_message_with_placeholder(self) -> None:
        from xiuxian_bot.core.message_archive_repository import MessageArchiveRepository
        from xiuxian_bot.core.contracts import MessageContext
        from xiuxian_bot.runtime import AccountRunner

        class FakeScheduler:
            def __init__(self, logger) -> None:  # type: ignore[no-untyped-def]
                self.logger = logger

            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                return None

            async def cancel_all(self) -> None:
                return None

        class FakeSender:
            def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
                self.kwargs = kwargs

            async def send(
                self,
                plugin: str,
                text: str,
                reply_to_topic: bool,
                *,
                reply_to_msg_id: int | None = None,
                identity_key: str | None = None,
            ) -> int | None:
                _ = (plugin, text, reply_to_topic, reply_to_msg_id, identity_key)
                return 1

        event = SimpleNamespace(
            chat_id=-100,
            sender_id=999,
            reply_to_msg_id=900,
            raw_text="编辑后带图说明",
            message=SimpleNamespace(
                id=702,
                date=datetime.now(timezone.utc),
                media=object(),
                photo=object(),
                sticker=None,
                voice=None,
                audio=None,
                video_note=None,
                video=None,
                gif=None,
                poll=None,
                document=None,
                forum_topic=False,
                reply_to=SimpleNamespace(reply_to_top_id=None, forum_topic=True),
                sender=None,
            ),
        )

        class FakeAdapter:
            def __init__(self, config, logger, **kwargs) -> None:  # type: ignore[no-untyped-def]
                _ = kwargs
                self.config = config
                self.logger = logger
                self.me_id = 1
                self._edit_handler = None

            def on_new_message(self, handler) -> None:  # type: ignore[no-untyped-def]
                _ = handler

            def on_message_edited(self, handler) -> None:  # type: ignore[no-untyped-def]
                self._edit_handler = handler

            async def start(self) -> None:
                assert self._edit_handler is not None
                asyncio.create_task(self._edit_handler(event))

            async def send_message(
                self,
                text: str,
                *,
                reply_to_topic: bool = True,
                reply_to_msg_id: int | None = None,
            ) -> int | None:
                _ = (text, reply_to_topic, reply_to_msg_id)
                return 1

            async def build_context(self, raw_event) -> MessageContext:  # type: ignore[no-untyped-def]
                _ = raw_event
                return MessageContext(
                    chat_id=-100,
                    message_id=702,
                    reply_to_msg_id=900,
                    sender_id=999,
                    text="编辑后带图说明",
                    ts=datetime.now(timezone.utc),
                    is_reply=False,
                    is_reply_to_me=False,
                )

            async def run_forever(self) -> None:
                await asyncio.Future()

            async def stop(self) -> None:
                return None

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "app.sqlite3"
            repo = AccountRepository(str(path), logging.getLogger("test"))
            record = repo.create_account("alpha", _dummy_config(account_name="alpha"), enabled=True)
            system_config = SystemConfig(app_db_path=str(path), log_dir=str(Path(tmpdir) / "logs"))
            runner = AccountRunner(record, system_config)

            with patch("xiuxian_bot.runtime.Scheduler", FakeScheduler), patch(
                "xiuxian_bot.runtime.ReliableSender",
                FakeSender,
            ), patch("xiuxian_bot.runtime.TGAdapter", FakeAdapter), patch(
                "xiuxian_bot.runtime.build_plugins",
                return_value=[],
            ):
                await runner.start()
                await asyncio.sleep(0.05)
                await runner.stop()

            archive = MessageArchiveRepository(str(path), logging.getLogger("test"))
            rows = archive.search_messages(account_id=record.id)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].raw_text, "[image]\n编辑后带图说明")
            self.assertEqual(rows[0].event_type, "edit")
            archive.close()
            repo.close()


@unittest.skipUnless(HAS_WEB_DEPS, "requires fastapi/httpx/telethon dependencies")
class TestWebMessageArchive(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _fake_manager_cls(log_dir: Path):
        from xiuxian_bot.runtime import RunnerSnapshot

        class FakeManager:
            def __init__(self, repository, system_config) -> None:  # type: ignore[no-untyped-def]
                _ = repository
                _ = system_config
                self._snapshots: dict[int, RunnerSnapshot] = {}

            async def start_enabled_accounts(self) -> None:
                return None

            async def shutdown(self) -> None:
                return None

            async def start_account(
                self,
                account_id: int,
                *,
                respect_enabled: bool = False,
                clear_runtime_pause: bool = False,
            ) -> None:
                _ = (respect_enabled, clear_runtime_pause)
                self._snapshots[account_id] = RunnerSnapshot(
                    account_id=account_id,
                    state="running",
                    message="",
                    log_path=str(log_dir / f"account_{account_id}.log"),
                )

            async def stop_account(self, account_id: int) -> None:
                self._snapshots.pop(account_id, None)

            async def sync_account(self, account_id: int) -> None:
                _ = account_id
                return None

            def snapshots(self):
                return dict(self._snapshots)

            def snapshot_for(self, account_id: int):
                return self._snapshots.get(account_id)

        return FakeManager

    async def test_global_and_account_message_pages_support_search(self) -> None:
        import httpx

        from xiuxian_bot.core.message_archive_repository import (
            MessageArchiveInput,
            MessageArchiveRepository,
        )
        from xiuxian_bot.web import create_app

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            system_config = SystemConfig(
                app_db_path=str(tmp_path / "app.sqlite3"),
                log_dir=str(tmp_path / "logs"),
                web_admin_username="admin",
                web_admin_password="secret",
                web_secret_key="secret-key",
            )
            fake_manager = self._fake_manager_cls(Path(system_config.log_dir))

            with patch("xiuxian_bot.web.SystemConfig.load", return_value=system_config), patch(
                "xiuxian_bot.web.AccountRepository.ensure_legacy_account",
                return_value=None,
            ), patch("xiuxian_bot.web.RunnerManager", fake_manager):
                app = create_app()
                async with app.router.lifespan_context(app):
                    repository = AccountRepository(system_config.app_db_path, logging.getLogger("test"))
                    account = repository.create_account("alpha", _dummy_config(account_name="alpha"), enabled=True)
                    archive = MessageArchiveRepository(system_config.app_db_path, logging.getLogger("test"))
                    now = datetime.now(timezone.utc)
                    archive.archive_message(
                        MessageArchiveInput(
                            account_id=account.id,
                            chat_id=-100,
                            topic_id=900,
                            message_id=1,
                            reply_to_msg_id=900,
                            sender_id=111,
                            sender_name="tester",
                            identity_key="main",
                            raw_text="全局检索目标",
                            event_type="new",
                            message_ts=now,
                            is_reply=False,
                            is_topic_message=True,
                        )
                    )
                    archive.archive_message(
                        MessageArchiveInput(
                            account_id=account.id,
                            chat_id=-100,
                            topic_id=901,
                            message_id=2,
                            reply_to_msg_id=901,
                            sender_id=222,
                            sender_name="tester2",
                            identity_key=None,
                            raw_text="其他消息",
                            event_type="new",
                            message_ts=now,
                            is_reply=False,
                            is_topic_message=True,
                        )
                    )
                    archive.close()
                    conn = sqlite3.connect(system_config.app_db_path)
                    fixed_now = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
                    conn.execute(
                        "UPDATE message_archive SET captured_at = ? WHERE message_id = ?",
                        (fixed_now.isoformat(timespec="seconds"), 1),
                    )
                    conn.execute(
                        "UPDATE message_archive SET captured_at = ? WHERE message_id = ?",
                        ((fixed_now - timedelta(days=8)).isoformat(timespec="seconds"), 2),
                    )
                    conn.commit()
                    conn.close()
                    repository.close()

                    fixed_now = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
                    transport = httpx.ASGITransport(app=app)
                    with patch("xiuxian_bot.web._utc_now", return_value=fixed_now):
                        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                            response = await client.post(
                                "/login",
                                data={"username": "admin", "password": "secret"},
                                follow_redirects=False,
                            )
                            self.assertEqual(response.status_code, 303)

                            global_page = await client.get("/messages?q=全局检索")
                            self.assertEqual(global_page.status_code, 200)
                            self.assertIn("消息归档", global_page.text)
                            self.assertIn("全局检索目标", global_page.text)
                            self.assertIn("身份：主魂", global_page.text)
                            self.assertNotIn("其他消息", global_page.text)
                            self.assertIn("总条数：2", global_page.text)
                            self.assertIn("今日新增：1", global_page.text)
                            self.assertIn("近7日新增：1", global_page.text)
                            self.assertIn("近30日新增：2", global_page.text)
                            self.assertIn("SQLite大小：", global_page.text)

                            account_page = await client.get(f"/accounts/{account.id}/messages?q=全局检索")
                            self.assertEqual(account_page.status_code, 200)
                            self.assertIn(f"账号消息 #{account.id}", account_page.text)
                            self.assertIn("全局检索目标", account_page.text)
                            self.assertNotIn("其他消息", account_page.text)
                            self.assertIn("总条数：2", account_page.text)

    async def test_lifespan_cleanup_removes_expired_archive_rows(self) -> None:
        import httpx

        from xiuxian_bot.core.message_archive_repository import (
            MessageArchiveInput,
            MessageArchiveRepository,
        )
        from xiuxian_bot.web import create_app

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            system_config = SystemConfig(
                app_db_path=str(tmp_path / "app.sqlite3"),
                log_dir=str(tmp_path / "logs"),
                web_admin_username="admin",
                web_admin_password="secret",
                web_secret_key="secret-key",
                message_archive_cleanup_enabled=True,
                message_archive_retention_days=30,
                message_archive_vacuum_enabled=False,
            )
            fake_manager = self._fake_manager_cls(Path(system_config.log_dir))

            repository = AccountRepository(system_config.app_db_path, logging.getLogger("test"))
            account = repository.create_account("alpha", _dummy_config(account_name="alpha"), enabled=True)
            archive = MessageArchiveRepository(system_config.app_db_path, logging.getLogger("test"))
            now = datetime(2026, 4, 11, 4, 0, tzinfo=timezone.utc)
            archive.archive_message(
                MessageArchiveInput(
                    account_id=account.id,
                    chat_id=-100,
                    topic_id=900,
                    message_id=1,
                    reply_to_msg_id=900,
                    sender_id=111,
                    sender_name="tester",
                    identity_key=None,
                    raw_text="保留消息",
                    event_type="new",
                    message_ts=now,
                    is_reply=False,
                    is_topic_message=True,
                )
            )
            archive.archive_message(
                MessageArchiveInput(
                    account_id=account.id,
                    chat_id=-100,
                    topic_id=900,
                    message_id=2,
                    reply_to_msg_id=900,
                    sender_id=111,
                    sender_name="tester",
                    identity_key=None,
                    raw_text="过期消息",
                    event_type="new",
                    message_ts=now,
                    is_reply=False,
                    is_topic_message=True,
                )
            )
            archive.close()
            conn = sqlite3.connect(system_config.app_db_path)
            conn.execute(
                "UPDATE message_archive SET captured_at = ? WHERE message_id = ?",
                (datetime(2026, 4, 10, 0, 0, tzinfo=timezone.utc).isoformat(timespec="seconds"), 1),
            )
            conn.execute(
                "UPDATE message_archive SET captured_at = ? WHERE message_id = ?",
                (datetime(2026, 3, 12, 15, 59, tzinfo=timezone.utc).isoformat(timespec="seconds"), 2),
            )
            conn.commit()
            conn.close()
            repository.close()

            with patch("xiuxian_bot.web.SystemConfig.load", return_value=system_config), patch(
                "xiuxian_bot.web.AccountRepository.ensure_legacy_account",
                return_value=None,
            ), patch("xiuxian_bot.web.RunnerManager", fake_manager), patch(
                "xiuxian_bot.web._utc_now",
                return_value=now,
            ):
                app = create_app()
                async with app.router.lifespan_context(app):
                    transport = httpx.ASGITransport(app=app)
                    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                        response = await client.post(
                            "/login",
                            data={"username": "admin", "password": "secret"},
                            follow_redirects=False,
                        )
                        self.assertEqual(response.status_code, 303)
                        page = await client.get("/messages")
                        self.assertEqual(page.status_code, 200)
                        self.assertIn("保留消息", page.text)
                        self.assertNotIn("过期消息", page.text)


if __name__ == "__main__":
    unittest.main()
