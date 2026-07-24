"""Telethon client setup."""

from __future__ import annotations

from typing import Any

from conversation_agent.settings import Settings
from conversation_agent.storage.repository import FeedbackRepository
from conversation_agent.telegram.feedback import handle_feedback_event
from conversation_agent.telegram.handlers import handle_incoming_event


async def create_telegram_client(settings: Settings) -> Any:
    from telethon import TelegramClient

    client: Any = TelegramClient(
        settings.telegram_session_path,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    await client.start()
    return client


def register_message_handler(
    client: Any,
    *,
    settings: Settings,
    responder: Any,
    own_user_id: int,
    dialog_locks: dict[int, Any],
    feedback_repository: FeedbackRepository | None = None,
) -> None:
    from telethon import events

    async def _handler(event: Any) -> None:
        await handle_incoming_event(
            event,
            settings=settings,
            responder=responder,
            own_user_id=own_user_id,
            dialog_locks=dialog_locks,
            feedback_repository=feedback_repository,
        )

    client.add_event_handler(_handler, events.NewMessage(incoming=True))

    if feedback_repository is not None:

        async def _feedback_handler(event: Any) -> None:
            await handle_feedback_event(
                event,
                own_user_id=own_user_id,
                repository=feedback_repository,
            )

        client.add_event_handler(_feedback_handler, events.NewMessage(outgoing=True))
