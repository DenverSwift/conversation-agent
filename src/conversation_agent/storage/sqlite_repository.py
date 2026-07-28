"""SQLite implementation of feedback and trainer-bot persistence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from conversation_agent.storage.conversation_models import (
    AgentDraftRecord,
    ApprovalAction,
    NewAgentDraft,
)
from conversation_agent.storage.models import (
    FeedbackCounts,
    FeedbackUpdate,
    GeneratedReplyRecord,
    NewGeneratedReply,
    PendingInteraction,
)

SCHEMA_VERSION = 3
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

CONVERSATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS identities (
    user_id TEXT PRIMARY KEY,
    profile_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS business_profiles (
    profile_id TEXT PRIMARY KEY,
    profile_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS style_profiles (
    profile_id TEXT PRIMARY KEY,
    profile_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relationship_profiles (
    contact_id TEXT PRIMARY KEY,
    profile_json TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    contact_id TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'telegram',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_contact_channel
ON conversations(contact_id, channel);

CREATE TABLE IF NOT EXISTS conversation_states (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
    contact_id TEXT NOT NULL,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    contact_id TEXT NOT NULL,
    telegram_message_id INTEGER,
    direction TEXT NOT NULL,
    provenance TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    draft_id INTEGER,
    UNIQUE(contact_id, telegram_message_id, direction)
);

CREATE INDEX IF NOT EXISTS idx_messages_contact_created
ON messages(contact_id, created_at);

CREATE TABLE IF NOT EXISTS behavior_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    message_group_id TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY REFERENCES generated_replies(id) ON DELETE CASCADE,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    contact_id TEXT NOT NULL,
    message_group_id TEXT NOT NULL,
    incoming_message_id INTEGER NOT NULL,
    behavior_plan_id INTEGER REFERENCES behavior_plans(id),
    status TEXT NOT NULL DEFAULT 'pending_approval',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    analyzer_json TEXT NOT NULL,
    goal_json TEXT NOT NULL,
    response_json TEXT NOT NULL,
    behavior_plan_json TEXT NOT NULL,
    prompt_inspection_json TEXT NOT NULL,
    prompt_fingerprint TEXT NOT NULL,
    confidence REAL NOT NULL,
    handoff_required INTEGER NOT NULL DEFAULT 0,
    approved_by TEXT,
    approved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_drafts_contact_status
ON drafts(contact_id, status);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    category TEXT,
    comment TEXT,
    corrected_text TEXT,
    source TEXT NOT NULL,
    trainer_user_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS retrieved_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    example_id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    score REAL NOT NULL,
    provenance TEXT NOT NULL,
    UNIQUE(draft_id, example_id)
);

CREATE TABLE IF NOT EXISTS runtime_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    message_group_id TEXT,
    draft_id INTEGER,
    behavior_plan_id INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_runtime_events_conversation
ON runtime_events(conversation_id, id);

CREATE TABLE IF NOT EXISTS handoffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    contact_id TEXT NOT NULL,
    draft_id INTEGER,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_handoffs_contact_status
ON handoffs(contact_id, status);

CREATE TABLE IF NOT EXISTS trainer_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    payload_text TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    claimed_at TEXT,
    completed_at TEXT,
    error_category TEXT
);

CREATE INDEX IF NOT EXISTS idx_trainer_actions_status
ON trainer_actions(status, id);
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
    "provider": "TEXT NOT NULL DEFAULT 'openai'",
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
            connection.executescript(CONVERSATION_SCHEMA)
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
                """
                SELECT g.*, d.status AS draft_status, d.analyzer_json,
                       d.goal_json, d.behavior_plan_json, d.prompt_inspection_json,
                       d.confidence, d.handoff_required
                FROM generated_replies g
                LEFT JOIN drafts d ON d.id = g.id
                WHERE g.id = ?
                """,
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
                  AND delivery_status IN ('sent', 'pending_approval')
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
                SELECT g.*, d.status AS draft_status, d.analyzer_json,
                       d.goal_json, d.behavior_plan_json, d.prompt_inspection_json,
                       d.confidence, d.handoff_required
                FROM generated_replies g
                LEFT JOIN drafts d ON d.id = g.id
                WHERE g.delivery_status IN ('sent', 'pending_approval')
                  AND g.trainer_review_message_id IS NULL
                  AND g.notification_status IN ('pending', 'failed')
                  AND g.notification_attempts < ?
                ORDER BY g.id
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
                SELECT g.*, d.status AS draft_status, d.analyzer_json,
                       d.goal_json, d.behavior_plan_json, d.prompt_inspection_json,
                       d.confidence, d.handoff_required
                FROM generated_replies g
                LEFT JOIN drafts d ON d.id = g.id
                WHERE g.delivery_status NOT IN ('generated', 'failed') {where_feedback}
                ORDER BY g.id DESC
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

    def upsert_profile(
        self,
        table: str,
        profile_id: str,
        profile_json: str,
        *,
        updated_at: str,
    ) -> None:
        id_columns = {
            "identities": "user_id",
            "business_profiles": "profile_id",
            "style_profiles": "profile_id",
        }
        id_column = id_columns.get(table)
        if id_column is None:
            raise ValueError(f"Unsupported profile table: {table}")
        with self._connect() as connection:
            connection.execute(
                f"""
                INSERT INTO {table} ({id_column}, profile_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT({id_column}) DO UPDATE SET
                    profile_json = excluded.profile_json,
                    updated_at = excluded.updated_at
                """,
                (profile_id, profile_json, updated_at),
            )

    def relationship_profile(self, contact_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT profile_json FROM relationship_profiles WHERE contact_id = ?",
                (contact_id,),
            ).fetchone()
        if row is None:
            return None
        value = json.loads(str(row["profile_json"]))
        return value if isinstance(value, dict) else None

    def save_relationship_profile(
        self,
        contact_id: str,
        profile_json: str,
        *,
        confidence: float,
        updated_at: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO relationship_profiles (
                    contact_id, profile_json, confidence, updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(contact_id) DO UPDATE SET
                    profile_json = excluded.profile_json,
                    confidence = excluded.confidence,
                    updated_at = excluded.updated_at
                """,
                (contact_id, profile_json, confidence, updated_at),
            )

    def upsert_conversation(
        self,
        conversation_id: str,
        contact_id: str,
        *,
        updated_at: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO conversations (
                    id, contact_id, channel, status, created_at, updated_at
                )
                VALUES (?, ?, 'telegram', 'active', ?, ?)
                ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (conversation_id, contact_id, updated_at, updated_at),
            )

    def conversation_state(self, conversation_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM conversation_states WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        value = json.loads(str(row["state_json"]))
        return value if isinstance(value, dict) else None

    def save_conversation_state(
        self,
        conversation_id: str,
        contact_id: str,
        state_json: str,
        *,
        updated_at: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO conversation_states (
                    conversation_id, contact_id, state_json, updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (conversation_id, contact_id, state_json, updated_at),
            )

    def save_message(
        self,
        *,
        conversation_id: str,
        contact_id: str,
        telegram_message_id: int | None,
        direction: str,
        provenance: str,
        text: str,
        created_at: str,
        draft_id: int | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO messages (
                    conversation_id, contact_id, telegram_message_id, direction,
                    provenance, text, created_at, draft_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    contact_id,
                    telegram_message_id,
                    direction,
                    provenance,
                    text,
                    created_at,
                    draft_id,
                ),
            )

    def create_agent_draft(self, draft: NewAgentDraft) -> int:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT INTO generated_replies (
                    dialog_id, incoming_message_id, created_at, model, prompt_version,
                    generated_reply_text, context_json, incoming_message_text,
                    delivery_status, notification_status, provider
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending_approval', 'pending', ?)
                """,
                (
                    int(draft.contact_id),
                    draft.incoming_message_id,
                    draft.created_at,
                    draft.model,
                    draft.prompt_version,
                    draft.generated_reply_text,
                    draft.context_json,
                    draft.incoming_message_text,
                    draft.provider,
                ),
            )
            reply_id = cursor.lastrowid
            if reply_id is None:
                raise sqlite3.DatabaseError("SQLite did not return a draft ID")
            plan_cursor = connection.execute(
                """
                INSERT INTO behavior_plans (
                    conversation_id, message_group_id, plan_json, status,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, 'planned', ?, ?)
                """,
                (
                    draft.conversation_id,
                    draft.message_group_id,
                    draft.behavior_plan_json,
                    draft.created_at,
                    draft.created_at,
                ),
            )
            behavior_plan_id = plan_cursor.lastrowid
            connection.execute(
                """
                INSERT INTO drafts (
                    id, conversation_id, contact_id, message_group_id,
                    incoming_message_id, behavior_plan_id, status, created_at, updated_at,
                    analyzer_json, goal_json, response_json, behavior_plan_json,
                    prompt_inspection_json, prompt_fingerprint, confidence,
                    handoff_required
                )
                VALUES (?, ?, ?, ?, ?, ?, 'pending_approval', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reply_id,
                    draft.conversation_id,
                    draft.contact_id,
                    draft.message_group_id,
                    draft.incoming_message_id,
                    behavior_plan_id,
                    draft.created_at,
                    draft.created_at,
                    draft.analyzer_json,
                    draft.goal_json,
                    draft.response_json,
                    draft.behavior_plan_json,
                    draft.prompt_inspection_json,
                    draft.prompt_fingerprint,
                    draft.confidence,
                    int(draft.handoff_required),
                ),
            )
            return int(reply_id)

    def get_agent_draft(self, draft_id: int) -> AgentDraftRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM drafts WHERE id = ?",
                (draft_id,),
            ).fetchone()
        return _draft_from_row(row) if row is not None else None

    def mark_pending_drafts_stale(
        self,
        contact_id: str,
        *,
        updated_at: str,
    ) -> list[int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id FROM drafts
                WHERE contact_id = ?
                  AND status IN ('pending_approval', 'approved', 'sending')
                """,
                (contact_id,),
            ).fetchall()
            draft_ids = [int(row["id"]) for row in rows]
            if draft_ids:
                placeholders = ",".join("?" for _ in draft_ids)
                connection.execute(
                    f"UPDATE drafts SET status = 'stale', updated_at = ? "
                    f"WHERE id IN ({placeholders})",
                    (updated_at, *draft_ids),
                )
                connection.execute(
                    f"UPDATE generated_replies SET delivery_status = 'stale' "
                    f"WHERE id IN ({placeholders})",
                    tuple(draft_ids),
                )
            return draft_ids

    def update_draft_status(
        self,
        draft_id: int,
        status: str,
        *,
        updated_at: str,
        approved_by: str | None = None,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE drafts
                SET status = ?, updated_at = ?,
                    approved_by = COALESCE(?, approved_by),
                    approved_at = CASE WHEN ? IS NOT NULL THEN ? ELSE approved_at END
                WHERE id = ?
                """,
                (
                    status,
                    updated_at,
                    approved_by,
                    approved_by,
                    updated_at,
                    draft_id,
                ),
            )
            return cursor.rowcount == 1

    def add_retrieved_examples(
        self,
        draft_id: int,
        examples: list[tuple[str, int, float, str]],
    ) -> None:
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO retrieved_examples (
                    draft_id, example_id, rank, score, provenance
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (draft_id, example_id, rank, score, provenance)
                    for example_id, rank, score, provenance in examples
                ],
            )

    def record_runtime_event(
        self,
        *,
        event_type: str,
        occurred_at: str,
        conversation_id: str,
        message_group_id: str = "",
        draft_id: int | None = None,
        behavior_plan_id: int | None = None,
        metadata_json: str = "{}",
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runtime_events (
                    event_type, occurred_at, conversation_id, message_group_id,
                    draft_id, behavior_plan_id, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_type,
                    occurred_at,
                    conversation_id,
                    message_group_id,
                    draft_id,
                    behavior_plan_id,
                    metadata_json,
                ),
            )

    def save_draft_feedback(
        self,
        draft_id: int,
        *,
        status: str,
        created_at: str,
        source: str,
        category: str | None = None,
        comment: str | None = None,
        corrected_text: str | None = None,
        trainer_user_id: int | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO feedback (
                    draft_id, status, category, comment, corrected_text,
                    source, trainer_user_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    status,
                    category,
                    comment,
                    corrected_text,
                    source,
                    trainer_user_id,
                    created_at,
                ),
            )

    def enqueue_trainer_action(
        self,
        draft_id: int,
        *,
        action: str,
        created_at: str,
        payload_text: str | None = None,
    ) -> bool:
        payload_hash = hashlib.sha256((payload_text or "").encode("utf-8")).hexdigest()
        key = f"{draft_id}:{action}:{payload_hash}"
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO trainer_actions (
                    draft_id, action, payload_text, idempotency_key, status, created_at
                )
                SELECT ?, ?, ?, ?, 'pending', ?
                WHERE EXISTS (
                    SELECT 1 FROM drafts
                    WHERE id = ? AND status IN ('pending_approval', 'approved')
                )
                """,
                (draft_id, action, payload_text, key, created_at, draft_id),
            )
            return cursor.rowcount == 1

    def claim_next_trainer_action(self, *, claimed_at: str) -> ApprovalAction | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM trainer_actions
                WHERE status = 'pending'
                ORDER BY id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            cursor = connection.execute(
                """
                UPDATE trainer_actions
                SET status = 'processing', claimed_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (claimed_at, int(row["id"])),
            )
            if cursor.rowcount != 1:
                return None
            return ApprovalAction(
                id=int(row["id"]),
                draft_id=int(row["draft_id"]),
                action=str(row["action"]),
                payload_text=_optional_str(row["payload_text"]),
                created_at=str(row["created_at"]),
            )

    def finish_trainer_action(
        self,
        action_id: int,
        *,
        status: str,
        completed_at: str,
        error_category: str | None = None,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE trainer_actions
                SET status = ?, completed_at = ?, error_category = ?
                WHERE id = ?
                """,
                (status, completed_at, error_category, action_id),
            )
            return cursor.rowcount == 1

    def start_handoff(
        self,
        *,
        conversation_id: str,
        contact_id: str,
        reason: str,
        created_at: str,
        draft_id: int | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO handoffs (
                    conversation_id, contact_id, draft_id, reason, status, created_at
                )
                VALUES (?, ?, ?, ?, 'active', ?)
                """,
                (conversation_id, contact_id, draft_id, reason, created_at),
            )
            connection.execute(
                "UPDATE conversations SET status = 'handed_off', updated_at = ? WHERE id = ?",
                (created_at, conversation_id),
            )

    def handoff_active(self, contact_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM handoffs
                WHERE contact_id = ? AND status = 'active'
                LIMIT 1
                """,
                (contact_id,),
            ).fetchone()
        return row is not None

    def conversation_snapshot(self, conversation_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            conversation = connection.execute(
                "SELECT * FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if conversation is None:
                return None
            state = connection.execute(
                "SELECT state_json FROM conversation_states WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            drafts = connection.execute(
                """
                SELECT id, status, created_at, confidence, handoff_required
                FROM drafts WHERE conversation_id = ? ORDER BY id DESC LIMIT 10
                """,
                (conversation_id,),
            ).fetchall()
        return {
            "conversation": dict(conversation),
            "state": json.loads(str(state["state_json"])) if state is not None else None,
            "drafts": [dict(row) for row in drafts],
        }

    def draft_snapshot(self, draft_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT d.*, g.delivery_status, g.feedback_status,
                       g.corrected_reply_text, g.generated_reply_text
                FROM drafts d JOIN generated_replies g ON g.id = d.id
                WHERE d.id = ?
                """,
                (draft_id,),
            ).fetchone()
        return dict(row) if row is not None else None

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
        draft_status=_optional_str(_row_value(row, "draft_status")),
        analyzer_json=str(_row_value(row, "analyzer_json") or ""),
        goal_json=str(_row_value(row, "goal_json") or ""),
        behavior_plan_json=str(_row_value(row, "behavior_plan_json") or ""),
        prompt_inspection_json=str(_row_value(row, "prompt_inspection_json") or ""),
        confidence=float(str(_row_value(row, "confidence") or 0)),
        handoff_required=bool(_row_value(row, "handoff_required") or 0),
        provider=str(_row_value(row, "provider") or "openai"),
    )


def _draft_from_row(row: sqlite3.Row) -> AgentDraftRecord:
    return AgentDraftRecord(
        id=int(row["id"]),
        conversation_id=str(row["conversation_id"]),
        contact_id=str(row["contact_id"]),
        message_group_id=str(row["message_group_id"]),
        incoming_message_id=int(row["incoming_message_id"]),
        behavior_plan_id=_optional_int(row["behavior_plan_id"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        analyzer_json=str(row["analyzer_json"]),
        goal_json=str(row["goal_json"]),
        response_json=str(row["response_json"]),
        behavior_plan_json=str(row["behavior_plan_json"]),
        prompt_inspection_json=str(row["prompt_inspection_json"]),
        prompt_fingerprint=str(row["prompt_fingerprint"]),
        confidence=float(row["confidence"]),
        handoff_required=bool(row["handoff_required"]),
        approved_by=_optional_str(row["approved_by"]),
        approved_at=_optional_str(row["approved_at"]),
    )


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


def _optional_int(value: object) -> int | None:
    return int(str(value)) if value is not None else None


def _row_value(row: sqlite3.Row, name: str) -> object | None:
    try:
        return row[name]
    except IndexError:
        return None
