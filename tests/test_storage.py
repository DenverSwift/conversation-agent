from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from conversation_agent.storage.models import FeedbackUpdate, NewGeneratedReply
from conversation_agent.storage.sqlite_repository import SQLiteFeedbackRepository


def new_reply() -> NewGeneratedReply:
    return NewGeneratedReply(
        dialog_id=1751105897,
        incoming_message_id=10,
        created_at="2026-07-24T10:00:00+00:00",
        model="test-model",
        prompt_version="AAA.2",
        generated_reply_text="generated",
        context_json='[{"role":"user","text":"hello"}]',
    )


def test_generated_reply_record_is_created(tmp_path) -> None:
    repository = SQLiteFeedbackRepository(tmp_path / "feedback.sqlite3")
    repository.initialize()

    reply_id = repository.create_generated_reply(new_reply())
    record = repository.get_reply(reply_id)

    assert record is not None
    assert record.generated_reply_text == "generated"
    assert record.delivery_status == "generated"
    assert record.prompt_version == "AAA.2"


def test_sent_telegram_message_id_is_stored(tmp_path) -> None:
    repository = SQLiteFeedbackRepository(tmp_path / "feedback.sqlite3")
    repository.initialize()
    reply_id = repository.create_generated_reply(new_reply())

    assert repository.mark_delivery(
        reply_id,
        status="sent",
        sent_message_id=99,
        sent_at="2026-07-24T10:00:01+00:00",
    )

    record = repository.get_reply(reply_id)
    assert record is not None
    assert record.sent_message_id == 99
    assert repository.sent_message_ids(1751105897) == {99}


def test_failed_telegram_delivery_is_stored(tmp_path) -> None:
    repository = SQLiteFeedbackRepository(tmp_path / "feedback.sqlite3")
    repository.initialize()
    reply_id = repository.create_generated_reply(new_reply())

    assert repository.mark_delivery(reply_id, status="failed")

    record = repository.get_reply(reply_id)
    assert record is not None
    assert record.delivery_status == "failed"
    assert repository.sent_message_ids(1751105897) == set()


def test_feedback_update_is_persisted(tmp_path) -> None:
    repository = SQLiteFeedbackRepository(tmp_path / "feedback.sqlite3")
    repository.initialize()
    reply_id = repository.create_generated_reply(new_reply())

    assert repository.save_feedback(
        reply_id,
        FeedbackUpdate(
            status="corrected",
            corrected_reply_text="human correction",
            updated_at="2026-07-24T10:01:00+00:00",
        ),
    )

    record = repository.get_reply(reply_id)
    assert record is not None
    assert record.feedback_status == "corrected"
    assert record.corrected_reply_text == "human correction"


def test_legacy_prompt_version_remains_readable(tmp_path) -> None:
    repository = SQLiteFeedbackRepository(tmp_path / "feedback.sqlite3")
    repository.initialize()
    reply_id = repository.create_generated_reply(
        replace(new_reply(), prompt_version="v0.2")
    )

    record = repository.get_reply(reply_id)

    assert record is not None
    assert record.prompt_version == "v0.2"


def test_aaa2_database_migrates_without_data_loss(tmp_path) -> None:
    database_path = tmp_path / "feedback.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE generated_replies (
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
            INSERT INTO generated_replies (
                dialog_id, incoming_message_id, created_at, model, prompt_version,
                generated_reply_text, context_json, feedback_status
            ) VALUES (
                1, 2, '2026-01-01T00:00:00+00:00', 'legacy', 'AAA.2',
                'old reply', '[]', 'approved'
            );
            PRAGMA user_version = 1;
            """
        )

    repository = SQLiteFeedbackRepository(database_path)
    repository.initialize()
    repository.initialize()
    record = repository.get_reply(1)

    assert record is not None
    assert record.generated_reply_text == "old reply"
    assert record.prompt_version == "AAA.2"
    assert record.feedback_source == "saved_messages"
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_two_repository_instances_can_write_concurrently(tmp_path) -> None:
    database_path = tmp_path / "feedback.sqlite3"
    first = SQLiteFeedbackRepository(database_path)
    second = SQLiteFeedbackRepository(database_path)
    first.initialize()
    second.initialize()

    def create(index: int) -> int:
        repository = first if index % 2 else second
        return repository.create_generated_reply(
            replace(new_reply(), incoming_message_id=index)
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(create, range(1, 21)))

    assert len(set(ids)) == 20
