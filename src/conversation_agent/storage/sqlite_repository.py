"""SQLite implementation of feedback and trainer-bot persistence."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from conversation_agent.storage.models import (
    FeedbackCounts,
    FeedbackUpdate,
    GeneratedReplyRecord,
    NewGeneratedReply,
    PendingInteraction,
)

SCHEMA_VERSION = 2
MAX_NOTIFICATION_ATTEMPTS = 3

BASE_SCHEMA = """
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

TRAINER_SCHEMA = """
CREATE TABLE IF NOT EXISTS trainer_pending_interactions (
    trainer_user_id INTEGER PRIMARY KEY,
    reply_id INTEGER NOT NULL REFERENCES generated_replies(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_generated_replies_notification
ON generated_replies(notification_status, notification_attempts);
"""

MIGRATION_COLUMNS = {
    "incoming_message_text": "TEXT NOT NULL DEFAULT ''",
    "feedback_source": "TEXT",
    "feedback_trainer_user_id": "INTEGER",
    "trainer_review_chat_id": "INTEGER",
    "trainer_review_message_id": "INTEGER",
    "notification_status": "TEXT NOT NULL DEFAULT 'not_requested'",
    "notification_attempts": "INTEGER NOT NULL DEFAULT 0",
    "notification_last_attempt_at": "TEXT",
    "notification_error_category": "TEXT",
}


class SQLiteFeedbackRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(BASE_SCHEMA)
            existing = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(generated_replies)")
            }
            for name, declaration in MIGRATION_COLUMNS.items():
                if name not in existing:
                    connection.execute(
                        f"ALTER TABLE generated_replies ADD COLUMN {name} {declaration}"
                    )
            connection.executescript(TRAINER_SCHEMA)
            connection.execute(
                """
                UPDATE generated_replies
                SET feedback_source = 'saved_messages'
                WHERE feedback_status != 'unreviewed'
                  AND feedback_source IS NULL
                """
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def create_generated_reply(self, reply: NewGeneratedReply) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO generated_replies (
                    dialog_id, incoming_message_id, created_at, model, prompt_version,
                    generated_reply_text, context_json, incoming_message_text
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reply.dialog_id,
                    reply.incoming_message_id,
                    reply.created_at,
                    reply.model,
                    reply.prompt_version,
                    reply.generated_reply_text,
                    reply.context_json,
                    reply.incoming_message_text,
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
                SET feedback_status = ?, feedback_category = ?,
                    feedback_comment = ?, corrected_reply_text = ?,
                    feedback_updated_at = ?, feedback_source = ?,
                    feedback_trainer_user_id = ?
                WHERE id = ?
                """,
                (
                    feedback.status,
                    feedback.category,
                    feedback.comment,
                    feedback.corrected_reply_text,
                    feedback.updated_at,
                    feedback.source,
                    feedback.trainer_user_id,
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
                SELECT sent_message_id FROM generated_replies
                WHERE dialog_id = ? AND delivery_status = 'sent'
                  AND sent_message_id IS NOT NULL
                """,
                (dialog_id,),
            ).fetchall()
        return {int(row["sent_message_id"]) for row in rows}

    def reviewed_replies(self) -> list[GeneratedReplyRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM generated_replies
                WHERE feedback_status != 'unreviewed'
                ORDER BY id
                """
            ).fetchall()
        return [_record_from_row(row) for row in rows]

    def claim_notification(self, reply_id: int, *, attempted_at: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE generated_replies
                SET notification_status = 'sending',
                    notification_attempts = notification_attempts + 1,
                    notification_last_attempt_at = ?,
                    notification_error_category = NULL
                WHERE id = ?
                  AND trainer_review_message_id IS NULL
                  AND notification_status IN ('not_requested', 'pending', 'failed')
                  AND notification_attempts < ?
                  AND delivery_status = 'sent'
                """,
                (attempted_at, reply_id, MAX_NOTIFICATION_ATTEMPTS),
            )
            return cursor.rowcount == 1

    def finish_notification(
        self,
        reply_id: int,
        *,
        status: str,
        attempted_at: str,
        chat_id: int | None = None,
        message_id: int | None = None,
        error_category: str | None = None,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE generated_replies
                SET notification_status = ?,
                    notification_last_attempt_at = ?,
                    trainer_review_chat_id = COALESCE(?, trainer_review_chat_id),
                    trainer_review_message_id = COALESCE(?, trainer_review_message_id),
                    notification_error_category = ?
                WHERE id = ?
                """,
                (status, attempted_at, chat_id, message_id, error_category, reply_id),
            )
            return cursor.rowcount == 1

    def pending_notifications(self, *, limit: int = 20) -> list[GeneratedReplyRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM generated_replies
                WHERE delivery_status = 'sent'
                  AND trainer_review_message_id IS NULL
                  AND notification_status IN ('pending', 'failed')
                  AND notification_attempts < ?
                ORDER BY id
                LIMIT ?
                """,
                (MAX_NOTIFICATION_ATTEMPTS, limit),
            ).fetchall()
        return [_record_from_row(row) for row in rows]

    def requeue_stale_notifications(self, *, older_than: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE generated_replies
                SET notification_status = 'failed',
                    notification_error_category = 'interrupted'
                WHERE notification_status = 'sending'
                  AND notification_last_attempt_at < ?
                  AND trainer_review_message_id IS NULL
                """,
                (older_than,),
            )
            return cursor.rowcount

    def set_pending_interaction(self, interaction: PendingInteraction) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO trainer_pending_interactions (
                    trainer_user_id, reply_id, kind, created_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(trainer_user_id) DO UPDATE SET
                    reply_id = excluded.reply_id,
                    kind = excluded.kind,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at
                """,
                (
                    interaction.trainer_user_id,
                    interaction.reply_id,
                    interaction.kind,
                    interaction.created_at,
                    interaction.expires_at,
                ),
            )

    def get_pending_interaction(self, trainer_user_id: int) -> PendingInteraction | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM trainer_pending_interactions
                WHERE trainer_user_id = ?
                """,
                (trainer_user_id,),
            ).fetchone()
        if row is None:
            return None
        return PendingInteraction(
            trainer_user_id=int(row["trainer_user_id"]),
            reply_id=int(row["reply_id"]),
            kind=str(row["kind"]),
            created_at=str(row["created_at"]),
            expires_at=str(row["expires_at"]),
        )

    def clear_pending_interaction(self, trainer_user_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM trainer_pending_interactions WHERE trainer_user_id = ?",
                (trainer_user_id,),
            )
            return cursor.rowcount == 1

    def recent_replies(
        self, *, limit: int = 5, unreviewed_only: bool = False
    ) -> list[GeneratedReplyRecord]:
        where_feedback = "AND feedback_status = 'unreviewed'" if unreviewed_only else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM generated_replies
                WHERE delivery_status = 'sent' {where_feedback}
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_record_from_row(row) for row in rows]

    def feedback_counts(self) -> FeedbackCounts:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total,
                    SUM(feedback_status = 'unreviewed') AS unreviewed,
                    SUM(feedback_status = 'approved') AS approved,
                    SUM(feedback_status = 'rejected') AS rejected,
                    SUM(feedback_status = 'corrected') AS corrected,
                    SUM(delivery_status = 'failed') AS delivery_failures
                FROM generated_replies
                """
            ).fetchone()
        assert row is not None
        return FeedbackCounts(
            total=int(row["total"] or 0),
            unreviewed=int(row["unreviewed"] or 0),
            approved=int(row["approved"] or 0),
            rejected=int(row["rejected"] or 0),
            corrected=int(row["corrected"] or 0),
            delivery_failures=int(row["delivery_failures"] or 0),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


def _record_from_row(row: sqlite3.Row) -> GeneratedReplyRecord:
    return GeneratedReplyRecord(
        id=int(row["id"]),
        dialog_id=int(row["dialog_id"]),
        incoming_message_id=int(row["incoming_message_id"]),
        sent_message_id=_optional_int(row["sent_message_id"]),
        created_at=str(row["created_at"]),
        sent_at=_optional_str(row["sent_at"]),
        model=str(row["model"]),
        prompt_version=str(row["prompt_version"]),
        generated_reply_text=str(row["generated_reply_text"]),
        context_json=str(row["context_json"]),
        delivery_status=str(row["delivery_status"]),
        feedback_status=str(row["feedback_status"]),
        feedback_category=_optional_str(row["feedback_category"]),
        feedback_comment=_optional_str(row["feedback_comment"]),
        corrected_reply_text=_optional_str(row["corrected_reply_text"]),
        feedback_updated_at=_optional_str(row["feedback_updated_at"]),
        incoming_message_text=str(row["incoming_message_text"]),
        feedback_source=_optional_str(row["feedback_source"]),
        feedback_trainer_user_id=_optional_int(row["feedback_trainer_user_id"]),
        trainer_review_chat_id=_optional_int(row["trainer_review_chat_id"]),
        trainer_review_message_id=_optional_int(row["trainer_review_message_id"]),
        notification_status=str(row["notification_status"]),
        notification_attempts=int(row["notification_attempts"]),
        notification_last_attempt_at=_optional_str(row["notification_last_attempt_at"]),
        notification_error_category=_optional_str(row["notification_error_category"]),
    )


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


def _optional_int(value: object) -> int | None:
    return int(str(value)) if value is not None else None
