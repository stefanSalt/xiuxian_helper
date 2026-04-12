import os
import unittest
from unittest.mock import patch

from xiuxian_bot.config import Config, SystemConfig


class TestConfig(unittest.TestCase):
    def test_from_mapping_accepts_lingxiaogong_fields(self) -> None:
        config = Config.from_mapping(
            {
                "tg_api_id": "1",
                "tg_api_hash": "hash",
                "tg_session_name": "session",
                "game_chat_id": "-100",
                "topic_id": "123",
                "my_name": "Me",
                "enable_lingxiaogong": "true",
                "enable_lingxiaogong_wenxintai": "false",
                "enable_lingxiaogong_jiutian": "true",
                "enable_lingxiaogong_dengtianjie": "true",
                "lingxiaogong_poll_interval_seconds": "180",
                "lingxiaogong_wenxintai_after_climb_count": "4",
            }
        )

        self.assertTrue(config.enable_lingxiaogong)
        self.assertFalse(config.enable_lingxiaogong_wenxintai)
        self.assertTrue(config.enable_lingxiaogong_jiutian)
        self.assertTrue(config.enable_lingxiaogong_dengtianjie)
        self.assertEqual(config.lingxiaogong_poll_interval_seconds, 180)
        self.assertEqual(config.lingxiaogong_wenxintai_after_climb_count, 4)

    def test_from_mapping_accepts_fractional_xinggong_shift_advance_seconds(self) -> None:
        config = Config.from_mapping(
            {
                "tg_api_id": "1",
                "tg_api_hash": "hash",
                "tg_session_name": "session",
                "game_chat_id": "-100",
                "topic_id": "123",
                "my_name": "Me",
                "xinggong_guanxing_shift_advance_seconds": "0.25",
            }
        )

        self.assertEqual(config.xinggong_guanxing_shift_advance_seconds, 0.25)

    def test_from_mapping_accepts_negative_xinggong_shift_advance_seconds(self) -> None:
        config = Config.from_mapping(
            {
                "tg_api_id": "1",
                "tg_api_hash": "hash",
                "tg_session_name": "session",
                "game_chat_id": "-100",
                "topic_id": "123",
                "my_name": "Me",
                "xinggong_guanxing_shift_advance_seconds": "-0.5",
            }
        )

        self.assertEqual(config.xinggong_guanxing_shift_advance_seconds, -0.5)

    def test_load_legacy_env_accepts_fractional_shift_advance_seconds(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TG_API_ID": "1",
                "TG_API_HASH": "hash",
                "TG_SESSION_NAME": "session",
                "GAME_CHAT_ID": "-100",
                "TOPIC_ID": "123",
                "MY_NAME": "Me",
                "XINGGONG_GUANXING_SHIFT_ADVANCE_SECONDS": "0.2",
            },
            clear=True,
        ):
            config = Config.load_legacy_env()

        assert config is not None
        self.assertEqual(config.xinggong_guanxing_shift_advance_seconds, 0.2)

    def test_load_legacy_env_accepts_negative_shift_advance_seconds(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TG_API_ID": "1",
                "TG_API_HASH": "hash",
                "TG_SESSION_NAME": "session",
                "GAME_CHAT_ID": "-100",
                "TOPIC_ID": "123",
                "MY_NAME": "Me",
                "XINGGONG_GUANXING_SHIFT_ADVANCE_SECONDS": "-0.2",
            },
            clear=True,
        ):
            config = Config.load_legacy_env()

        assert config is not None
        self.assertEqual(config.xinggong_guanxing_shift_advance_seconds, -0.2)

    def test_load_legacy_env_accepts_lingxiaogong_fields(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TG_API_ID": "1",
                "TG_API_HASH": "hash",
                "TG_SESSION_NAME": "session",
                "GAME_CHAT_ID": "-100",
                "TOPIC_ID": "123",
                "MY_NAME": "Me",
                "ENABLE_LINGXIAOGONG": "true",
                "ENABLE_LINGXIAOGONG_WENXINTAI": "false",
                "ENABLE_LINGXIAOGONG_JIUTIAN": "true",
                "ENABLE_LINGXIAOGONG_DENGTIANJIE": "true",
                "LINGXIAOGONG_POLL_INTERVAL_SECONDS": "240",
                "LINGXIAOGONG_WENXINTAI_AFTER_CLIMB_COUNT": "5",
            },
            clear=True,
        ):
            config = Config.load_legacy_env()

        assert config is not None
        self.assertTrue(config.enable_lingxiaogong)
        self.assertFalse(config.enable_lingxiaogong_wenxintai)
        self.assertTrue(config.enable_lingxiaogong_jiutian)
        self.assertTrue(config.enable_lingxiaogong_dengtianjie)
        self.assertEqual(config.lingxiaogong_poll_interval_seconds, 240)
        self.assertEqual(config.lingxiaogong_wenxintai_after_climb_count, 5)

    def test_system_config_load_accepts_message_archive_retention_fields(self) -> None:
        with patch("xiuxian_bot.config._load_dotenv", return_value=None), patch.dict(
            os.environ,
            {
                "MESSAGE_ARCHIVE_CLEANUP_ENABLED": "0",
                "MESSAGE_ARCHIVE_RETENTION_DAYS": "14",
                "MESSAGE_ARCHIVE_VACUUM_ENABLED": "0",
            },
            clear=True,
        ):
            config = SystemConfig.load()

        self.assertFalse(config.message_archive_cleanup_enabled)
        self.assertEqual(config.message_archive_retention_days, 14)
        self.assertFalse(config.message_archive_vacuum_enabled)


if __name__ == "__main__":
    unittest.main()
