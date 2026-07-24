"""Separate long-polling process for the private trainer bot."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from conversation_agent.main import configure_logging
from conversation_agent.runtime import AlreadyRunningError, SingleInstanceLock
from conversation_agent.settings import Settings
from conversation_agent.storage.sqlite_repository import SQLiteFeedbackRepository
from conversation_agent.trainer.cards import (
    category_keyboard,
    review_card,
    review_keyboard,
)
from conversation_agent.trainer.notification_client import TrainerNotificationClient
from conversation_agent.trainer.service import ServiceResult, TrainerService

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the private trainer feedback bot.")
    parser.add_argument("command", nargs="?", choices=("run",), default="run")
    args = parser.parse_args()
    if args.command != "run":
        return 2
    try:
        asyncio.run(run_trainer_bot())
    except KeyboardInterrupt:
        logger.info("Trainer bot stopped by Ctrl+C")
    except AlreadyRunningError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        logger.exception("Trainer bot stopped because startup or runtime failed")
        print(f"Trainer bot failed: {exc}", file=sys.stderr)
        return 1
    return 0


async def run_trainer_bot() -> None:
    settings = Settings.load()
    configure_logging(Path("logs/trainer-bot.log"))
    if not settings.trainer_bot_enabled:
        raise ValueError("Trainer bot is disabled. Set TRAINER_BOT_ENABLED=true.")
    if not settings.trainer_bot_polling_enabled:
        raise ValueError("Trainer bot polling is disabled.")
    assert settings.trainer_bot_token is not None
    assert settings.trainer_telegram_user_id is not None
    assert settings.trainer_bot_review_chat_id is not None

    from telegram.ext import ApplicationBuilder

    repository = SQLiteFeedbackRepository(settings.feedback_database_path)
    repository.initialize()
    application = ApplicationBuilder().token(settings.trainer_bot_token).build()
    service = TrainerService(
        repository,
        trainer_user_id=settings.trainer_telegram_user_id,
        review_chat_id=settings.trainer_bot_review_chat_id,
    )
    register_handlers(application, service, repository)
    notifier = TrainerNotificationClient(
        bot=application.bot,
        repository=repository,
        review_chat_id=settings.trainer_bot_review_chat_id,
        markup_factory=telegram_markup,
    )
    stop_path = settings.runtime_dir / "trainer_bot.stop"

    with SingleInstanceLock(settings.runtime_dir, name="trainer_bot"):
        stop_path.unlink(missing_ok=True)
        await application.initialize()
        await application.start()
        updater = application.updater
        if updater is None:
            raise RuntimeError("Trainer bot polling updater is unavailable")
        try:
            await updater.start_polling(drop_pending_updates=False)
            retried = await notifier.retry_pending()
            logger.info("Trainer bot started; startup_cards_delivered=%s", retried)
            await _wait_for_stop_file(stop_path)
        finally:
            if updater.running:
                await updater.stop()
            if application.running:
                await application.stop()
            await application.shutdown()
            stop_path.unlink(missing_ok=True)
            logger.info("Trainer bot stopped cleanly")


def register_handlers(application: Any, service: TrainerService, repository: Any) -> None:
    from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters

    async def command(update: Any, context: Any) -> None:
        del context
        if not _authorized(update, service):
            await _deny(update)
            return
        name = str(update.effective_message.text).split()[0].lower()
        if name in {"/start", "/help"}:
            await update.effective_message.reply_text(
                "Private Conversation Agent trainer.\n"
                "/status - feedback counts\n"
                "/recent - recent replies\n"
                "/pending - unreviewed replies\n"
                "/cancel - cancel text entry"
            )
        elif name == "/status":
            await update.effective_message.reply_text(service.status())
        elif name == "/cancel":
            await update.effective_message.reply_text(service.cancel().message)
        elif name in {"/recent", "/pending"}:
            records = repository.recent_replies(
                limit=5,
                unreviewed_only=name == "/pending",
            )
            if not records:
                await update.effective_message.reply_text("No matching replies.")
            for record in reversed(records):
                await update.effective_message.reply_text(
                    review_card(record),
                    parse_mode="HTML",
                    reply_markup=telegram_markup(review_keyboard(record.id)),
                    disable_web_page_preview=True,
                )

    async def callback(update: Any, context: Any) -> None:
        del context
        query = update.callback_query
        if query is None:
            return
        if not _authorized(update, service):
            logger.warning(
                "Unauthorized trainer callback user_id=%s chat_id=%s",
                getattr(update.effective_user, "id", None),
                getattr(update.effective_chat, "id", None),
            )
            await query.answer("Action unavailable", show_alert=True)
            return
        result = service.handle_callback(query.data or "")
        await query.answer(result.callback_notice)
        await _apply_result(update, result, service, repository)

    async def text(update: Any, context: Any) -> None:
        del context
        if not _authorized(update, service):
            await _deny(update)
            return
        result = service.handle_text(update.effective_message.text or "")
        await _apply_result(update, result, service, repository)

    for name in ("start", "help", "status", "recent", "pending", "cancel"):
        application.add_handler(CommandHandler(name, command))
    application.add_handler(CallbackQueryHandler(callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text))


def telegram_markup(rows: list[list[tuple[str, str]]]) -> Any:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(label, callback_data=data) for label, data in row]
            for row in rows
        ]
    )


def _authorized(update: Any, service: TrainerService) -> bool:
    user = update.effective_user
    chat = update.effective_chat
    return bool(
        user
        and chat
        and service.authorized(
            user_id=user.id,
            chat_id=chat.id,
            chat_type=chat.type,
        )
    )


async def _deny(update: Any) -> None:
    logger.warning(
        "Unauthorized trainer message user_id=%s chat_id=%s",
        getattr(update.effective_user, "id", None),
        getattr(update.effective_chat, "id", None),
    )
    message = update.effective_message
    if message is not None:
        await message.reply_text("This private bot is not available.")


async def _apply_result(
    update: Any,
    result: ServiceResult,
    service: TrainerService,
    repository: Any,
) -> None:
    if result.message:
        await update.effective_message.reply_text(result.message)
    if result.edit_reply_id is None:
        return
    record = repository.get_reply(result.edit_reply_id)
    if record is None:
        return
    if result.keyboard == "categories":
        markup = telegram_markup(category_keyboard(record.id))
    elif result.keyboard == "review":
        markup = telegram_markup(review_keyboard(record.id))
    else:
        markup = None
    query = update.callback_query
    if query is not None and query.message is not None:
        await query.edit_message_text(
            review_card(record),
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    elif record.trainer_review_chat_id and record.trainer_review_message_id:
        await update.get_bot().edit_message_text(
            chat_id=record.trainer_review_chat_id,
            message_id=record.trainer_review_message_id,
            text=review_card(record),
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )


async def _wait_for_stop_file(stop_path: Path) -> None:
    while not stop_path.exists():
        await asyncio.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
