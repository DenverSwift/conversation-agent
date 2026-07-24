from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from conversation_agent.storage.models import FeedbackUpdate, NewGeneratedReply
from conversation_agent.storage.sqlite_repository import SQLiteFeedbackRepository
from conversation_agent.telegram.feedback import (
    FeedbackCommandError,
    handle_feedback_event,
    parse_feedback_command,
)

OWN_USER_ID = 42


@dataclass
class FakeSavedEvent:
    raw_text: str
    chat_id: int = OWN_USER_ID
    sender_id: int = OWN_USER_ID
    is_private: bool = True

    def __post_init__(self) -> None:
        self.responses: list[str] = []

    async def respond(self, text: str) -> None:
        self.responses.append(text)


def repository_with_reply(tmp_path) -> tuple[SQLiteFeedbackRepository, int]:
    repository = SQLiteFeedbackRepository(tmp_path / "feedback.sqlite3")
    repository.initialize()
    reply_id = repository.create_generated_reply(
        NewGeneratedReply(
            dialog_id=1751105897,
            incoming_message_id=10,
            created_at="2026-07-24T10:00:00+00:00",
            model="model",
            prompt_version="v0.2",
            generated_reply_text="generated",
            context_json="[]",
        )
    )
    return repository, reply_id


def run_feedback(event: FakeSavedEvent, repository: SQLiteFeedbackRepository | None) -> bool:
    return asyncio.run(
        handle_feedback_event(
            event,
            own_user_id=OWN_USER_ID,
            repository=repository,
        )
    )


def test_good_command_parsing_and_persistence(tmp_path) -> None:
    repository, reply_id = repository_with_reply(tmp_path)
    event = FakeSavedEvent(f"/good {reply_id}")

    assert run_feedback(event, repository)

    record = repository.get_reply(reply_id)
    assert record is not None
    assert record.feedback_status == "approved"


def test_bad_category_parsing_and_persistence(tmp_path) -> None:
    repository, reply_id = repository_with_reply(tmp_path)
    event = FakeSavedEvent(f"/bad {reply_id} too_formal")

    assert run_feedback(event, repository)

    record = repository.get_reply(reply_id)
    assert record is not None
    assert record.feedback_status == "rejected"
    assert record.feedback_category == "too_formal"


def test_bad_arbitrary_comment_is_preserved(tmp_path) -> None:
    repository, reply_id = repository_with_reply(tmp_path)
    event = FakeSavedEvent(f"/bad {reply_id} does not sound natural")

    assert run_feedback(event, repository)

    record = repository.get_reply(reply_id)
    assert record is not None
    assert record.feedback_category == "other"
    assert record.feedback_comment == "does not sound natural"


def test_fix_command_persists_correction(tmp_path) -> None:
    repository, reply_id = repository_with_reply(tmp_path)
    event = FakeSavedEvent(f"/fix {reply_id} corrected human answer")

    assert run_feedback(event, repository)

    record = repository.get_reply(reply_id)
    assert record is not None
    assert record.feedback_status == "corrected"
    assert record.corrected_reply_text == "corrected human answer"


def test_malformed_feedback_command_is_safe() -> None:
    with pytest.raises(FeedbackCommandError):
        parse_feedback_command("/bad nope")


def test_unknown_reply_id_returns_error(tmp_path) -> None:
    repository, _ = repository_with_reply(tmp_path)
    event = FakeSavedEvent("/good 999")

    assert run_feedback(event, repository)
    assert event.responses == ["Reply #999 was not found."]


def test_commands_are_accepted_only_in_saved_messages(tmp_path) -> None:
    repository, reply_id = repository_with_reply(tmp_path)
    event = FakeSavedEvent(f"/good {reply_id}", chat_id=100)

    assert not run_feedback(event, repository)

    record = repository.get_reply(reply_id)
    assert record is not None
    assert record.feedback_status == "unreviewed"


def test_feedback_disabled_mode_does_nothing() -> None:
    event = FakeSavedEvent("/good 1")

    assert not run_feedback(event, None)
    assert event.responses == []


def test_repository_accepts_direct_feedback_update(tmp_path) -> None:
    repository, reply_id = repository_with_reply(tmp_path)

    assert repository.save_feedback(
        reply_id,
        FeedbackUpdate(status="approved", updated_at="2026-07-24T10:00:00+00:00"),
    )
