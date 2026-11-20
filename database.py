"""ذخیره وضعیت شروع کاربران و درخواست‌های در انتظار؛ بدون ساخت تاریخچه چت."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS started_users (
                    user_id INTEGER PRIMARY KEY,
                    first_started_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS message_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender_id INTEGER NOT NULL,
                    receiver_id INTEGER NOT NULL,
                    source_chat_id INTEGER NOT NULL,
                    source_message_id INTEGER NOT NULL,
                    content_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    content_ready INTEGER NOT NULL DEFAULT 0,
                    admin_card_message_id INTEGER,
                    created_at TEXT NOT NULL,
                    processed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_requests_status
                    ON message_requests(status);
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def mark_first_start(self, user_id: int) -> bool:
        """True فقط برای اولین /start کاربر برمی‌گرداند."""
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO started_users(user_id, first_started_at) VALUES (?, ?)",
                (user_id, self._now()),
            )
            return cursor.rowcount == 1

    def create_request(
        self,
        sender_id: int,
        receiver_id: int,
        source_chat_id: int,
        source_message_id: int,
        content_type: str,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO message_requests
                    (sender_id, receiver_id, source_chat_id, source_message_id,
                     content_type, status, content_ready, created_at)
                VALUES (?, ?, ?, ?, ?, 'PENDING', 0, ?)
                """,
                (
                    sender_id,
                    receiver_id,
                    source_chat_id,
                    source_message_id,
                    content_type,
                    self._now(),
                ),
            )
            return int(cursor.lastrowid)

    def set_admin_card(self, request_id: int, message_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE message_requests SET admin_card_message_id=? WHERE id=?",
                (message_id, request_id),
            )

    def set_content_ready(self, request_id: int, ready: bool) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE message_requests SET content_ready=? WHERE id=?",
                (1 if ready else 0, request_id),
            )

    def get_request(self, request_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM message_requests WHERE id=?", (request_id,)
            ).fetchone()
            return dict(row) if row else None

    def claim_request(self, request_id: int, action: str) -> bool:
        """درخواست را به‌شکل اتمی برای یک ادمین قفل می‌کند."""
        if action not in {"APPROVE", "REJECT"}:
            raise ValueError("عملیات نامعتبر است.")
        target_status = f"PROCESSING_{action}"
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE message_requests
                SET status=?
                WHERE id=? AND status='PENDING'
                """,
                (target_status, request_id),
            )
            return cursor.rowcount == 1

    def finish_request(self, request_id: int, status: str) -> None:
        allowed = {"APPROVED", "REJECTED", "FAILED"}
        if status not in allowed:
            raise ValueError("وضعیت نهایی نامعتبر است.")
        with self._connect() as connection:
            connection.execute(
                "UPDATE message_requests SET status=?, processed_at=? WHERE id=?",
                (status, self._now(), request_id),
            )

    def mark_failed(self, request_id: int) -> None:
        self.finish_request(request_id, "FAILED")
