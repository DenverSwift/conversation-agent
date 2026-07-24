from __future__ import annotations

import asyncio
from dataclasses import dataclass

from conversation_agent.agent.context_builder import build_dialog_context


@dataclass
class FakeMessage:
    id: int
    sender_id: int
    raw_text: str
    out: bool = False
    date: int | None = None


class FakeClient:
    def __init__(self, messages: list[FakeMessage]) -> None:
        self.messages = messages

    async def iter_messages(self, peer: object, **kwargs: object):
        raw_limit = kwargs.get("limit")
        limit = int(raw_limit) if isinstance(raw_limit, (int, str)) else len(self.messages)
        for yielded, message in enumerate(sorted(self.messages, key=lambda item: item.id, reverse=True)):
            if yielded >= limit:
                break
            yield message


def test_context_contains_maximum_30_messages() -> None:
    messages = [
        FakeMessage(id=index, sender_id=1751105897 if index % 2 else 42, raw_text=f"m{index}")
        for index in range(1, 80)
    ]
    current = FakeMessage(id=80, sender_id=1751105897, raw_text="current")
    context = asyncio.run(
        build_dialog_context(
            FakeClient(messages),
            peer=1751105897,
            allowed_user_id=1751105897,
            own_user_id=42,
            limit=30,
            current_message=current,
        )
    )

    assert len(context) == 30
    assert context[-1].content == "current"


def test_context_order_and_roles_are_chronological() -> None:
    messages = [
        FakeMessage(id=3, sender_id=1751105897, raw_text="third"),
        FakeMessage(id=1, sender_id=1751105897, raw_text="first"),
        FakeMessage(id=2, sender_id=42, raw_text="second", out=True),
    ]
    current = FakeMessage(id=4, sender_id=1751105897, raw_text="fourth")
    context = asyncio.run(
        build_dialog_context(
            FakeClient(messages),
            peer=1751105897,
            allowed_user_id=1751105897,
            own_user_id=42,
            limit=30,
            current_message=current,
        )
    )

    assert [(message.role, message.content) for message in context] == [
        ("user", "first"),
        ("assistant", "second"),
        ("user", "third"),
        ("user", "fourth"),
    ]
