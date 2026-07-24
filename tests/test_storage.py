from __future__ import annotations

from conversation_agent.storage.models import FeedbackUpdate, NewGeneratedReply
from conversation_agent.storage.sqlite_repository import SQLiteFeedbackRepository


def new_reply() -> NewGeneratedReply:
    return NewGeneratedReply(
        dialog_id=1751105897,
        incoming_message_id=10,
        created_at="2026-07-24T10:00:00+00:00",
        model="test-model",
        prompt_version="v0.2",
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
    assert record.prompt_version == "v0.2"


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
