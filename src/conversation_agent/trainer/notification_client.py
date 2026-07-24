"""Bot API client for reliable trainer review-card delivery."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from conversation_agent.storage.repository import FeedbackRepository
from conversation_agent.trainer.cards import review_card, review_keyboard

logger = logging.getLogger(__name__)


class TrainerNotificationClient:
    def __init__(
        self,
        *,
        bot: Any,
        repository: FeedbackRepository,
        review_chat_id: int,
        markup_factory: Callable[[list[list[tuple[str, str]]]], Any],
        max_attempts: int = 3,
    ) -> None:
        self.bot = bot
        self.repository = repository
        self.review_chat_id = review_chat_id
        self.markup_factory = markup_factory
        self.max_attempts = max_attempts

    async def notify_reply(self, reply_id: int) -> bool:
        record = self.repository.get_reply(reply_id)
        if record is None or record.trainer_review_message_id is not None:
            return False
        for attempt in range(self.max_attempts):
            attempted_at = datetime.now(UTC).isoformat()
            if not self.repository.claim_notification(reply_id, attempted_at=attempted_at):
                return False
            try:
                message = await self.bot.send_message(
                    chat_id=self.review_chat_id,
                    text=review_card(record),
                    parse_mode="HTML",
                    reply_markup=self.markup_factory(review_keyboard(reply_id)),
                    disable_web_page_preview=True,
                )
            except Exception as exc:  # noqa: BLE001
                category = _error_category(exc)
                self.repository.finish_notification(
                    reply_id,
                    status="failed",
                    attempted_at=datetime.now(UTC).isoformat(),
                    error_category=category,
                )
                logger.error(
                    "Trainer card delivery failed reply_id=%s error_type=%s category=%s",
                    reply_id,
                    type(exc).__name__,
                    category,
                )
                if attempt + 1 < self.max_attempts:
                    await asyncio.sleep(min(2**attempt, 2))
                continue
            self.repository.finish_notification(
                reply_id,
                status="sent",
                attempted_at=datetime.now(UTC).isoformat(),
                chat_id=self.review_chat_id,
                message_id=int(message.message_id),
            )
            return True
        return False

    async def retry_pending(self, *, limit: int = 20) -> int:
        cutoff = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        self.repository.requeue_stale_notifications(older_than=cutoff)
        delivered = 0
        for record in self.repository.pending_notifications(limit=limit):
            delivered += int(await self.notify_reply(record.id))
        return delivered


def _error_category(exc: Exception) -> str:
    name = type(exc).__name__.lower()
    if "retryafter" in name:
        return "rate_limited"
    if "timeout" in name:
        return "timeout"
    if "network" in name:
        return "network"
    if "forbidden" in name or "unauthorized" in name:
        return "authorization"
    return "telegram_api"
