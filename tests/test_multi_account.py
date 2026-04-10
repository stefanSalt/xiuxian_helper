import asyncio
import importlib.util
import logging
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from xiuxian_bot.config import Config, SystemConfig
from xiuxian_bot.core.account_repository import AccountRepository
from xiuxian_bot.core.contracts import MessageContext, SendAction
from xiuxian_bot.core.state_store import SQLiteStateStore, serialize_datetime


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
        enable_biguan=True,
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
        enable_yuanying_liefeng=True,
        global_send_min_interval_seconds=10,
        state_db_path="xiuxian_app.sqlite3",
        enable_chuangta=False,
        chuangta_time="14:15",
        account_id="default",
        account_name="default",
    )
    values.update(overrides)
    return Config(**values)


HAS_RUNTIME_DEPS = importlib.util.find_spec("telethon") is not None
HAS_WEB_DEPS = (
    importlib.util.find_spec("fastapi") is not None
    and importlib.util.find_spec("httpx") is not None
    and HAS_RUNTIME_DEPS
)


class TestMultiAccountStorage(unittest.TestCase):
    def test_state_store_is_isolated_by_account_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.sqlite3"
            root = SQLiteStateStore(str(path))
            alpha = root.for_account("alpha")
            beta = root.for_account("beta")

            alpha.save_state("xinggong", {"count": 1})
            beta.save_state("xinggong", {"count": 2})

            self.assertEqual(alpha.load_state("xinggong"), {"count": 1})
            self.assertEqual(beta.load_state("xinggong"), {"count": 2})

            root.delete_account_states("alpha")
            self.assertEqual(alpha.load_state("xinggong"), {})
            self.assertEqual(beta.load_state("xinggong"), {"count": 2})
            root.close()

    def test_account_repository_crud_and_delete_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "app.sqlite3"
            logger = logging.getLogger("test")
            repo = AccountRepository(str(path), logger)

            created = repo.create_account("alpha", _dummy_config(account_name="alpha"), enabled=True)
            self.assertEqual(created.name, "alpha")
            self.assertTrue(created.enabled)

            store = SQLiteStateStore(str(path), logger, account_id=str(created.id))
            store.save_state("xinggong", {"phase": "ready"})
            store.close()

            updated = repo.update_account(
                created.id,
                "alpha-renamed",
                created.config.with_identity(account_id=str(created.id), account_name="alpha-renamed"),
                enabled=False,
            )
            self.assertEqual(updated.name, "alpha-renamed")
            self.assertFalse(updated.enabled)

            listed = repo.list_accounts()
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0].name, "alpha-renamed")

            repo.delete_account(created.id)
            self.assertEqual(repo.count_accounts(), 0)

            store = SQLiteStateStore(str(path), logger, account_id=str(created.id))
            self.assertEqual(store.load_state("xinggong"), {})
            store.close()
            repo.close()

    def test_reconcile_config_change_clears_scheduled_state(self) -> None:
        from xiuxian_bot.web import _reconcile_runtime_state_for_config_change

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "app.sqlite3"
            logger = logging.getLogger("test")
            garden_next = serialize_datetime(datetime.now())
            xinggong_next = serialize_datetime(datetime.now())
            wenan_next = serialize_datetime(datetime.now())
            qizhen_blocked = serialize_datetime(datetime.now())
            chuqiao_blocked = serialize_datetime(datetime.now())
            store = SQLiteStateStore(str(path), logger, account_id="1")
            store.save_state(
                "garden",
                {
                    "seed_insufficient": True,
                    "next_poll_at": garden_next,
                },
            )
            store.save_state(
                "xinggong",
                {
                    "next_poll_at": xinggong_next,
                    "wenan_next_at": wenan_next,
                    "qizhen_blocked_until": qizhen_blocked,
                },
            )
            store.save_state(
                "yuanying",
                {
                    "liefeng_blocked_until": serialize_datetime(datetime.now()),
                    "chuqiao_blocked_until": chuqiao_blocked,
                },
            )
            store.close()

            previous = _dummy_config(
                enable_garden=True,
                enable_xinggong=True,
                enable_yuanying=True,
                garden_poll_interval_seconds=3600,
                xinggong_poll_interval_seconds=3600,
                xinggong_wenan_interval_seconds=43200,
                yuanying_liefeng_interval_seconds=43200,
            )
            current = _dummy_config(
                enable_garden=True,
                enable_xinggong=True,
                enable_yuanying=True,
                garden_poll_interval_seconds=600,
                xinggong_poll_interval_seconds=600,
                xinggong_wenan_interval_seconds=3600,
                yuanying_liefeng_interval_seconds=1800,
            )

            _reconcile_runtime_state_for_config_change(
                db_path=str(path),
                account_id=1,
                previous_config=previous,
                current_config=current,
                logger=logger,
            )

            store = SQLiteStateStore(str(path), logger, account_id="1")
            self.assertEqual(store.load_state("garden"), {"seed_insufficient": True})
            self.assertEqual(
                store.load_state("xinggong"),
                {"qizhen_blocked_until": qizhen_blocked},
            )
            yuanying_state = store.load_state("yuanying")
            self.assertNotIn("liefeng_blocked_until", yuanying_state)
            self.assertEqual(yuanying_state["chuqiao_blocked_until"], chuqiao_blocked)
            store.close()

    def test_reconcile_config_change_preserves_yuanying_real_cooldown(self) -> None:
        from xiuxian_bot.web import _reconcile_runtime_state_for_config_change

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "app.sqlite3"
            logger = logging.getLogger("test")
            store = SQLiteStateStore(str(path), logger, account_id="1")
            blocked_until = serialize_datetime(datetime.now())
            store.save_state(
                "yuanying",
                {
                    "liefeng_blocked_until": blocked_until,
                    "liefeng_block_source": "cooldown",
                },
            )
            store.close()

            previous = _dummy_config(enable_yuanying=True, yuanying_liefeng_interval_seconds=43200)
            current = _dummy_config(enable_yuanying=True, yuanying_liefeng_interval_seconds=1800)

            _reconcile_runtime_state_for_config_change(
                db_path=str(path),
                account_id=1,
                previous_config=previous,
                current_config=current,
                logger=logger,
            )

            store = SQLiteStateStore(str(path), logger, account_id="1")
            self.assertEqual(
                store.load_state("yuanying"),
                {
                    "liefeng_blocked_until": blocked_until,
                    "liefeng_block_source": "cooldown",
                },
            )
            store.close()

    def test_ensure_legacy_account_migrates_first_account_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "app.sqlite3"
            repo = AccountRepository(str(path), logging.getLogger("test"))
            system_config = SystemConfig(
                app_db_path=str(path),
                default_account_name="legacy-user",
            )

            with patch(
                "xiuxian_bot.core.account_repository.Config.load_legacy_env",
                return_value=_dummy_config(account_name=""),
            ):
                record = repo.ensure_legacy_account(system_config)
                self.assertIsNotNone(record)
                assert record is not None
                self.assertEqual(record.name, "legacy-user")
                self.assertEqual(repo.count_accounts(), 1)
                self.assertIsNone(repo.ensure_legacy_account(system_config))
            repo.close()

    def test_runtime_resolves_relative_session_name_into_session_root_dir(self) -> None:
        if not HAS_RUNTIME_DEPS:
            self.skipTest("requires telethon runtime dependencies")
        from xiuxian_bot.runtime import _resolve_session_name

        with tempfile.TemporaryDirectory() as tmpdir:
            system_config = SystemConfig(session_root_dir=str(Path(tmpdir) / "sessions"))
            resolved = _resolve_session_name(system_config, "bot-1")
            self.assertEqual(resolved, str(Path(tmpdir) / "sessions" / "bot-1"))
            self.assertTrue((Path(tmpdir) / "sessions").exists())

    def test_build_account_logger_closes_previous_file_handler(self) -> None:
        from xiuxian_bot.runtime import build_account_logger

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "app.sqlite3"
            repo = AccountRepository(str(path), logging.getLogger("test"))
            record = repo.create_account("alpha", _dummy_config(account_name="alpha"), enabled=True)
            system_config = SystemConfig(app_db_path=str(path), log_dir=str(Path(tmpdir) / "logs"))

            logger, _ = build_account_logger(system_config, record)
            first_file_handler = next(
                handler for handler in logger.handlers if isinstance(handler, logging.FileHandler)
            )

            logger, _ = build_account_logger(system_config, record)
            self.assertIsNone(first_file_handler.stream)

            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            repo.close()


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requires telethon runtime dependencies")
class TestRunnerManager(unittest.IsolatedAsyncioTestCase):
    async def test_runner_manager_starts_enabled_and_stops_disabled_account(self) -> None:
        from xiuxian_bot.runtime import RunnerManager, RunnerSnapshot

        events: list[tuple[str, int]] = []

        class FakeRunner:
            def __init__(self, record, system_config) -> None:  # type: ignore[no-untyped-def]
                self.record = record
                self.system_config = system_config

            async def start(self) -> None:
                events.append(("start", self.record.id))

            async def stop(self) -> None:
                events.append(("stop", self.record.id))

            def snapshot(self) -> RunnerSnapshot:
                return RunnerSnapshot(
                    account_id=self.record.id,
                    state="running",
                    message="",
                    log_path=f"/tmp/account_{self.record.id}.log",
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "app.sqlite3"
            repo = AccountRepository(str(path), logging.getLogger("test"))
            record = repo.create_account("alpha", _dummy_config(account_name="alpha"), enabled=True)
            system_config = SystemConfig(app_db_path=str(path), log_dir=str(Path(tmpdir) / "logs"))

            with patch("xiuxian_bot.runtime.AccountRunner", FakeRunner):
                manager = RunnerManager(repo, system_config)
                await manager.start_enabled_accounts()
                self.assertEqual(events, [("start", record.id)])
                self.assertIsNotNone(manager.snapshot_for(record.id))

                repo.update_account(record.id, record.name, record.config, enabled=False)
                await manager.sync_account(record.id)
                self.assertEqual(events[-1], ("stop", record.id))
                self.assertIsNone(manager.snapshot_for(record.id))

            repo.close()

    async def test_runner_manager_start_account_marks_manual_resume(self) -> None:
        from xiuxian_bot.runtime import RunnerManager, RunnerSnapshot

        events: list[tuple[str, int, bool]] = []

        class FakeRunner:
            def __init__(self, record, system_config) -> None:  # type: ignore[no-untyped-def]
                self.record = record
                self.system_config = system_config
                self.manual_resume = False

            def set_manual_resume(self, enabled: bool) -> None:
                self.manual_resume = enabled

            async def start(self) -> None:
                events.append(("start", self.record.id, self.manual_resume))

            async def stop(self) -> None:
                events.append(("stop", self.record.id, self.manual_resume))

            def snapshot(self) -> RunnerSnapshot:
                return RunnerSnapshot(
                    account_id=self.record.id,
                    state="paused" if self.manual_resume else "running",
                    message="",
                    log_path=f"/tmp/account_{self.record.id}.log",
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "app.sqlite3"
            repo = AccountRepository(str(path), logging.getLogger("test"))
            record = repo.create_account("alpha", _dummy_config(account_name="alpha"), enabled=True)
            system_config = SystemConfig(app_db_path=str(path), log_dir=str(Path(tmpdir) / "logs"))

            with patch("xiuxian_bot.runtime.AccountRunner", FakeRunner):
                manager = RunnerManager(repo, system_config)
                await manager.start_account(record.id, clear_runtime_pause=True)
                self.assertEqual(events, [("start", record.id, True)])

            repo.close()

    async def test_account_runner_enters_paused_state_and_suppresses_actions_immediately(self) -> None:
        from xiuxian_bot.runtime import AccountRunner

        sends: list[tuple[str, str]] = []
        cancel_calls: list[str] = []

        class FakeScheduler:
            def __init__(self, logger) -> None:  # type: ignore[no-untyped-def]
                self.logger = logger

            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                return None

            async def cancel_all(self) -> None:
                cancel_calls.append("cancel")

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
            ) -> int | None:
                sends.append((plugin, text))
                return 1

        class FakeAdapter:
            def __init__(self, config, logger) -> None:  # type: ignore[no-untyped-def]
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
                asyncio.create_task(self._handler(object()))

            async def send_message(
                self,
                text: str,
                *,
                reply_to_topic: bool = True,
                reply_to_msg_id: int | None = None,
            ) -> int | None:
                _ = (text, reply_to_topic, reply_to_msg_id)
                return 1

            async def build_context(self, event) -> MessageContext:  # type: ignore[no-untyped-def]
                _ = event
                return MessageContext(
                    chat_id=-100,
                    message_id=1,
                    reply_to_msg_id=123,
                    sender_id=999,
                    text="【元婴遁逃·虚弱】千钧一发之际，你的元婴带着你的三魂七魄，从破碎的肉身中遁出！但你的神魂遭受重创，已陷入6小时的【虚弱期】！",
                    ts=datetime.now(timezone.utc),
                    is_reply=True,
                    is_reply_to_me=True,
                )

            async def run_forever(self) -> None:
                await asyncio.Future()

            async def stop(self) -> None:
                return None

        class PausePlugin:
            name = "yuanying"
            enabled = True
            priority = 100

            def __init__(self) -> None:
                self.paused = False

            def set_state_store(self, store) -> None:  # type: ignore[no-untyped-def]
                _ = store

            def restore_state(self) -> None:
                return None

            def runtime_pause_reason(self) -> str | None:
                if self.paused:
                    return "元婴遁逃暂停中，等待手动恢复"
                return None

            async def on_message(self, ctx: MessageContext) -> list[SendAction] | None:
                _ = ctx
                self.paused = True
                return None

        class OtherPlugin:
            name = "other"
            enabled = True
            priority = 10

            async def on_message(self, ctx: MessageContext) -> list[SendAction] | None:
                _ = ctx
                return [
                    SendAction(
                        plugin="other",
                        text=".会被暂停",
                        reply_to_topic=True,
                        delay_seconds=0.0,
                        key="other.immediate",
                    )
                ]

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "app.sqlite3"
            repo = AccountRepository(str(path), logging.getLogger("test"))
            record = repo.create_account(
                "alpha",
                _dummy_config(account_name="alpha", enable_yuanying=True),
                enabled=True,
            )
            system_config = SystemConfig(app_db_path=str(path), log_dir=str(Path(tmpdir) / "logs"))
            runner = AccountRunner(record, system_config)

            with patch("xiuxian_bot.runtime.Scheduler", FakeScheduler), patch(
                "xiuxian_bot.runtime.ReliableSender",
                FakeSender,
            ), patch("xiuxian_bot.runtime.TGAdapter", FakeAdapter), patch(
                "xiuxian_bot.runtime.build_plugins",
                return_value=[PausePlugin(), OtherPlugin()],
            ):
                await runner.start()
                await asyncio.sleep(0.05)
                snapshot = runner.snapshot()
                self.assertEqual(snapshot.state, "paused")
                self.assertEqual(snapshot.message, "元婴遁逃暂停中，等待手动恢复")
                self.assertEqual(sends, [])
                self.assertTrue(cancel_calls)
                await runner.stop()

            repo.close()


@unittest.skipUnless(HAS_WEB_DEPS, "requires fastapi/httpx/telethon dependencies")
class TestWebApp(unittest.IsolatedAsyncioTestCase):
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
                _ = respect_enabled
                self._snapshots[account_id] = RunnerSnapshot(
                    account_id=account_id,
                    state="paused" if clear_runtime_pause else "running",
                    message="元婴遁逃暂停中，等待手动恢复" if clear_runtime_pause else "",
                    log_path=str(log_dir / f"account_{account_id}.log"),
                )

            async def stop_account(self, account_id: int) -> None:
                self._snapshots.pop(account_id, None)

            async def sync_account(self, account_id: int) -> None:
                _ = account_id
                return None

            def snapshots(self) -> dict[int, RunnerSnapshot]:
                return dict(self._snapshots)

            def snapshot_for(self, account_id: int) -> RunnerSnapshot | None:
                return self._snapshots.get(account_id)

        return FakeManager

    async def test_login_and_create_account(self) -> None:
        import httpx

        from xiuxian_bot.web import create_app

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            system_config = SystemConfig(
                app_db_path=str(tmp_path / "app.sqlite3"),
                log_dir=str(tmp_path / "logs"),
                web_admin_username="admin",
                web_admin_password="secret",
                web_secret_key="secret-key",
                default_account_name="default",
            )
            fake_manager = self._fake_manager_cls(Path(system_config.log_dir))

            with patch("xiuxian_bot.web.SystemConfig.load", return_value=system_config), patch(
                "xiuxian_bot.web.AccountRepository.ensure_legacy_account",
                return_value=None,
            ), patch("xiuxian_bot.web.RunnerManager", fake_manager):
                app = create_app()
                async with app.router.lifespan_context(app):
                    transport = httpx.ASGITransport(app=app)
                    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                        response = await client.get("/", follow_redirects=False)
                        self.assertEqual(response.status_code, 303)
                        self.assertEqual(response.headers["location"], "/login")

                        response = await client.post(
                            "/login",
                            data={"username": "admin", "password": "secret"},
                            follow_redirects=False,
                        )
                        self.assertEqual(response.status_code, 303)
                        self.assertEqual(response.headers["location"], "/")

                        create_response = await client.post(
                            "/accounts/new",
                            data={
                                "name": "bot-1",
                                "tg_api_id": "10001",
                                "tg_api_hash": "hash",
                                "tg_session_name": "session-bot-1",
                                "game_chat_id": "-100123",
                                "topic_id": "12345",
                                "my_name": "BotOne",
                                "send_to_topic": "on",
                                "enable_biguan": "on",
                                "action_cmd_biguan": ".闭关修炼",
                                "log_level": "INFO",
                                "global_sends_per_minute": "6",
                                "plugin_sends_per_minute": "3",
                                "global_send_min_interval_seconds": "10",
                                "biguan_extra_buffer_seconds": "60",
                                "biguan_cooldown_jitter_min_seconds": "5",
                                "biguan_cooldown_jitter_max_seconds": "15",
                                "biguan_retry_jitter_min_seconds": "3",
                                "biguan_retry_jitter_max_seconds": "8",
                                "garden_seed_name": "清灵草种子",
                                "garden_poll_interval_seconds": "3600",
                                "garden_action_spacing_seconds": "25",
                                "xinggong_star_name": "庚金星",
                                "xinggong_poll_interval_seconds": "3600",
                                "xinggong_action_spacing_seconds": "25",
                                "xinggong_qizhen_start_time": "07:00",
                                "xinggong_qizhen_retry_interval_seconds": "120",
                                "xinggong_qizhen_second_offset_seconds": "43500",
                                "xinggong_wenan_interval_seconds": "43200",
                                "yuanying_liefeng_interval_seconds": "43200",
                                "yuanying_chuqiao_interval_seconds": "28800",
                                "enable_xinggong_wenan": "on",
                                "xinggong_guanxing_target_username": "salt9527",
                                "xinggong_guanxing_preview_advance_seconds": "180",
                                "xinggong_guanxing_shift_advance_seconds": "1",
                                "xinggong_guanxing_watch_events": "星辰异象,地磁暴动",
                                "enable_chuangta": "",
                                "chuangta_time": "14:15",
                                "enable_lingxiaogong": "on",
                                "enable_lingxiaogong_wenxintai": "on",
                                "enable_lingxiaogong_dengtianjie": "on",
                                "lingxiaogong_poll_interval_seconds": "300",
                                "zongmen_cmd_dianmao": ".宗门点卯",
                                "zongmen_dianmao_time": "",
                                "zongmen_cmd_chuangong": ".宗门传功",
                                "zongmen_chuangong_times": "",
                                "zongmen_chuangong_xinde_text": "今日修行心得：稳中求进。",
                                "zongmen_action_spacing_seconds": "20",
                            },
                            follow_redirects=False,
                        )
                        self.assertEqual(create_response.status_code, 303)
                        self.assertEqual(create_response.headers["location"], "/")

                        dashboard = await client.get("/")
                        self.assertEqual(dashboard.status_code, 200)
                        self.assertIn("bot-1", dashboard.text)

                        edit_page = await client.get("/accounts/1/edit")
                        self.assertEqual(edit_page.status_code, 200)
                        self.assertIn("编辑账号 #1", edit_page.text)
                        self.assertIn("凌霄宫", edit_page.text)

                        logs_page = await client.get("/accounts/1/logs")
                        self.assertEqual(logs_page.status_code, 200)
                        self.assertIn("账号日志 #1 - bot-1", logs_page.text)

    async def test_healthz_returns_ok_without_auth(self) -> None:
        import httpx

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
                    transport = httpx.ASGITransport(app=app)
                    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                        response = await client.get("/healthz")
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.json()["status"], "ok")
