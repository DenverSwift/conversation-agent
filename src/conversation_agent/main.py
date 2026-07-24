"""CLI entrypoint for the local Telegram conversation agent."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from conversation_agent.agent.prompt_builder import build_instructions
from conversation_agent.agent.responder import Responder
from conversation_agent.llm.openai_client import OpenAIReplyClient
from conversation_agent.runtime import AlreadyRunningError, SingleInstanceLock
from conversation_agent.settings import Settings
from conversation_agent.telegram.client import create_telegram_client, register_message_handler

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Telegram conversation agent.")
    parser.add_argument("command", nargs="?", choices=("run", "login"), default="run")
    args = parser.parse_args()

    try:
        if args.command == "login":
            asyncio.run(login())
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
        client = await create_telegram_client(settings)
        try:
            me = await client.get_me()
            own_user_id = int(me.id)
            instructions = build_instructions(settings.readme_path)
            reply_client = OpenAIReplyClient(
                api_key=settings.openai_api_key,
                model=settings.openai_model,
                timeout_seconds=settings.openai_timeout_seconds,
            )
            responder = Responder(reply_client, instructions)
            register_message_handler(
                client,
                settings=settings,
                responder=responder,
                own_user_id=own_user_id,
                dialog_locks={},
            )
            logger.info(
                "Agent started for allowed_user_id=%s with context_limit=%s",
                settings.allowed_telegram_user_id,
                settings.context_message_limit,
            )
            await wait_until_stopped(client, settings.runtime_dir / "agent.stop")
        finally:
            await client.disconnect()
            logger.info("Telegram client disconnected")


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
