from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from conversation_agent.settings import Settings
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

    async def get_input_chat(self) -> int:
        return self.chat_id

    async def respond(self, text: str) -> None:
        self.sent.append(text)


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
