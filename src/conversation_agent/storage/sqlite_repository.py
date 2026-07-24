"""SQLite implementation of the feedback repository."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from conversation_agent.storage.models import (
    FeedbackUpdate,
    GeneratedReplyRecord,
    NewGeneratedReply,
)

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS generated_replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dialog_id INTEGER NOT NULL,
    incoming_message_id INTEGER NOT NULL,
    sent_message_id INTEGER,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    generated_reply_text TEXT NOT NULL,
    context_json TEXT NOT NULL,
    delivery_status TEXT NOT NULL DEFAULT 'generated',
    feedback_status TEXT NOT NULL DEFAULT 'unreviewed',
    feedback_category TEXT,
    feedback_comment TEXT,
    corrected_reply_text TEXT,
    feedback_updated_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_generated_replies_sent_message
ON generated_replies(sent_message_id)
WHERE sent_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_generated_replies_dialog
ON generated_replies(dialog_id);

CREATE INDEX IF NOT EXISTS idx_generated_replies_feedback
ON generated_replies(feedback_status);
"""


class SQLiteFeedbackRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def create_generated_reply(self, reply: NewGeneratedReply) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO generated_replies (
                    dialog_id,
                    incoming_message_id,
                    created_at,
                    model,
                    prompt_version,
                    generated_reply_text,
                    context_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reply.dialog_id,
                    reply.incoming_message_id,
                    reply.created_at,
                    reply.model,
                    reply.prompt_version,
                    reply.generated_reply_text,
                    reply.context_json,
                ),
            )
            reply_id = cursor.lastrowid
        if reply_id is None:
            raise sqlite3.DatabaseError("SQLite did not return a generated reply ID")
        return int(reply_id)

    def mark_delivery(
        self,
        reply_id: int,
        *,
        status: str,
        sent_message_id: int | None = None,
        sent_at: str | None = None,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE generated_replies
                SET delivery_status = ?, sent_message_id = ?, sent_at = ?
                WHERE id = ?
                """,
                (status, sent_message_id, sent_at, reply_id),
            )
            return cursor.rowcount == 1

    def save_feedback(self, reply_id: int, feedback: FeedbackUpdate) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE generated_replies
                SET feedback_status = ?,
                    feedback_category = ?,
                    feedback_comment = ?,
                    corrected_reply_text = ?,
                    feedback_updated_at = ?
                WHERE id = ?
                """,
                (
                    feedback.status,
                    feedback.category,
                    feedback.comment,
                    feedback.corrected_reply_text,
                    feedback.updated_at,
                    reply_id,
                ),
            )
            return cursor.rowcount == 1

    def get_reply(self, reply_id: int) -> GeneratedReplyRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM generated_replies WHERE id = ?",
                (reply_id,),
            ).fetchone()
        return _record_from_row(row) if row is not None else None

    def sent_message_ids(self, dialog_id: int) -> set[int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT sent_message_id
                FROM generated_replies
                WHERE dialog_id = ?
                  AND delivery_status = 'sent'
                  AND sent_message_id IS NOT NULL
                """,
                (dialog_id,),
            ).fetchall()
        return {int(row["sent_message_id"]) for row in rows}

    def reviewed_replies(self) -> list[GeneratedReplyRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM generated_replies
                WHERE feedback_status != 'unreviewed'
                ORDER BY id
                """
            ).fetchall()
        return [_record_from_row(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection


def _record_from_row(row: sqlite3.Row) -> GeneratedReplyRecord:
    return GeneratedReplyRecord(
        id=int(row["id"]),
        dialog_id=int(row["dialog_id"]),
        incoming_message_id=int(row["incoming_message_id"]),
        sent_message_id=(
            int(row["sent_message_id"]) if row["sent_message_id"] is not None else None
        ),
        created_at=str(row["created_at"]),
        sent_at=str(row["sent_at"]) if row["sent_at"] is not None else None,
        model=str(row["model"]),
        prompt_version=str(row["prompt_version"]),
        generated_reply_text=str(row["generated_reply_text"]),
        context_json=str(row["context_json"]),
        delivery_status=str(row["delivery_status"]),
        feedback_status=str(row["feedback_status"]),
        feedback_category=(
            str(row["feedback_category"]) if row["feedback_category"] is not None else None
        ),
        feedback_comment=(
            str(row["feedback_comment"]) if row["feedback_comment"] is not None else None
        ),
        corrected_reply_text=(
            str(row["corrected_reply_text"]) if row["corrected_reply_text"] is not None else None
        ),
        feedback_updated_at=(
            str(row["feedback_updated_at"]) if row["feedback_updated_at"] is not None else None
        ),
    )
