from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from pathlib import Path

from conversation_agent.main import create_feedback_repository
from conversation_agent.settings import Settings
from conversation_agent.storage.sqlite_repository import SQLiteFeedbackRepository
from conversation_agent.telegram.handlers import handle_incoming_event

ALLOWED_USER_ID = 1751105897
OWN_USER_ID = 42


@dataclass
class FakeMessage:
    id: int
    sender_id: int
    raw_text: str
    out: bool = False


class FakeClient:
    def __init__(self, messages: list[FakeMessage]) -> None:
        self.messages = messages

    async def iter_messages(self, peer: object, **kwargs: object):
        raw_limit = kwargs.get("limit")
        limit = int(raw_limit) if isinstance(raw_limit, (int, str)) else len(self.messages)
        raw_min_id = kwargs.get("min_id")
        min_id = int(raw_min_id) if isinstance(raw_min_id, (int, str)) else 0
        yielded = 0
        for message in sorted(self.messages, key=lambda item: item.id, reverse=True):
            if message.id <= min_id:
                continue
            if yielded >= limit:
                break
            yielded += 1
            yield message


class FakeEvent:
    def __init__(
        self,
        *,
        client: FakeClient,
        sender_id: int = ALLOWED_USER_ID,
        is_private: bool = True,
        out: bool = False,
        raw_text: str = "hello",
        message_id: int = 10,
    ) -> None:
        self.client = client
        self.sender_id = sender_id
        self.is_private = is_private
        self.out = out
        self.raw_text = raw_text
        self.id = message_id
        self.chat_id = sender_id
        self.sent: list[str] = []
        self.respond_error: Exception | None = None

    async def get_input_chat(self) -> int:
        return self.chat_id

    async def respond(self, text: str) -> FakeMessage:
        if self.respond_error is not None:
            raise self.respond_error
        self.sent.append(text)
        return FakeMessage(id=99, sender_id=OWN_USER_ID, raw_text=text, out=True)


class FakeResponder:
    def __init__(self, reply: str = "reply", error: Exception | None = None) -> None:
        self.reply_text = reply
        self.error = error
        self.calls = 0

    async def reply(self, messages: object) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.reply_text


def settings(tmp_path: Path) -> Settings:
    return Settings(
        telegram_api_id=1,
        telegram_api_hash="hash",
        telegram_session_path=".secrets/matvey",
        openai_api_key="key",
        openai_model="model",
        allowed_telegram_user_id=ALLOWED_USER_ID,
        context_message_limit=30,
        readme_path=tmp_path / "README.md",
        openai_timeout_seconds=30,
    )


def run_handler(event: FakeEvent, responder: FakeResponder, tmp_path: Path) -> None:
    asyncio.run(
        handle_incoming_event(
            event,
            settings=settings(tmp_path),
            responder=responder,
            own_user_id=OWN_USER_ID,
            dialog_locks={},
        )
    )


def test_allowed_private_user_triggers_generation(tmp_path: Path) -> None:
    event = FakeEvent(client=FakeClient([]))
    responder = FakeResponder("ok")

    run_handler(event, responder, tmp_path)

    assert responder.calls == 1
    assert event.sent == ["ok"]


def test_other_user_is_ignored(tmp_path: Path) -> None:
    event = FakeEvent(client=FakeClient([]), sender_id=123)
    responder = FakeResponder()

    run_handler(event, responder, tmp_path)

    assert responder.calls == 0
    assert event.sent == []


def test_group_is_ignored(tmp_path: Path) -> None:
    event = FakeEvent(client=FakeClient([]), is_private=False)
    responder = FakeResponder()

    run_handler(event, responder, tmp_path)

    assert responder.calls == 0
    assert event.sent == []


def test_outgoing_message_is_ignored(tmp_path: Path) -> None:
    event = FakeEvent(client=FakeClient([]), out=True)
    responder = FakeResponder()

    run_handler(event, responder, tmp_path)

    assert responder.calls == 0
    assert event.sent == []


def test_message_without_text_is_ignored(tmp_path: Path) -> None:
    event = FakeEvent(client=FakeClient([]), raw_text="")
    responder = FakeResponder()

    run_handler(event, responder, tmp_path)

    assert responder.calls == 0
    assert event.sent == []


def test_openai_error_does_not_send_message(tmp_path: Path) -> None:
    event = FakeEvent(client=FakeClient([]))
    responder = FakeResponder(error=RuntimeError("api failed"))

    run_handler(event, responder, tmp_path)

    assert responder.calls == 1
    assert event.sent == []


def test_empty_reply_is_not_sent(tmp_path: Path) -> None:
    event = FakeEvent(client=FakeClient([]))
    responder = FakeResponder("   ")

    run_handler(event, responder, tmp_path)

    assert responder.calls == 1
    assert event.sent == []


def test_manual_reply_cancels_prepared_llm_reply(tmp_path: Path) -> None:
    client = FakeClient([FakeMessage(id=11, sender_id=OWN_USER_ID, raw_text="manual", out=True)])
    event = FakeEvent(client=client, message_id=10)
    responder = FakeResponder("llm reply")

    run_handler(event, responder, tmp_path)

    assert responder.calls == 1
    assert event.sent == []


def test_private_message_text_is_not_written_to_logs(
    tmp_path: Path,
    caplog,
) -> None:
    private_text = "private conversation text"
    event = FakeEvent(client=FakeClient([]))
    responder = FakeResponder(error=RuntimeError(private_text))

    with caplog.at_level(logging.ERROR):
        run_handler(event, responder, tmp_path)

    assert private_text not in caplog.text


def test_feedback_command_does_not_trigger_openai(tmp_path: Path) -> None:
    event = FakeEvent(
        client=FakeClient([]),
        sender_id=OWN_USER_ID,
        out=True,
        raw_text="/good 1",
    )
    responder = FakeResponder()

    run_handler(event, responder, tmp_path)

    assert responder.calls == 0
    assert event.sent == []


def test_failed_telegram_delivery_updates_feedback_record(tmp_path: Path) -> None:
    repository = SQLiteFeedbackRepository(tmp_path / "feedback.sqlite3")
    repository.initialize()
    event = FakeEvent(client=FakeClient([]))
    event.respond_error = RuntimeError("telegram unavailable")
    responder = FakeResponder("generated")
    current_settings = replace(settings(tmp_path), feedback_saved_messages_enabled=False)

    asyncio.run(
        handle_incoming_event(
            event,
            settings=current_settings,
            responder=responder,
            own_user_id=OWN_USER_ID,
            dialog_locks={},
            feedback_repository=repository,
        )
    )

    record = repository.get_reply(1)
    assert record is not None
    assert record.delivery_status == "failed"


def test_feedback_storage_failure_blocks_delivery(tmp_path: Path) -> None:
    class FailingRepository:
        def create_generated_reply(self, reply) -> int:
            raise OSError("disk unavailable")

    event = FakeEvent(client=FakeClient([]))
    responder = FakeResponder("generated")

    asyncio.run(
        handle_incoming_event(
            event,
            settings=settings(tmp_path),
            responder=responder,
            own_user_id=OWN_USER_ID,
            dialog_locks={},
            feedback_repository=FailingRepository(),  # type: ignore[arg-type]
        )
    )

    assert event.sent == []


def test_feedback_disabled_does_not_create_database(tmp_path: Path) -> None:
    database_path = tmp_path / "feedback.sqlite3"
    current_settings = replace(
        settings(tmp_path),
        feedback_enabled=False,
        feedback_database_path=database_path,
    )

    assert create_feedback_repository(current_settings) is None
    assert not database_path.exists()


def test_sent_reply_triggers_trainer_notification(tmp_path: Path) -> None:
    class FakeNotifier:
        def __init__(self) -> None:
            self.reply_ids: list[int] = []

        async def notify_reply(self, reply_id: int) -> bool:
            self.reply_ids.append(reply_id)
            return True

    repository = SQLiteFeedbackRepository(tmp_path / "feedback.sqlite3")
    repository.initialize()
    notifier = FakeNotifier()
    event = FakeEvent(client=FakeClient([]), raw_text="incoming")

    asyncio.run(
        handle_incoming_event(
            event,
            settings=settings(tmp_path),
            responder=FakeResponder("generated"),
            own_user_id=OWN_USER_ID,
            dialog_locks={},
            feedback_repository=repository,
            review_notifier=notifier,
        )
    )

    record = repository.get_reply(1)
    assert notifier.reply_ids == [1]
    assert record is not None
    assert record.incoming_message_text == "incoming"
