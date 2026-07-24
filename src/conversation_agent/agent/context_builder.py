"""Build OpenAI conversation context from Telegram messages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


def telegram_text(message: Any) -> str:
    text = getattr(message, "raw_text", None) or getattr(message, "text", None)
    if text is None:
        text = getattr(message, "message", None)
    return text.strip() if isinstance(text, str) else ""


def role_for_message(message: Any, allowed_user_id: int, own_user_id: int) -> str | None:
    sender_id = getattr(message, "sender_id", None)
    if sender_id == allowed_user_id:
        return "user"
    if sender_id == own_user_id or bool(getattr(message, "out", False)):
        return "assistant"
    return None


async def build_dialog_context(
    client: Any,
    peer: Any,
    *,
    allowed_user_id: int,
    own_user_id: int,
    limit: int,
    current_message: Any,
) -> list[ChatMessage]:
    current_text = telegram_text(current_message)
    current_id = getattr(current_message, "id", None)
    previous: list[Any] = []

    async for message in client.iter_messages(peer, limit=max(limit * 3, limit + 5)):
        if current_id is not None and getattr(message, "id", None) == current_id:
            continue
        if not telegram_text(message):
            continue
        if role_for_message(message, allowed_user_id, own_user_id) is None:
            continue
        previous.append(message)
        if len(previous) >= max(limit - 1, 0):
            break

    ordered = sorted(previous, key=_message_order_key)
    context = [
        ChatMessage(role=role, content=telegram_text(message))
        for message in ordered
        if (role := role_for_message(message, allowed_user_id, own_user_id)) is not None
    ]
    if current_text and limit > 0:
        context.append(ChatMessage(role="user", content=current_text))
    return context[-limit:]


def messages_for_openai(messages: list[ChatMessage]) -> list[dict[str, str]]:
    return [{"role": message.role, "content": message.content} for message in messages]


def _message_order_key(message: Any) -> tuple[bool, Any, int]:
    date = getattr(message, "date", None)
    message_id = getattr(message, "id", 0) or 0
    return (date is None, date or message_id, message_id)
