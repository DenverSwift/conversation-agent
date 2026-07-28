"""CLI entrypoint for the local Telegram conversation agent."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from conversation_agent.agent.analyzer import InteractionAnalyzer
from conversation_agent.agent.context_builder import ChatMessage
from conversation_agent.agent.goal_planner import GoalPlanner
from conversation_agent.agent.pipeline import ConversationPipeline
from conversation_agent.agent.prompt_builder import load_readme_behavior
from conversation_agent.agent.prompt_composer import PromptComposer
from conversation_agent.agent.response_generator import ResponseGenerator
from conversation_agent.agent.retriever import ExampleRetriever
from conversation_agent.domain.models import (
    ConversationState,
    IncomingMessage,
    IncomingMessageGroup,
    RelationshipProfile,
)
from conversation_agent.domain.profiles import (
    load_business_profile,
    load_identity_profile,
    load_style_profile,
)
from conversation_agent.llm.conversation_client import (
    DeterministicFakeProvider,
    OpenAIConversationClient,
)
from conversation_agent.runtime import AlreadyRunningError, SingleInstanceLock
from conversation_agent.settings import Settings, load_env_file
from conversation_agent.storage.repository import FeedbackRepository
from conversation_agent.storage.sqlite_repository import SQLiteFeedbackRepository
from conversation_agent.style.bundle import load_style_bundle
from conversation_agent.style.runtime import StyleRuntime
from conversation_agent.telegram.approval import ApprovalActionWorker
from conversation_agent.telegram.behavior import (
    BehaviorConfig,
    TelegramBehaviorPlanner,
    TelegramBehaviorRuntime,
)
from conversation_agent.telegram.client import (
    create_telegram_client,
    register_orchestrator_handler,
)
from conversation_agent.telegram.handlers import account_replied_after
from conversation_agent.telegram.orchestrator import TelegramConversationOrchestrator
from conversation_agent.trainer.notification_client import TrainerNotificationClient

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Telegram conversation agent.")
    parser.add_argument(
        "command",
        nargs="?",
        choices=(
            "run",
            "login",
            "simulate",
            "inspect-conversation",
            "inspect-draft",
        ),
        default="run",
    )
    parser.add_argument("identifier", nargs="?")
    parser.add_argument("--shadow", action="store_true")
    parser.add_argument("--contact-id", default="test-contact")
    parser.add_argument("--message")
    args = parser.parse_args()

    try:
        if args.command == "login":
            asyncio.run(login())
        elif args.command == "simulate":
            if not args.message:
                parser.error("simulate requires --message")
            asyncio.run(simulate(contact_id=args.contact_id, message=args.message))
        elif args.command == "inspect-conversation":
            if not args.identifier:
                parser.error("inspect-conversation requires an ID")
            inspect_conversation(args.identifier)
        elif args.command == "inspect-draft":
            if not args.identifier or not args.identifier.isdigit():
                parser.error("inspect-draft requires a numeric ID")
            inspect_draft(int(args.identifier))
        else:
            asyncio.run(run_agent(force_shadow=args.shadow))
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


async def run_agent(*, force_shadow: bool = False) -> None:
    settings = Settings.load()
    configure_logging(settings.log_path)
    validate_runtime_files(settings)

    with SingleInstanceLock(settings.runtime_dir):
        feedback_repository = create_feedback_repository(settings)
        if feedback_repository is None:
            raise ValueError("FEEDBACK_ENABLED must be true for approval-first AA.1")
        if not isinstance(feedback_repository, SQLiteFeedbackRepository):
            raise TypeError("AA.1 requires the local SQLite repository")
        if not settings.trainer_bot_enabled:
            raise ValueError("TRAINER_BOT_ENABLED must be true for approval-first AA.1")
        if not (settings.shadow_mode or force_shadow):
            raise ValueError("AA.1 supports only shadow/approval mode")
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
        orchestrator: TelegramConversationOrchestrator | None = None
        approval_stop = asyncio.Event()
        approval_task: asyncio.Task[None] | None = None
        try:
            active_client = await create_telegram_client(settings)
            client = active_client
            me = await active_client.get_me()
            own_user_id = int(me.id)
            identity = load_identity_profile(settings.identity_profile_path)
            business = load_business_profile(settings.business_profile_path)
            style = load_style_profile(settings.style_profile_path)
            pipeline = create_conversation_pipeline(
                settings,
                feedback_repository,
            )
            assert review_notifier is not None
            orchestrator = TelegramConversationOrchestrator(
                settings=settings,
                client=active_client,
                own_user_id=own_user_id,
                repository=feedback_repository,
                pipeline=pipeline,
                identity=identity,
                business=business,
                style=style,
                review_notifier=review_notifier,
            )
            register_orchestrator_handler(
                active_client,
                settings=settings,
                orchestrator=orchestrator,
            )
            behavior_runtime = TelegramBehaviorRuntime(
                client=active_client,
                repository=feedback_repository,
                manual_reply_check=lambda contact_id, incoming_message_id: account_replied_after(
                    active_client,
                    int(contact_id),
                    incoming_message_id,
                    own_user_id,
                ),
            )
            approval_worker = ApprovalActionWorker(
                repository=feedback_repository,
                behavior_runtime=behavior_runtime,
                poll_interval_seconds=settings.approval_poll_interval_seconds,
            )
            approval_task = asyncio.create_task(approval_worker.run(approval_stop))
            logger.info(
                "AA.1 shadow agent started allowed_contacts=%s context_limit=%s",
                settings.allowed_contact_ids,
                settings.context_message_limit,
            )
            await wait_until_stopped(active_client, settings.runtime_dir / "agent.stop")
        finally:
            approval_stop.set()
            if orchestrator is not None:
                await orchestrator.close()
            if approval_task is not None:
                await approval_task
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


def create_conversation_pipeline(
    settings: Settings,
    repository: FeedbackRepository | None,
    *,
    provider: Any | None = None,
) -> ConversationPipeline:
    bundle = None
    compiled_rules = load_readme_behavior(settings.readme_path)
    if settings.style_adaptation_enabled:
        try:
            bundle = load_style_bundle(
                settings.style_bundle_directory,
                contact_id=settings.allowed_telegram_user_id,
                state_path=settings.style_compiler_state_path,
            )
            compiled_rules = "\n\n".join(
                value
                for value in (compiled_rules, bundle.rules_markdown)
                if value.strip()
            )
        except ValueError:
            if settings.style_require_bundle:
                raise
            logger.warning("Style bundle unavailable; using editable style profile only")
    active_provider = provider or OpenAIConversationClient(
        api_key=settings.openai_api_key,
        timeout_seconds=settings.openai_timeout_seconds,
    )
    return ConversationPipeline(
        analyzer=InteractionAnalyzer(
            active_provider,
            model=settings.analysis_model or settings.openai_model,
        ),
        goal_planner=GoalPlanner(handoff_threshold=settings.handoff_threshold),
        retriever=ExampleRetriever(
            bundle=bundle,
            repository=repository,
            limit=settings.style_retrieval_limit,
        ),
        prompt_composer=PromptComposer(token_budget=settings.prompt_token_budget),
        response_generator=ResponseGenerator(
            active_provider,
            model=settings.response_model or settings.openai_model,
            max_bubble_count=settings.max_bubble_count,
            max_message_length=settings.max_message_length,
        ),
        behavior_planner=TelegramBehaviorPlanner(_behavior_config(settings)),
        compiled_style_rules=compiled_rules,
    )


async def simulate(*, contact_id: str, message: str) -> None:
    load_env_file(Path(".env"))
    identity_path = Path(os.environ.get("IDENTITY_PROFILE_PATH", "config/identity.example.json"))
    business_path = Path(os.environ.get("BUSINESS_PROFILE_PATH", "config/business.example.json"))
    style_path = Path(os.environ.get("STYLE_PROFILE_PATH", "config/style.example.json"))
    readme_path = Path(os.environ.get("README_PATH", "README.md"))
    identity = load_identity_profile(identity_path)
    business = load_business_profile(business_path)
    style = load_style_profile(style_path)
    provider = DeterministicFakeProvider()
    pipeline = ConversationPipeline(
        analyzer=InteractionAnalyzer(provider, model="offline-simulation"),
        goal_planner=GoalPlanner(handoff_threshold=0.25),
        retriever=ExampleRetriever(bundle=None, repository=None, limit=8),
        prompt_composer=PromptComposer(token_budget=6000),
        response_generator=ResponseGenerator(
            provider,
            model="offline-simulation",
            max_bubble_count=4,
            max_message_length=1200,
        ),
        behavior_planner=TelegramBehaviorPlanner(_default_behavior_config()),
        compiled_style_rules=load_readme_behavior(readme_path),
    )
    now = datetime.now(UTC).isoformat()
    group = IncomingMessageGroup(
        group_id=f"{contact_id}:simulation",
        contact_id=contact_id,
        messages=(
            IncomingMessage(
                message_id=1,
                contact_id=contact_id,
                text=message,
                received_at=now,
            ),
        ),
        started_at=now,
        completed_at=now,
    )
    result = await pipeline.process(
        group=group,
        recent_messages=[
            ChatMessage(
                role="user",
                content=message,
                message_id=1,
                provenance="contact",
            )
        ],
        identity=identity,
        business=business,
        style=style,
        relationship=RelationshipProfile.neutral(contact_id),
        state=ConversationState.initial(contact_id),
    )
    output = {
        "analyzer": asdict(result.analysis),
        "active_goal": asdict(result.goal),
        "retrieved_examples": list(result.prompt.retrieved_example_ids),
        "generated_response": asdict(result.response),
        "bubble_split": list(result.response.messages),
        "timing_plan": result.behavior.to_dict(),
        "handoff_decision": result.response.handoff_required,
        "prompt_inspection": result.prompt.inspection,
    }
    _print_json(output)


def inspect_conversation(identifier: str) -> None:
    repository = _inspection_repository()
    conversation_id = identifier if identifier.startswith("telegram:") else f"telegram:{identifier}"
    snapshot = repository.conversation_snapshot(conversation_id)
    if snapshot is None:
        raise ValueError(f"Conversation not found: {identifier}")
    _print_json(snapshot)


def inspect_draft(draft_id: int) -> None:
    repository = _inspection_repository()
    snapshot = repository.draft_snapshot(draft_id)
    if snapshot is None:
        raise ValueError(f"Draft not found: {draft_id}")
    _print_json(snapshot)


def _inspection_repository() -> SQLiteFeedbackRepository:
    load_env_file(Path(".env"))
    path = Path(os.environ.get("FEEDBACK_DATABASE_PATH", ".runtime/feedback.sqlite3"))
    if not path.exists():
        raise ValueError(f"Conversation database not found: {path}")
    repository = SQLiteFeedbackRepository(path)
    repository.initialize()
    return repository


def _print_json(value: Any) -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _behavior_config(settings: Settings) -> BehaviorConfig:
    return BehaviorConfig(
        typing_speed_min_chars_per_second=settings.typing_speed_min_chars_per_second,
        typing_speed_max_chars_per_second=settings.typing_speed_max_chars_per_second,
        delay_jitter_ms=settings.behavior_delay_jitter_ms,
        initial_read_delay_min_ms=settings.initial_read_delay_min_ms,
        initial_read_delay_max_ms=settings.initial_read_delay_max_ms,
        pre_typing_delay_min_ms=settings.pre_typing_delay_min_ms,
        pre_typing_delay_max_ms=settings.pre_typing_delay_max_ms,
        bubble_delay_min_ms=settings.bubble_delay_min_ms,
        bubble_delay_max_ms=settings.bubble_delay_max_ms,
    )


def _default_behavior_config() -> BehaviorConfig:
    return BehaviorConfig(
        typing_speed_min_chars_per_second=7,
        typing_speed_max_chars_per_second=13,
        delay_jitter_ms=350,
        initial_read_delay_min_ms=800,
        initial_read_delay_max_ms=3500,
        pre_typing_delay_min_ms=500,
        pre_typing_delay_max_ms=2500,
        bubble_delay_min_ms=500,
        bubble_delay_max_ms=1800,
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
    session_file = (
        session_path if session_path.suffix == ".session" else session_path.with_suffix(".session")
    )
    if not session_file.exists():
        raise ValueError("Telegram session not found. Run scripts\\login_telegram.bat first.")
    for profile_path in (
        settings.identity_profile_path,
        settings.business_profile_path,
        settings.style_profile_path,
    ):
        if not profile_path.exists():
            raise ValueError(f"Profile file not found: {profile_path}")


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
