import asyncio
import logging
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from xiuxian_bot.config import Config
from xiuxian_bot.core.contracts import MessageContext
from xiuxian_bot.core.identity_switch import (
    IdentitySwitchCoordinator,
    unique_best_identity_match,
)
from xiuxian_bot.core.state_store import SQLiteStateStore
from xiuxian_bot.domain.text_normalizer import normalize_match_text


def _config() -> Config:
    return Config.from_mapping(
        {
            "tg_api_id": "1",
            "tg_api_hash": "hash",
            "tg_session_name": "session",
            "game_chat_id": "-100",
            "topic_id": "123",
            "my_name": "主魂名",
            "active_identity_key": "main",
            "identity_profiles": [
                {
                    "key": "main",
                    "kind": "main",
                    "my_name": "主魂名",
                    "switch_target": "主魂",
                    "display_name": "主魂",
                },
                {
                    "key": "avatar_a",
                    "kind": "avatar",
                    "my_name": "同名道号",
                    "switch_target": "111111",
                    "display_name": "同名道号",
                    "game_id": "111111",
                },
                {
                    "key": "avatar_b",
                    "kind": "avatar",
                    "my_name": "同名道号",
                    "switch_target": "222222",
                    "display_name": "同名道号",
                    "game_id": "222222",
                },
            ],
        }
    )


def _ctx(text: str, *, reply_to_msg_id: int | None = None) -> MessageContext:
    return MessageContext(
        chat_id=-100,
        message_id=2001,
        reply_to_msg_id=reply_to_msg_id,
        sender_id=999,
        text=text,
        ts=datetime.now(timezone.utc),
        is_reply=reply_to_msg_id is not None,
        is_reply_to_me=reply_to_msg_id is not None,
    )


class TestIdentitySwitchMatching(unittest.IsolatedAsyncioTestCase):
    def test_unique_best_identity_match_returns_none_for_same_name_tie(self) -> None:
        config = _config()
        normalized = normalize_match_text("切换成功！你的神念已附着在【同名道号】之上。")

        matched = unique_best_identity_match(normalized, config.identities[1:])

        self.assertIsNone(matched)

    def test_unique_best_identity_match_uses_longer_unique_token(self) -> None:
        config = _config()
        normalized = normalize_match_text("切换成功！同名道号 222222")

        matched = unique_best_identity_match(normalized, config.identities[1:])

        assert matched is not None
        self.assertEqual(matched.key, "avatar_b")

    def test_ambiguous_global_switch_success_does_not_mark_first_identity(self) -> None:
        config = _config()
        with tempfile.TemporaryDirectory() as tmpdir:
            state_store = SQLiteStateStore(
                str(Path(tmpdir) / "state.sqlite3"),
                logging.getLogger("test"),
            )

            async def _send(*_args, **_kwargs) -> int | None:  # type: ignore[no-untyped-def]
                return 1001

            coordinator = IdentitySwitchCoordinator(
                config,
                state_store,
                logging.getLogger("test"),
                _send,
            )

            coordinator.observe(_ctx("切换成功！你的神念已附着在【同名道号】之上。"))

            self.assertEqual(coordinator.active_identity_key, "main")

    async def test_pending_switch_prefers_target_when_names_are_same(self) -> None:
        config = _config()
        sent: list[str] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            state_store = SQLiteStateStore(
                str(Path(tmpdir) / "state.sqlite3"),
                logging.getLogger("test"),
            )

            async def _send(_plugin: str, text: str, _reply_to_topic: bool) -> int | None:
                sent.append(text)
                return 1001

            coordinator = IdentitySwitchCoordinator(
                config,
                state_store,
                logging.getLogger("test"),
                _send,
            )

            task = asyncio.create_task(
                coordinator.ensure_identity(
                    "avatar_b",
                    timeout_seconds=1.0,
                    retry_delay_seconds=1.0,
                )
            )
            while not sent:
                await asyncio.sleep(0)

            coordinator.observe(_ctx("切换成功！你的神念已附着在【同名道号】之上。"))

            self.assertTrue(await asyncio.wait_for(task, timeout=1.0))
            self.assertEqual(sent, [".切换 222222"])
            self.assertEqual(coordinator.active_identity_key, "avatar_b")


if __name__ == "__main__":
    unittest.main()
