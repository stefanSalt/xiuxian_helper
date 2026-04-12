from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..domain.text_normalizer import normalize_match_text


_BEIJING_TZ = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class MessageArchiveInput:
    account_id: int
    chat_id: int
    topic_id: int | None
    message_id: int
    reply_to_msg_id: int | None
    sender_id: int | None
    sender_name: str | None
    raw_text: str
    event_type: str
    message_ts: datetime
    is_reply: bool
    is_topic_message: bool


@dataclass(frozen=True)
class MessageArchiveRecord:
    id: int
    account_id: int
    chat_id: int
    topic_id: int | None
    message_id: int
    reply_to_msg_id: int | None
    sender_id: int | None
    sender_name: str | None
    raw_text: str
    normalized_text: str
    event_type: str
    message_ts: str
    captured_at: str
    edit_version: int
    is_reply: bool
    is_topic_message: bool


@dataclass(frozen=True)
class MessageArchiveStats:
    total_count: int
    today_count: int
    last_7_days_count: int
    last_30_days_count: int


@dataclass(frozen=True)
class MessageArchiveCleanupResult:
    before_count: int
    deleted_count: int
    after_count: int
    vacuum_attempted: bool
    vacuum_succeeded: bool


class MessageArchiveRepository:
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
            CREATE TABLE IF NOT EXISTS message_archive (
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
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_message_archive_lookup
            ON message_archive(account_id, topic_id, event_type, message_ts DESC, id DESC)
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_message_archive_message
            ON message_archive(account_id, message_id, edit_version DESC)
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_message_archive_sender
            ON message_archive(sender_id, message_ts DESC)
            """
        )
        self._conn.commit()

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        self._conn.close()

    def archive_message(self, payload: MessageArchiveInput) -> int:
        normalized_text = normalize_match_text(payload.raw_text)
        captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        message_ts = self._serialize_datetime(payload.message_ts)
        row = self._conn.execute(
            """
            SELECT COALESCE(MAX(edit_version), -1) AS max_version
            FROM message_archive
            WHERE account_id = ? AND message_id = ?
            """,
            (int(payload.account_id), int(payload.message_id)),
        ).fetchone()
        edit_version = int(row["max_version"]) + 1 if row is not None else 0
        cursor = self._conn.execute(
            """
            INSERT INTO message_archive(
                account_id,
                chat_id,
                topic_id,
                message_id,
                reply_to_msg_id,
                sender_id,
                sender_name,
                raw_text,
                normalized_text,
                event_type,
                message_ts,
                captured_at,
                edit_version,
                is_reply,
                is_topic_message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(payload.account_id),
                int(payload.chat_id),
                int(payload.topic_id) if payload.topic_id is not None else None,
                int(payload.message_id),
                int(payload.reply_to_msg_id) if payload.reply_to_msg_id is not None else None,
                int(payload.sender_id) if payload.sender_id is not None else None,
                (payload.sender_name or "").strip() or None,
                payload.raw_text,
                normalized_text,
                payload.event_type.strip(),
                message_ts,
                captured_at,
                edit_version,
                1 if payload.is_reply else 0,
                1 if payload.is_topic_message else 0,
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def search_messages(
        self,
        *,
        query: str | None = None,
        account_id: int | None = None,
        topic_id: int | None = None,
        sender_id: int | None = None,
        event_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MessageArchiveRecord]:
        sql, params = self._build_search_sql(
            count_only=False,
            query=query,
            account_id=account_id,
            topic_id=topic_id,
            sender_id=sender_id,
            event_type=event_type,
            limit=limit,
            offset=offset,
        )
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def count_messages(
        self,
        *,
        query: str | None = None,
        account_id: int | None = None,
        topic_id: int | None = None,
        sender_id: int | None = None,
        event_type: str | None = None,
    ) -> int:
        sql, params = self._build_search_sql(
            count_only=True,
            query=query,
            account_id=account_id,
            topic_id=topic_id,
            sender_id=sender_id,
            event_type=event_type,
            limit=0,
            offset=0,
        )
        row = self._conn.execute(sql, params).fetchone()
        return int(row["c"]) if row is not None else 0

    def get_stats(
        self,
        *,
        account_id: int | None = None,
        now: datetime | None = None,
    ) -> MessageArchiveStats:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        else:
            current = current.astimezone(timezone.utc)
        current_local = current.astimezone(_BEIJING_TZ)
        today_start_local = current_local.replace(hour=0, minute=0, second=0, microsecond=0)
        last_7_start_local = today_start_local - timedelta(days=6)
        last_30_start_local = today_start_local - timedelta(days=29)

        clauses = ["1 = 1"]
        params: list[object] = [
            last_30_start_local.astimezone(timezone.utc).isoformat(timespec="seconds"),
            last_7_start_local.astimezone(timezone.utc).isoformat(timespec="seconds"),
            today_start_local.astimezone(timezone.utc).isoformat(timespec="seconds"),
        ]
        if account_id is not None:
            clauses.append("account_id = ?")
            params.append(int(account_id))
        where_sql = " AND ".join(clauses)
        row = self._conn.execute(
            f"""
            SELECT
                COUNT(*) AS total_count,
                COALESCE(SUM(CASE WHEN captured_at >= ? THEN 1 ELSE 0 END), 0) AS last_30_days_count,
                COALESCE(SUM(CASE WHEN captured_at >= ? THEN 1 ELSE 0 END), 0) AS last_7_days_count,
                COALESCE(SUM(CASE WHEN captured_at >= ? THEN 1 ELSE 0 END), 0) AS today_count
            FROM message_archive
            WHERE {where_sql}
            """,
            params,
        ).fetchone()
        if row is None:
            return MessageArchiveStats(0, 0, 0, 0)
        return MessageArchiveStats(
            total_count=int(row["total_count"]),
            today_count=int(row["today_count"]),
            last_7_days_count=int(row["last_7_days_count"]),
            last_30_days_count=int(row["last_30_days_count"]),
        )

    def cleanup_old_messages(
        self,
        *,
        retention_days: int,
        now: datetime | None = None,
        vacuum: bool = False,
    ) -> MessageArchiveCleanupResult:
        before_count = self.count_messages()
        if retention_days <= 0 or before_count <= 0:
            return MessageArchiveCleanupResult(
                before_count=before_count,
                deleted_count=0,
                after_count=before_count,
                vacuum_attempted=False,
                vacuum_succeeded=False,
            )

        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        else:
            current = current.astimezone(timezone.utc)
        cutoff_local = current.astimezone(_BEIJING_TZ).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        ) - timedelta(days=max(retention_days - 1, 0))
        cutoff_utc = cutoff_local.astimezone(timezone.utc).isoformat(timespec="seconds")

        cursor = self._conn.execute(
            "DELETE FROM message_archive WHERE captured_at < ?",
            (cutoff_utc,),
        )
        self._conn.commit()
        deleted_count = int(cursor.rowcount if cursor.rowcount is not None else 0)
        after_count = self.count_messages()

        vacuum_attempted = False
        vacuum_succeeded = False
        if vacuum and deleted_count > 0:
            vacuum_attempted = True
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._conn.execute("VACUUM")
                vacuum_succeeded = True
            except sqlite3.DatabaseError as exc:
                if self._logger is not None:
                    self._logger.warning("message_archive_vacuum_failed error=%s", exc)

        return MessageArchiveCleanupResult(
            before_count=before_count,
            deleted_count=deleted_count,
            after_count=after_count,
            vacuum_attempted=vacuum_attempted,
            vacuum_succeeded=vacuum_succeeded,
        )

    def _build_search_sql(
        self,
        *,
        count_only: bool,
        query: str | None,
        account_id: int | None,
        topic_id: int | None,
        sender_id: int | None,
        event_type: str | None,
        limit: int,
        offset: int,
    ) -> tuple[str, list[object]]:
        clauses = ["1 = 1"]
        params: list[object] = []
        if query:
            normalized_query = normalize_match_text(query)
            clauses.append("(raw_text LIKE ? OR normalized_text LIKE ?)")
            like_value = f"%{query}%"
            normalized_like_value = f"%{normalized_query}%"
            params.extend([like_value, normalized_like_value])
        if account_id is not None:
            clauses.append("account_id = ?")
            params.append(int(account_id))
        if topic_id is not None:
            clauses.append("topic_id = ?")
            params.append(int(topic_id))
        if sender_id is not None:
            clauses.append("sender_id = ?")
            params.append(int(sender_id))
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type.strip())
        where_sql = " AND ".join(clauses)
        if count_only:
            return (
                f"SELECT COUNT(*) AS c FROM message_archive WHERE {where_sql}",
                params,
            )
        params.extend([max(int(limit), 1), max(int(offset), 0)])
        return (
            f"""
            SELECT
                id,
                account_id,
                chat_id,
                topic_id,
                message_id,
                reply_to_msg_id,
                sender_id,
                sender_name,
                raw_text,
                normalized_text,
                event_type,
                message_ts,
                captured_at,
                edit_version,
                is_reply,
                is_topic_message
            FROM message_archive
            WHERE {where_sql}
            ORDER BY message_ts DESC, message_id DESC, edit_version DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            params,
        )

    def _row_to_record(self, row: sqlite3.Row) -> MessageArchiveRecord:
        return MessageArchiveRecord(
            id=int(row["id"]),
            account_id=int(row["account_id"]),
            chat_id=int(row["chat_id"]),
            topic_id=int(row["topic_id"]) if row["topic_id"] is not None else None,
            message_id=int(row["message_id"]),
            reply_to_msg_id=int(row["reply_to_msg_id"]) if row["reply_to_msg_id"] is not None else None,
            sender_id=int(row["sender_id"]) if row["sender_id"] is not None else None,
            sender_name=row["sender_name"],
            raw_text=row["raw_text"],
            normalized_text=row["normalized_text"],
            event_type=row["event_type"],
            message_ts=row["message_ts"],
            captured_at=row["captured_at"],
            edit_version=int(row["edit_version"]),
            is_reply=bool(row["is_reply"]),
            is_topic_message=bool(row["is_topic_message"]),
        )

    @staticmethod
    def _serialize_datetime(value: datetime) -> str:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")
