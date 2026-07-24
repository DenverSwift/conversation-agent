"""Feedback commands handled in Matvey's Telegram Saved Messages."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from conversation_agent.agent.context_builder import telegram_text
from conversation_agent.storage.models import FeedbackUpdate
from conversation_agent.storage.repository import FeedbackRepository

logger = logging.getLogger(__name__)

FEEDBACK_CATEGORIES = frozenset(
    {
        "too_formal",
        "too_informal",
        "too_long",
        "too_short",
        "wrong_tone",
        "wrong_facts",
        "invented_information",
        "missed_context",
        "bad_emoji",
        "unnatural_phrase",
        "should_not_reply",
        "other",
    }
)
FEEDBACK_COMMANDS = frozenset({"/good", "/bad", "/fix", "/feedback_help"})


class FeedbackCommandError(ValueError):
    """Raised when a feedback command has invalid syntax."""


@dataclass(frozen=True)
class FeedbackCommand:
    name: str
    reply_id: int | None = None
    category: str | None = None
    comment: str | None = None
    corrected_reply_text: str | None = None


def parse_feedback_command(text: str) -> FeedbackCommand:
    stripped = text.strip()
    command_name = stripped.split(maxsplit=1)[0].lower() if stripped else ""
    if command_name not in FEEDBACK_COMMANDS:
        raise FeedbackCommandError("Unknown feedback command.")

    if command_name == "/feedback_help":
        if stripped != command_name:
            raise FeedbackCommandError("Use /feedback_help without arguments.")
        return FeedbackCommand(name="help")

    parts = stripped.split(maxsplit=2)
    if len(parts) < 2:
        raise FeedbackCommandError(f"Use {command_name} with a reply ID.")
    reply_id = _parse_reply_id(parts[1])

    if command_name == "/good":
        if len(parts) != 2:
            raise FeedbackCommandError("Use /good <reply_id>.")
        return FeedbackCommand(name="good", reply_id=reply_id)

    if len(parts) != 3 or not parts[2].strip():
        if command_name == "/bad":
            raise FeedbackCommandError("Use /bad <reply_id> <category or comment>.")
        raise FeedbackCommandError("Use /fix <reply_id> <corrected reply>.")

    detail = parts[2].strip()
    if command_name == "/fix":
        return FeedbackCommand(
            name="fix",
            reply_id=reply_id,
            corrected_reply_text=detail,
        )

    category_candidate, separator, remaining = detail.partition(" ")
    if category_candidate in FEEDBACK_CATEGORIES:
        category = category_candidate
        comment = remaining.strip() if separator and remaining.strip() else None
    else:
        category = "other"
        comment = detail
    return FeedbackCommand(
        name="bad",
        reply_id=reply_id,
        category=category,
        comment=comment,
    )


def is_saved_messages_event(event: Any, own_user_id: int) -> bool:
    return (
        bool(getattr(event, "is_private", False))
        and getattr(event, "chat_id", None) == own_user_id
        and getattr(event, "sender_id", None) == own_user_id
    )


def is_feedback_command(text: str) -> bool:
    if not text.strip():
        return False
    return text.strip().split(maxsplit=1)[0].lower() in FEEDBACK_COMMANDS


async def handle_feedback_event(
    event: Any,
    *,
    own_user_id: int,
    repository: FeedbackRepository | None,
) -> bool:
    if repository is None or not is_saved_messages_event(event, own_user_id):
        return False

    text = telegram_text(event)
    if not is_feedback_command(text):
        return False

    try:
        command = parse_feedback_command(text)
    except FeedbackCommandError as exc:
        await event.respond(str(exc))
        return True

    if command.name == "help":
        await event.respond(feedback_help_text())
        return True

    feedback = _feedback_update(command)
    try:
        saved = repository.save_feedback(_required_reply_id(command), feedback)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Feedback storage update failed error_type=%s",
            type(exc).__name__,
        )
        await event.respond("Feedback could not be saved. Check the local agent log.")
        return True

    reply_id = _required_reply_id(command)
    if not saved:
        await event.respond(f"Reply #{reply_id} was not found.")
        return True

    status_messages = {
        "good": f"Reply #{reply_id} marked good.",
        "bad": f"Reply #{reply_id} marked bad.",
        "fix": f"Correction saved for reply #{reply_id}.",
    }
    await event.respond(status_messages[command.name])
    return True


def feedback_help_text() -> str:
    categories = ", ".join(sorted(FEEDBACK_CATEGORIES))
    return (
        "Feedback commands:\n"
        "/good <reply_id>\n"
        "/bad <reply_id> <category or comment>\n"
        "/fix <reply_id> <corrected reply>\n"
        "/feedback_help\n\n"
        f"Categories: {categories}"
    )


def feedback_card(reply_id: int, dialog_id: int, generated_reply: str) -> str:
    compact_reply = generated_reply.strip()
    if len(compact_reply) > 1000:
        compact_reply = f"{compact_reply[:997]}..."
    return (
        f"[AI reply #{reply_id}]\n\n"
        f"Dialog: {dialog_id}\n"
        f"Sent:\n{compact_reply}\n\n"
        "Feedback commands:\n"
        f"/good {reply_id}\n"
        f"/bad {reply_id} wrong_tone\n"
        f"/fix {reply_id} <corrected reply>"
    )


def _parse_reply_id(value: str) -> int:
    try:
        reply_id = int(value)
    except ValueError as exc:
        raise FeedbackCommandError("Reply ID must be a positive integer.") from exc
    if reply_id <= 0:
        raise FeedbackCommandError("Reply ID must be a positive integer.")
    return reply_id


def _required_reply_id(command: FeedbackCommand) -> int:
    if command.reply_id is None:
        raise FeedbackCommandError("Feedback command has no reply ID.")
    return command.reply_id


def _feedback_update(command: FeedbackCommand) -> FeedbackUpdate:
    now = datetime.now(UTC).isoformat()
    if command.name == "good":
        return FeedbackUpdate(status="approved", updated_at=now)
    if command.name == "bad":
        return FeedbackUpdate(
            status="rejected",
            updated_at=now,
            category=command.category,
            comment=command.comment,
        )
    if command.name == "fix":
        return FeedbackUpdate(
            status="corrected",
            updated_at=now,
            corrected_reply_text=command.corrected_reply_text,
        )
    raise FeedbackCommandError("Unsupported feedback command.")
