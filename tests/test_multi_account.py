import importlib.util
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xiuxian_bot.config import Config, SystemConfig
from xiuxian_bot.core.account_repository import AccountRepository
from xiuxian_bot.core.state_store import SQLiteStateStore


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


@unittest.skipUnless(HAS_WEB_DEPS, "requires fastapi/httpx/telethon dependencies")
class TestWebApp(unittest.TestCase):
    def test_login_and_create_account(self) -> None:
        from fastapi.testclient import TestClient

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

            with patch("xiuxian_bot.web.SystemConfig.load", return_value=system_config), patch(
                "xiuxian_bot.web.AccountRepository.ensure_legacy_account",
                return_value=None,
            ):
                with TestClient(create_app()) as client:
                    response = client.get("/", follow_redirects=False)
                    self.assertEqual(response.status_code, 303)
                    self.assertEqual(response.headers["location"], "/login")

                    response = client.post(
                        "/login",
                        data={"username": "admin", "password": "secret"},
                        follow_redirects=False,
                    )
                    self.assertEqual(response.status_code, 303)
                    self.assertEqual(response.headers["location"], "/")

                    create_response = client.post(
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

                    dashboard = client.get("/")
                    self.assertEqual(dashboard.status_code, 200)
                    self.assertIn("bot-1", dashboard.text)

    def test_healthz_returns_ok_without_auth(self) -> None:
        from fastapi.testclient import TestClient

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

            with patch("xiuxian_bot.web.SystemConfig.load", return_value=system_config), patch(
                "xiuxian_bot.web.AccountRepository.ensure_legacy_account",
                return_value=None,
            ):
                with TestClient(create_app()) as client:
                    response = client.get("/healthz")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["status"], "ok")
