"""Telegram event handling."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from conversation_agent.agent.context_builder import (
    ChatMessage,
    build_dialog_context,
    telegram_text,
)
from conversation_agent.settings import Settings
from conversation_agent.storage.models import NewGeneratedReply
from conversation_agent.storage.repository import FeedbackRepository

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
    feedback_repository: FeedbackRepository | None = None,
    review_notifier: Any | None = None,
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
            known_ai_ids = (
                feedback_repository.sent_message_ids(dialog_id)
                if feedback_repository is not None
                else set()
            )
            context = await build_dialog_context(
                client,
                peer,
                allowed_user_id=settings.allowed_telegram_user_id,
                own_user_id=own_user_id,
                limit=settings.context_message_limit,
                current_message=event,
                known_ai_message_ids=known_ai_ids,
            )
            reply = (await responder.reply(context)).strip()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "OpenAI reply generation failed for message_id=%s error_type=%s",
                message_id,
                type(exc).__name__,
            )
            return

        if not reply:
            logger.info("OpenAI returned an empty reply for message_id=%s", message_id)
            return

        reply_id = _record_generated_reply(
            feedback_repository,
            settings=settings,
            dialog_id=dialog_id,
            incoming_message_id=int(getattr(event, "id", 0)),
            reply=reply,
            context=context,
            incoming_message_text=telegram_text(event),
        )
        if feedback_repository is not None and reply_id is None:
            return

        try:
            if await matvey_replied_after(client, peer, getattr(event, "id", 0), own_user_id):
                logger.info("Manual reply detected after message_id=%s; skipping LLM reply", message_id)
                _mark_delivery(
                    feedback_repository,
                    reply_id,
                    status="cancelled_manual",
                )
                return
            sent_message = await event.respond(reply)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Telegram send failed for message_id=%s error_type=%s",
                message_id,
                type(exc).__name__,
            )
            _mark_delivery(feedback_repository, reply_id, status="failed")
            return

        sent_message_id = getattr(sent_message, "id", None)
        delivery_recorded = _mark_delivery(
            feedback_repository,
            reply_id,
            status="sent",
            sent_message_id=int(sent_message_id) if sent_message_id is not None else None,
            sent_at=datetime.now(UTC).isoformat(),
        )
        if reply_id is not None and delivery_recorded and review_notifier is not None:
            try:
                assert feedback_repository is not None
                feedback_repository.finish_notification(
                    reply_id,
                    status="pending",
                    attempted_at=datetime.now(UTC).isoformat(),
                )
                await review_notifier.notify_reply(reply_id)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Trainer feedback card failed for reply_id=%s error_type=%s",
                    reply_id,
                    type(exc).__name__,
                )


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


def _record_generated_reply(
    repository: FeedbackRepository | None,
    *,
    settings: Settings,
    dialog_id: int,
    incoming_message_id: int,
    reply: str,
    context: list[ChatMessage],
    incoming_message_text: str,
) -> int | None:
    if repository is None:
        return None
    context_json = json.dumps(
        [
            {
                "role": message.role,
                "text": message.content,
                "provenance": message.provenance,
                "message_id": message.message_id,
            }
            for message in context
        ],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    try:
        return repository.create_generated_reply(
            NewGeneratedReply(
                dialog_id=dialog_id,
                incoming_message_id=incoming_message_id,
                created_at=datetime.now(UTC).isoformat(),
                model=settings.openai_model,
                prompt_version=settings.prompt_version,
                generated_reply_text=reply,
                context_json=context_json,
                incoming_message_text=incoming_message_text,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Feedback storage creation failed for message_id=%s error_type=%s; "
            "reply delivery blocked",
            incoming_message_id,
            type(exc).__name__,
        )
        return None


def _mark_delivery(
    repository: FeedbackRepository | None,
    reply_id: int | None,
    *,
    status: str,
    sent_message_id: int | None = None,
    sent_at: str | None = None,
) -> bool:
    if repository is None or reply_id is None:
        return False
    try:
        return repository.mark_delivery(
            reply_id,
            status=status,
            sent_message_id=sent_message_id,
            sent_at=sent_at,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Feedback delivery update failed for reply_id=%s error_type=%s",
            reply_id,
            type(exc).__name__,
        )
        return False
