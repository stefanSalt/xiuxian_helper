import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from xiuxian_bot.config import Config
from xiuxian_bot.core.state_store import SQLiteStateStore
from xiuxian_bot.plugins.random_text import RandomTextPlugin
from xiuxian_bot.runtime import build_plugins


def _dummy_config(**overrides) -> Config:
    values = {
        "tg_api_id": "1",
        "tg_api_hash": "hash",
        "tg_session_name": "session",
        "game_chat_id": "-100",
        "topic_id": "123",
        "my_name": "Me",
        "enable_random_text": True,
        "random_text_messages": "山风正好\n今日修行顺遂",
        "random_text_min_interval_seconds": "60",
        "random_text_max_interval_seconds": "60",
        "random_text_daily_limit": "1",
    }
    values.update(overrides)
    return Config.from_mapping(values)


class _FakeRng:
    def uniform(self, start: float, end: float) -> float:
        _ = end
        return start

    def choice(self, values):  # type: ignore[no-untyped-def]
        return values[0]


class TestRandomTextPlugin(unittest.IsolatedAsyncioTestCase):
    def test_build_plugins_includes_random_text(self) -> None:
        plugins = build_plugins(_dummy_config(), logging.getLogger("test"))
        self.assertIn("random_text", {plugin.name for plugin in plugins})

    async def test_bootstrap_does_not_schedule_initial_send(self) -> None:
        plugin = RandomTextPlugin(_dummy_config(), logging.getLogger("test"))
        calls: list[str] = []

        class _FakeScheduler:
            async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
                _ = (delay_seconds, action)
                calls.append(key)

        async def _send(_plugin: str, _text: str, _reply_to_topic: bool) -> int | None:
            return None

        await plugin.bootstrap(_FakeScheduler(), _send)
        self.assertEqual(calls, [])

    def test_empty_messages_never_selects_message(self) -> None:
        plugin = RandomTextPlugin(
            _dummy_config(random_text_messages="  \n"),
            logging.getLogger("test"),
        )

        self.assertIsNone(plugin.next_message())

    def test_initial_cooldown_then_daily_limit(self) -> None:
        now = datetime(2026, 5, 12, 6, 0, tzinfo=timezone.utc)

        def _now() -> datetime:
            return now

        with tempfile.TemporaryDirectory() as tmpdir:
            store = SQLiteStateStore(str(Path(tmpdir) / "state.sqlite3"))
            plugin = RandomTextPlugin(
                _dummy_config(),
                logging.getLogger("test"),
                now_fn=_now,
                rng=_FakeRng(),  # type: ignore[arg-type]
            )
            plugin.set_state_store(store)
            plugin.restore_state()

            self.assertIsNone(plugin.next_message())
            self.assertEqual(
                store.load_state("random_text")["next_allowed_at"],
                "2026-05-12T06:01:00+00:00",
            )

            now = now + timedelta(seconds=60)
            self.assertEqual(plugin.next_message(), "山风正好")
            plugin.mark_sent()
            self.assertIsNone(plugin.next_message())
            self.assertEqual(store.load_state("random_text")["sent_today"], 1)
            store.close()
