from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import SystemConfig


SHARED_SYSTEM_SETTING_KEYS: tuple[str, ...] = (
    "tg_api_id",
    "tg_api_hash",
    "game_chat_id",
    "topic_id",
    "send_to_topic",
    "system_reply_source_usernames",
)


class SystemSettingsRepository:
    def __init__(self, path: str, logger: logging.Logger | None = None) -> None:
        base = Path(path).expanduser()
        self._path = base if base.is_absolute() else (Path.cwd() / base)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._logger = logger
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def load_shared_settings(self) -> dict[str, Any]:
        placeholders = ",".join("?" for _ in SHARED_SYSTEM_SETTING_KEYS)
        rows = self._conn.execute(
            f"SELECT key, value_json FROM app_settings WHERE key IN ({placeholders})",
            SHARED_SYSTEM_SETTING_KEYS,
        ).fetchall()
        values: dict[str, Any] = {}
        for row in rows:
            key = str(row["key"])
            try:
                values[key] = json.loads(row["value_json"])
            except json.JSONDecodeError:
                if self._logger is not None:
                    self._logger.warning("app_setting_invalid_json key=%s", key)
        return values

    def apply_to_config(self, config: SystemConfig) -> SystemConfig:
        values = self.load_shared_settings()
        if not values:
            return config
        return replace(config, **values)

    def save_shared_settings(self, values: dict[str, Any]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        rows = [
            (key, json.dumps(values[key], ensure_ascii=False, separators=(",", ":")), now)
            for key in SHARED_SYSTEM_SETTING_KEYS
            if key in values
        ]
        self._conn.executemany(
            """
            INSERT INTO app_settings(key, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            rows,
        )
        self._conn.commit()
