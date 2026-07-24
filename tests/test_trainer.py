from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from conversation_agent.storage.models import NewGeneratedReply
from conversation_agent.storage.sqlite_repository import SQLiteFeedbackRepository
from conversation_agent.trainer.cards import (
    BAD_CATEGORIES,
    parse_callback,
    review_card,
    review_keyboard,
)
from conversation_agent.trainer.notification_client import TrainerNotificationClient
from conversation_agent.trainer.service import TrainerService

TRAINER_ID = 12345


def repository_with_reply(tmp_path) -> tuple[SQLiteFeedbackRepository, int]:
    repository = SQLiteFeedbackRepository(tmp_path / "feedback.sqlite3")
    repository.initialize()
    reply_id = repository.create_generated_reply(
        NewGeneratedReply(
            dialog_id=1751105897,
            incoming_message_id=10,
            created_at="2026-07-24T10:00:00+00:00",
            model="gpt-test",
            prompt_version="AAA.3",
            generated_reply_text="Fine <reply> & more",
            context_json=(
                '[{"role":"assistant","text":"DO_NOT_SHOW_SECRET_CONTEXT"},'
                '{"role":"user","text":"Hi"}]'
            ),
            incoming_message_text="Hello <script>alert(1)</script>",
        )
    )
    repository.mark_delivery(
        reply_id,
        status="sent",
        sent_message_id=11,
        sent_at="2026-07-24T10:00:01+00:00",
    )
    return repository, reply_id


def service(repository, now=None) -> TrainerService:
    return TrainerService(
        repository,
        trainer_user_id=TRAINER_ID,
        review_chat_id=TRAINER_ID,
        now=now,
    )


def test_trainer_authorization_requires_configured_private_chat(tmp_path) -> None:
    repository, _ = repository_with_reply(tmp_path)
    trainer = service(repository)

    assert trainer.authorized(user_id=TRAINER_ID, chat_id=TRAINER_ID, chat_type="private")
    assert not trainer.authorized(user_id=999, chat_id=TRAINER_ID, chat_type="private")
    assert not trainer.authorized(user_id=TRAINER_ID, chat_id=-100, chat_type="group")


def test_review_card_escapes_text_and_callbacks_are_compact(tmp_path) -> None:
    repository, reply_id = repository_with_reply(tmp_path)
    record = repository.get_reply(reply_id)
    assert record is not None

    rendered = review_card(record)
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "DO_NOT_SHOW_SECRET_CONTEXT" not in rendered
    for row in review_keyboard(reply_id):
        for _, payload in row:
            assert len(payload.encode()) <= 64
            assert parse_callback(payload) is not None
            assert "Hello" not in payload


def test_review_card_marks_truncated_private_text(tmp_path) -> None:
    repository, reply_id = repository_with_reply(tmp_path)
    record = repository.get_reply(reply_id)
    assert record is not None

    rendered = review_card(
        replace(
            record,
            incoming_message_text="x" * 2000,
            generated_reply_text="y" * 2500,
        )
    )

    assert rendered.count("[truncated]") == 2
    assert len(rendered) < 4096


def test_good_and_should_not_reply_are_persisted(tmp_path) -> None:
    repository, reply_id = repository_with_reply(tmp_path)
    trainer = service(repository)

    trainer.handle_callback(f"good:{reply_id}")
    approved = repository.get_reply(reply_id)
    assert approved is not None
    assert approved.feedback_status == "approved"
    assert approved.feedback_source == "trainer_bot"
    assert approved.feedback_trainer_user_id == TRAINER_ID

    trainer.handle_callback(f"no_reply:{reply_id}")
    rejected = repository.get_reply(reply_id)
    assert rejected is not None
    assert rejected.feedback_status == "rejected"
    assert rejected.feedback_category == "should_not_reply"


def test_repeated_and_invalid_callbacks_are_idempotent(tmp_path) -> None:
    repository, reply_id = repository_with_reply(tmp_path)
    trainer = service(repository)

    trainer.handle_callback(f"good:{reply_id}")
    first = repository.get_reply(reply_id)
    trainer.handle_callback(f"good:{reply_id}")
    second = repository.get_reply(reply_id)

    assert first is not None and second is not None
    assert first.feedback_status == second.feedback_status == "approved"
    assert trainer.handle_callback("not-valid").callback_notice == "Action unavailable"
    assert trainer.handle_callback("good:9999").callback_notice == "Action unavailable"


def test_details_contains_metadata_but_not_context(tmp_path) -> None:
    repository, reply_id = repository_with_reply(tmp_path)
    result = service(repository).handle_callback(f"details:{reply_id}")

    assert result.message is not None
    assert f"Reply ID: {reply_id}" in result.message
    assert "Prompt: AAA.3" in result.message
    assert "DO_NOT_SHOW_SECRET_CONTEXT" not in result.message


def test_all_bad_categories_are_supported(tmp_path) -> None:
    repository, reply_id = repository_with_reply(tmp_path)
    trainer = service(repository)

    for category in BAD_CATEGORIES:
        result = trainer.handle_callback(f"cat:{reply_id}:{category}")
        if category == "other":
            assert result.message is not None
            assert repository.get_pending_interaction(TRAINER_ID) is not None
        else:
            record = repository.get_reply(reply_id)
            assert record is not None
            assert record.feedback_category == category


def test_fix_pending_state_survives_service_restart(tmp_path) -> None:
    repository, reply_id = repository_with_reply(tmp_path)
    service(repository).handle_callback(f"fix:{reply_id}")

    restarted = service(SQLiteFeedbackRepository(repository.database_path))
    result = restarted.handle_text("A better answer")
    record = repository.get_reply(reply_id)

    assert result.message == "Feedback saved."
    assert record is not None
    assert record.feedback_status == "corrected"
    assert record.corrected_reply_text == "A better answer"
    assert repository.get_pending_interaction(TRAINER_ID) is None


def test_pending_state_rejects_commands_and_expires(tmp_path) -> None:
    repository, reply_id = repository_with_reply(tmp_path)
    current = datetime(2026, 7, 24, 10, tzinfo=UTC)
    trainer = service(repository, now=lambda: current)
    trainer.handle_callback(f"fix:{reply_id}")

    assert "plain non-empty" in str(trainer.handle_text("/status").message)
    expired = service(repository, now=lambda: current + timedelta(minutes=16))
    assert "expired" in str(expired.handle_text("late correction").message)


def test_other_reason_and_cancel(tmp_path) -> None:
    repository, reply_id = repository_with_reply(tmp_path)
    trainer = service(repository)
    trainer.handle_callback(f"cat:{reply_id}:other")
    trainer.handle_text("The phrasing feels unlike Matvey")

    record = repository.get_reply(reply_id)
    assert record is not None
    assert record.feedback_category == "other"
    assert record.feedback_comment == "The phrasing feels unlike Matvey"

    trainer.handle_callback(f"fix:{reply_id}")
    assert trainer.cancel().message == "Pending action cancelled."


def test_unrelated_text_without_pending_does_not_change_feedback(tmp_path) -> None:
    repository, reply_id = repository_with_reply(tmp_path)

    result = service(repository).handle_text("unrelated")
    record = repository.get_reply(reply_id)

    assert "No pending action" in str(result.message)
    assert record is not None
    assert record.feedback_status == "unreviewed"


class FakeBot:
    def __init__(self, *, failures: int = 0) -> None:
        self.failures = failures
        self.calls = 0

    async def send_message(self, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise TimeoutError("temporary")
        return SimpleNamespace(message_id=700)


def notifier(repository, bot) -> TrainerNotificationClient:
    return TrainerNotificationClient(
        bot=bot,
        repository=repository,
        review_chat_id=TRAINER_ID,
        markup_factory=lambda rows: rows,
    )


def test_notification_is_sent_once_and_ids_are_persisted(tmp_path) -> None:
    repository, reply_id = repository_with_reply(tmp_path)
    bot = FakeBot()
    client = notifier(repository, bot)

    assert asyncio.run(client.notify_reply(reply_id))
    assert not asyncio.run(client.notify_reply(reply_id))
    record = repository.get_reply(reply_id)

    assert bot.calls == 1
    assert record is not None
    assert record.notification_status == "sent"
    assert record.trainer_review_chat_id == TRAINER_ID
    assert record.trainer_review_message_id == 700


def test_notification_retries_and_records_failure(tmp_path) -> None:
    repository, reply_id = repository_with_reply(tmp_path)
    bot = FakeBot(failures=3)

    assert not asyncio.run(notifier(repository, bot).notify_reply(reply_id))
    record = repository.get_reply(reply_id)

    assert bot.calls == 3
    assert record is not None
    assert record.notification_status == "failed"
    assert record.notification_attempts == 3
    assert record.notification_error_category == "timeout"


def test_failed_notification_state_survives_restart_and_can_retry(tmp_path) -> None:
    repository, reply_id = repository_with_reply(tmp_path)
    first_client = TrainerNotificationClient(
        bot=FakeBot(failures=1),
        repository=repository,
        review_chat_id=TRAINER_ID,
        markup_factory=lambda rows: rows,
        max_attempts=1,
    )
    assert not asyncio.run(first_client.notify_reply(reply_id))

    restarted = SQLiteFeedbackRepository(repository.database_path)
    assert asyncio.run(notifier(restarted, FakeBot()).retry_pending()) == 1
    record = restarted.get_reply(reply_id)
    assert record is not None
    assert record.notification_status == "sent"


def test_status_and_recent_queries(tmp_path) -> None:
    repository, reply_id = repository_with_reply(tmp_path)
    trainer = service(repository)

    assert "Pending: 1" in trainer.status()
    assert repository.recent_replies(unreviewed_only=True)[0].id == reply_id
    trainer.handle_callback(f"good:{reply_id}")
    assert repository.recent_replies(unreviewed_only=True) == []
