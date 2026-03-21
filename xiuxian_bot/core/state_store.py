from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any


def serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="seconds")


def deserialize_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def serialize_date(value: date | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def deserialize_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class SQLiteStateStore:
    def __init__(
        self,
        path: str,
        logger: logging.Logger | None = None,
        *,
        account_id: str = "__global__",
        conn: sqlite3.Connection | None = None,
        owns_connection: bool = True,
    ) -> None:
        base = Path(path).expanduser()
        self._path = base if base.is_absolute() else (Path.cwd() / base)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._logger = logger
        self._account_id = str(account_id)
        self._owns_connection = owns_connection
        self._conn = conn or sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS plugin_state (
                account_id TEXT NOT NULL,
                plugin TEXT NOT NULL,
                state_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (account_id, plugin)
            )
            """
        )
        self._conn.commit()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def account_id(self) -> str:
        return self._account_id

    def for_account(self, account_id: str) -> "SQLiteStateStore":
        return SQLiteStateStore(
            str(self._path),
            self._logger,
            account_id=account_id,
            conn=self._conn,
            owns_connection=False,
        )

    def load_state(self, plugin: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT state_json FROM plugin_state WHERE account_id = ? AND plugin = ?",
            (self._account_id, plugin),
        ).fetchone()
        if row is None:
            return {}
        try:
            data = json.loads(row["state_json"])
        except json.JSONDecodeError:
            if self._logger is not None:
                self._logger.warning(
                    "state_store_corrupt account_id=%s plugin=%s path=%s",
                    self._account_id,
                    plugin,
                    self._path,
                )
            return {}
        return data if isinstance(data, dict) else {}

    def save_state(self, plugin: str, state: dict[str, Any]) -> None:
        payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        updated_at = datetime.now().isoformat(timespec="seconds")
        self._conn.execute(
            """
            INSERT INTO plugin_state(account_id, plugin, state_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(account_id, plugin) DO UPDATE SET
                state_json = excluded.state_json,
                updated_at = excluded.updated_at
            """,
            (self._account_id, plugin, payload, updated_at),
        )
        self._conn.commit()

    def delete_account_states(self, account_id: str) -> None:
        self._conn.execute("DELETE FROM plugin_state WHERE account_id = ?", (str(account_id),))
        self._conn.commit()

    def close(self) -> None:
        if self._owns_connection:
            self._conn.close()
