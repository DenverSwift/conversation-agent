"""Telegram event handling."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from conversation_agent.agent.context_builder import build_dialog_context, telegram_text
from conversation_agent.settings import Settings

logger = logging.getLogger(__name__)


def should_process_event(event: Any, allowed_user_id: int) -> bool:
    if bool(getattr(event, "out", False)):
        return False
    if not bool(getattr(event, "is_private", False)):
        return False
    if getattr(event, "sender_id", None) != allowed_user_id:
        return False
    return bool(telegram_text(event))


async def handle_incoming_event(
    event: Any,
    *,
    settings: Settings,
    responder: Any,
    own_user_id: int,
    dialog_locks: dict[int, asyncio.Lock],
) -> None:
    if not should_process_event(event, settings.allowed_telegram_user_id):
        return

    dialog_id = int(getattr(event, "chat_id", 0) or settings.allowed_telegram_user_id)
    lock = dialog_locks.setdefault(dialog_id, asyncio.Lock())

    async with lock:
        client = event.client
        peer = await _event_peer(event)
        message_id = getattr(event, "id", "unknown")

        try:
            context = await build_dialog_context(
                client,
                peer,
                allowed_user_id=settings.allowed_telegram_user_id,
                own_user_id=own_user_id,
                limit=settings.context_message_limit,
                current_message=event,
            )
            reply = (await responder.reply(context)).strip()
        except Exception:
            logger.exception("OpenAI reply generation failed for message_id=%s", message_id)
            return

        if not reply:
            logger.info("OpenAI returned an empty reply for message_id=%s", message_id)
            return

        try:
            if await matvey_replied_after(client, peer, getattr(event, "id", 0), own_user_id):
                logger.info("Manual reply detected after message_id=%s; skipping LLM reply", message_id)
                return
            await event.respond(reply)
        except Exception:
            logger.exception("Telegram send failed for message_id=%s", message_id)


async def matvey_replied_after(
    client: Any,
    peer: Any,
    incoming_message_id: int,
    own_user_id: int,
) -> bool:
    async for message in client.iter_messages(peer, min_id=incoming_message_id, limit=10):
        if getattr(message, "id", 0) <= incoming_message_id:
            continue
        if not telegram_text(message):
            continue
        if getattr(message, "sender_id", None) == own_user_id or bool(getattr(message, "out", False)):
            return True
    return False


async def _event_peer(event: Any) -> Any:
    get_input_chat = getattr(event, "get_input_chat", None)
    if get_input_chat is not None:
        return await get_input_chat()
    return getattr(event, "chat_id", None)
