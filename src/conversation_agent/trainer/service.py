"""Framework-independent trainer interaction rules."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from conversation_agent.storage.models import FeedbackUpdate, PendingInteraction
from conversation_agent.storage.repository import FeedbackRepository
from conversation_agent.trainer.cards import BAD_CATEGORIES, details_text, parse_callback

PENDING_TTL = timedelta(minutes=15)
MAX_CORRECTION_LENGTH = 4000
MAX_COMMENT_LENGTH = 1000


@dataclass(frozen=True)
class ServiceResult:
    message: str | None = None
    edit_reply_id: int | None = None
    keyboard: str | None = None
    remove_keyboard: bool = False
    callback_notice: str = "Done"


class TrainerService:
    def __init__(
        self,
        repository: FeedbackRepository,
        *,
        trainer_user_id: int,
        review_chat_id: int,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.trainer_user_id = trainer_user_id
        self.review_chat_id = review_chat_id
        self._now = now or (lambda: datetime.now(UTC))

    def authorized(self, *, user_id: int | None, chat_id: int, chat_type: str) -> bool:
        return (
            user_id == self.trainer_user_id
            and chat_id == self.review_chat_id
            and chat_type == "private"
        )

    def handle_callback(self, payload: str) -> ServiceResult:
        parsed = parse_callback(payload)
        if parsed is None:
            return ServiceResult(callback_notice="Action unavailable")
        action, reply_id, value = parsed
        record = self.repository.get_reply(reply_id)
        if record is None:
            return ServiceResult(callback_notice="Action unavailable")

        if action == "details":
            return ServiceResult(message=details_text(record), callback_notice="Details")
        if action == "bad":
            return ServiceResult(
                edit_reply_id=reply_id,
                keyboard="categories",
                callback_notice="Choose a reason",
            )
        if action == "fix":
            self._set_pending(reply_id, "fix")
            return ServiceResult(
                message=(
                    "Send the reply as Matvey should have written it. "
                    f"Reply #{reply_id}; use /cancel to stop."
                ),
                callback_notice="Waiting for correction",
            )
        if action == "no_reply":
            return self._save(
                reply_id,
                status="rejected",
                category="should_not_reply",
            )
        if action == "good":
            return self._save(reply_id, status="approved")
        if action == "cancel":
            self.repository.clear_pending_interaction(self.trainer_user_id)
            return ServiceResult(
                edit_reply_id=reply_id,
                keyboard="review",
                callback_notice="Cancelled",
            )
        if action == "cat" and value in BAD_CATEGORIES:
            if value == "other":
                self._set_pending(reply_id, "bad_other")
                return ServiceResult(
                    message=f"Send a short reason for #{reply_id}, or /cancel.",
                    callback_notice="Waiting for reason",
                )
            return self._save(reply_id, status="rejected", category=value)
        return ServiceResult(callback_notice="Action unavailable")

    def handle_text(self, text: str) -> ServiceResult:
        pending = self.repository.get_pending_interaction(self.trainer_user_id)
        if pending is None:
            return ServiceResult(message="No pending action. Use /pending to review replies.")
        now = self._now()
        if datetime.fromisoformat(pending.expires_at) <= now:
            self.repository.clear_pending_interaction(self.trainer_user_id)
            return ServiceResult(message="That action expired. Open the review card again.")
        normalized = text.strip()
        if not normalized or normalized.startswith("/"):
            return ServiceResult(message="Send plain non-empty text, or use /cancel.")
        limit = MAX_CORRECTION_LENGTH if pending.kind == "fix" else MAX_COMMENT_LENGTH
        if len(normalized) > limit:
            return ServiceResult(message=f"Text is too long. Maximum: {limit} characters.")

        if pending.kind == "fix":
            result = self._save(
                pending.reply_id,
                status="corrected",
                corrected_reply_text=normalized,
            )
        else:
            result = self._save(
                pending.reply_id,
                status="rejected",
                category="other",
                comment=normalized,
            )
        self.repository.clear_pending_interaction(self.trainer_user_id)
        return ServiceResult(
            message="Feedback saved.",
            edit_reply_id=result.edit_reply_id,
            remove_keyboard=True,
        )

    def cancel(self) -> ServiceResult:
        if self.repository.clear_pending_interaction(self.trainer_user_id):
            return ServiceResult(message="Pending action cancelled.")
        return ServiceResult(message="No pending action.")

    def status(self) -> str:
        counts = self.repository.feedback_counts()
        return (
            "Trainer bot status\n"
            f"Total sent: {counts.total}\n"
            f"Pending: {counts.unreviewed}\n"
            f"Good: {counts.approved}\n"
            f"Bad: {counts.rejected}\n"
            f"Fixed: {counts.corrected}"
            f"\nDelivery failures: {counts.delivery_failures}"
        )

    def _save(
        self,
        reply_id: int,
        *,
        status: str,
        category: str | None = None,
        comment: str | None = None,
        corrected_reply_text: str | None = None,
    ) -> ServiceResult:
        saved = self.repository.save_feedback(
            reply_id,
            FeedbackUpdate(
                status=status,
                category=category,
                comment=comment,
                corrected_reply_text=corrected_reply_text,
                updated_at=self._now().isoformat(),
                source="trainer_bot",
                trainer_user_id=self.trainer_user_id,
            ),
        )
        if not saved:
            return ServiceResult(callback_notice="Action unavailable")
        return ServiceResult(
            edit_reply_id=reply_id,
            remove_keyboard=True,
            callback_notice="Feedback saved",
        )

    def _set_pending(self, reply_id: int, kind: str) -> None:
        now = self._now()
        self.repository.set_pending_interaction(
            PendingInteraction(
                trainer_user_id=self.trainer_user_id,
                reply_id=reply_id,
                kind=kind,
                created_at=now.isoformat(),
                expires_at=(now + PENDING_TTL).isoformat(),
            )
        )
