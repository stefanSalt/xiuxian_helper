from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..config import Config, SystemConfig
from .state_store import SQLiteStateStore


@dataclass(frozen=True)
class AccountRecord:
    id: int
    name: str
    enabled: bool
    config: Config
    created_at: str
    updated_at: str


class AccountRepository:
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
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1,
                config_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        self._conn.close()

    def list_accounts(self) -> list[AccountRecord]:
        rows = self._conn.execute(
            "SELECT id, name, enabled, config_json, created_at, updated_at FROM accounts ORDER BY id"
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def get_account(self, account_id: int) -> AccountRecord | None:
        row = self._conn.execute(
            "SELECT id, name, enabled, config_json, created_at, updated_at FROM accounts WHERE id = ?",
            (int(account_id),),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def create_account(self, name: str, config: Config, *, enabled: bool) -> AccountRecord:
        now = datetime.now().isoformat(timespec="seconds")
        payload = self._dump_config(config)
        cursor = self._conn.execute(
            """
            INSERT INTO accounts(name, enabled, config_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name.strip(), 1 if enabled else 0, payload, now, now),
        )
        self._conn.commit()
        created = self.get_account(int(cursor.lastrowid))
        if created is None:
            raise RuntimeError("failed to load created account")
        return created

    def update_account(self, account_id: int, name: str, config: Config, *, enabled: bool) -> AccountRecord:
        now = datetime.now().isoformat(timespec="seconds")
        payload = self._dump_config(config)
        self._conn.execute(
            """
            UPDATE accounts
            SET name = ?, enabled = ?, config_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (name.strip(), 1 if enabled else 0, payload, now, int(account_id)),
        )
        self._conn.commit()
        updated = self.get_account(account_id)
        if updated is None:
            raise RuntimeError("failed to load updated account")
        return updated

    def delete_account(self, account_id: int) -> None:
        self._conn.execute("DELETE FROM accounts WHERE id = ?", (int(account_id),))
        self._conn.commit()
        state_store = SQLiteStateStore(str(self._path), self._logger)
        state_store.delete_account_state_prefix(str(account_id))
        state_store.close()

    def count_accounts(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS c FROM accounts").fetchone()
        return int(row["c"]) if row is not None else 0

    def ensure_legacy_account(self, system_config: SystemConfig) -> AccountRecord | None:
        if self.count_accounts() > 0:
            return None
        legacy = Config.load_legacy_env()
        if legacy is None:
            return None
        name = legacy.account_name.strip() or system_config.default_account_name
        return self.create_account(
            name,
            legacy.with_identity(
                account_id="0",
                account_name=name,
                state_db_path=system_config.app_db_path,
            ),
            enabled=True,
        )

    def _row_to_record(self, row: sqlite3.Row) -> AccountRecord:
        try:
            payload = json.loads(row["config_json"])
        except json.JSONDecodeError as exc:
            raise ValueError(f"Corrupt account config for account_id={row['id']}") from exc
        config = Config.from_mapping(payload).with_identity(
            account_id=str(row["id"]),
            account_name=row["name"],
        )
        return AccountRecord(
            id=int(row["id"]),
            name=row["name"],
            enabled=bool(row["enabled"]),
            config=config,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _dump_config(self, config: Config) -> str:
        payload = config.to_dict()
        payload["account_id"] = ""
        payload["account_name"] = ""
        if payload.get("identity_profiles"):
            payload["identity_profiles"] = [
                {
                    **profile,
                    "config_overrides": dict(profile.get("config_overrides") or {}),
                }
                for profile in payload["identity_profiles"]
            ]
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
