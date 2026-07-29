"""CLI entrypoint for the local Telegram conversation agent."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, cast

from conversation_agent.agent.prompt_builder import build_instructions
from conversation_agent.agent.responder import Responder
from conversation_agent.llm.openai_client import OpenAIReplyClient
from conversation_agent.local_slm.cli import add_local_slm_parsers, run_local_slm_command
from conversation_agent.local_slm.context import LocalContextBuilder
from conversation_agent.local_slm.models import GenerationMode
from conversation_agent.local_slm.openai_fallback import OpenAIReplyFallbackProvider
from conversation_agent.local_slm.policy import RuleBasedDialoguePolicy
from conversation_agent.local_slm.provider import (
    FakeLocalGenerationProvider,
    OpenAICompatibleLocalProvider,
)
from conversation_agent.local_slm.reply_client import LocalRouterReplyClient
from conversation_agent.local_slm.router import HybridGenerationRouter
from conversation_agent.local_slm.validator import OutputValidator
from conversation_agent.runtime import AlreadyRunningError, SingleInstanceLock
from conversation_agent.settings import Settings
from conversation_agent.storage.repository import FeedbackRepository
from conversation_agent.storage.sqlite_repository import SQLiteFeedbackRepository
from conversation_agent.style.bundle import load_style_bundle
from conversation_agent.style.runtime import StyleRuntime
from conversation_agent.telegram.client import create_telegram_client, register_message_handler
from conversation_agent.trainer.notification_client import TrainerNotificationClient

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Telegram conversation agent.")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("run").set_defaults(func=None)
    subparsers.add_parser("login").set_defaults(func=None)
    add_local_slm_parsers(subparsers)
    args = parser.parse_args()
    if args.command is None:
        args.command = "run"

    try:
        if args.command == "login":
            asyncio.run(login())
        elif hasattr(args, "func") and args.func is not None:
            return run_local_slm_command(args)
        else:
            asyncio.run(run_agent())
    except KeyboardInterrupt:
        logger.info("Stopped by Ctrl+C")
    except AlreadyRunningError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        logger.exception("Agent stopped because startup or runtime failed")
        print(f"Agent failed: {exc}", file=sys.stderr)
        return 1
    return 0


async def login() -> None:
    settings = Settings.load()
    configure_logging(settings.log_path)
    client = await create_telegram_client(settings)
    try:
        me = await client.get_me()
        logger.info("Telegram session created for account_id=%s", getattr(me, "id", "unknown"))
        print("Telegram login completed.")
    finally:
        await client.disconnect()


async def run_agent() -> None:
    settings = Settings.load()
    configure_logging(settings.log_path)
    validate_runtime_files(settings)

    with SingleInstanceLock(settings.runtime_dir):
        feedback_repository = create_feedback_repository(settings)
        style_runtime = create_style_runtime(settings, feedback_repository)
        trainer_bot: Any | None = None
        review_notifier: TrainerNotificationClient | None = None
        if settings.trainer_bot_enabled:
            if feedback_repository is None:
                raise ValueError("FEEDBACK_ENABLED must be true when TRAINER_BOT_ENABLED is true")
            assert settings.trainer_bot_token is not None
            assert settings.trainer_bot_review_chat_id is not None
            from telegram import Bot

            from conversation_agent.trainer.bot import telegram_markup

            trainer_bot = Bot(settings.trainer_bot_token)
            await trainer_bot.initialize()
            review_notifier = TrainerNotificationClient(
                bot=trainer_bot,
                repository=feedback_repository,
                review_chat_id=settings.trainer_bot_review_chat_id,
                markup_factory=telegram_markup,
            )
        client: Any | None = None
        try:
            active_client = await create_telegram_client(settings)
            client = active_client
            me = await active_client.get_me()
            own_user_id = int(me.id)
            instructions = build_instructions(settings.readme_path)
            reply_client = create_reply_client(settings)
            responder = Responder(
                reply_client,
                instructions,
                style_runtime=style_runtime,
            )
            register_message_handler(
                active_client,
                settings=settings,
                responder=responder,
                own_user_id=own_user_id,
                dialog_locks={},
                feedback_repository=feedback_repository,
                review_notifier=review_notifier,
            )
            logger.info(
                "Agent started for allowed_user_id=%s with context_limit=%s",
                settings.allowed_telegram_user_id,
                settings.context_message_limit,
            )
            await wait_until_stopped(active_client, settings.runtime_dir / "agent.stop")
        finally:
            if client is not None:
                await client.disconnect()
            if trainer_bot is not None:
                await trainer_bot.shutdown()
            logger.info("Telegram client disconnected")


def create_feedback_repository(settings: Settings) -> FeedbackRepository | None:
    if not settings.feedback_enabled:
        logger.info("Local feedback collection is disabled")
        return None
    repository = SQLiteFeedbackRepository(settings.feedback_database_path)
    repository.initialize()
    logger.info("Local feedback storage initialized")
    return repository


def create_style_runtime(
    settings: Settings,
    repository: FeedbackRepository | None,
) -> StyleRuntime | None:
    if not settings.style_adaptation_enabled:
        logger.info("Runtime style adaptation is disabled")
        return None
    try:
        bundle = load_style_bundle(
            settings.style_bundle_directory,
            contact_id=settings.allowed_telegram_user_id,
            state_path=settings.style_compiler_state_path,
        )
    except ValueError:
        if settings.style_require_bundle:
            raise
        logger.warning("Style bundle unavailable; continuing with AAA.3 prompt behavior")
        return None
    logger.info(
        "Style bundle loaded source_examples=%s rules=%s",
        bundle.source_example_count,
        len(bundle.rules),
    )
    return StyleRuntime(
        bundle=bundle,
        bundle_directory=settings.style_bundle_directory,
        repository=repository,
        contact_id=settings.allowed_telegram_user_id,
        retrieval_limit=settings.style_retrieval_limit,
        rules_max_chars=settings.style_rules_max_chars,
        examples_max_chars=settings.style_examples_max_chars,
    )


def create_reply_client(settings: Settings) -> Any:
    if settings.generation_mode == "openai_only":
        return OpenAIReplyClient(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            timeout_seconds=settings.openai_timeout_seconds,
        )
    if settings.local_generation_provider == "openai_compatible":
        local_provider = OpenAICompatibleLocalProvider(
            base_url=settings.local_generation_base_url,
            model=settings.local_generation_model,
            api_key=settings.local_generation_api_key,
            timeout_seconds=settings.local_generation_timeout_seconds,
            max_output_tokens=settings.local_generation_max_output_tokens,
            temperature=settings.local_generation_temperature,
            top_k=settings.local_generation_top_k,
            top_p=settings.local_generation_top_p,
            min_p=settings.local_generation_min_p,
            presence_penalty=settings.local_generation_presence_penalty,
            context_tokens=settings.local_generation_context_tokens,
            thinking=settings.local_generation_thinking,
            seed=settings.local_generation_seed,
        )
    else:
        local_provider = FakeLocalGenerationProvider()
    fallback_provider = None
    if settings.generation_mode in {"local_with_fallback", "compare_shadow"}:
        fallback_provider = OpenAIReplyFallbackProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            timeout_seconds=settings.openai_timeout_seconds,
        )
    router = HybridGenerationRouter(
        local_provider=local_provider,
        validator=OutputValidator(),
        mode=cast(GenerationMode, settings.generation_mode),
        fallback_provider=fallback_provider,
        low_confidence_threshold=settings.local_generation_low_confidence_threshold,
    )
    return LocalRouterReplyClient(
        policy=RuleBasedDialoguePolicy(),
        context_builder=LocalContextBuilder(budget_chars=settings.local_context_budget_chars),
        router=router,
        agent_id=settings.local_agent_id,
    )


def configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def validate_runtime_files(settings: Settings) -> None:
    if not settings.readme_path.exists():
        raise ValueError(f"README file not found: {settings.readme_path}")
    session_path = Path(settings.telegram_session_path)
    session_file = session_path if session_path.suffix == ".session" else session_path.with_suffix(".session")
    if not session_file.exists():
        raise ValueError("Telegram session not found. Run scripts\\login_telegram.bat first.")


async def wait_until_stopped(client: Any, stop_path: Path) -> None:
    stop_path.unlink(missing_ok=True)
    disconnected = asyncio.create_task(client.run_until_disconnected())
    stop_requested = asyncio.create_task(_wait_for_stop_file(stop_path))
    try:
        done, pending = await asyncio.wait(
            {disconnected, stop_requested},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_requested in done:
            logger.info("Stop file detected; disconnecting Telegram client")
            await client.disconnect()
            await disconnected
        elif disconnected in done:
            await disconnected
        for task in pending:
            task.cancel()
    finally:
        stop_path.unlink(missing_ok=True)


async def _wait_for_stop_file(stop_path: Path) -> None:
    while not stop_path.exists():
        await asyncio.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
