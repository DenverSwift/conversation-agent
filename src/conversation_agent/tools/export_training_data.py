"""Export provider-independent human-authored Telegram style examples."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from conversation_agent.main import validate_runtime_files
from conversation_agent.runtime import AlreadyRunningError, SingleInstanceLock
from conversation_agent.settings import Settings
from conversation_agent.storage.sqlite_repository import SQLiteFeedbackRepository
from conversation_agent.telegram.client import create_telegram_client
from conversation_agent.training.exporter import (
    build_training_examples,
    write_training_exports,
)
from conversation_agent.training.models import HistoryMessage


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export provider-independent Matvey-authored examples for retrieval, "
            "evaluation, and prompt development."
        )
    )
    parser.parse_args()
    try:
        summary = asyncio.run(export_training_data())
    except AlreadyRunningError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"Dataset export failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


async def export_training_data() -> dict[str, Any]:
    settings = Settings.load()
    validate_runtime_files(settings)

    with SingleInstanceLock(settings.runtime_dir):
        known_ai_ids = _known_ai_ids(settings)
        print("[1/3] Подключение к Telegram...", file=sys.stderr)
        client = await create_telegram_client(settings)
        try:
            me = await client.get_me()
            own_user_id = int(me.id)
            entity = await client.get_entity(settings.allowed_telegram_user_id)
            if entity.__class__.__name__ != "User":
                raise ValueError("Allowed Telegram ID must resolve to a private user")
            peer = await client.get_input_entity(entity)
            fetch_limit = max(settings.training_export_limit * 3, 500)
            print(f"[2/3] Чтение последних {fetch_limit} сообщений для пользователя ID={settings.allowed_telegram_user_id}...", file=sys.stderr)
            messages = await _read_dialog_history(client, peer, limit=fetch_limit)
            print(f"      Всего загружено сообщений из Telegram: {len(messages)}", file=sys.stderr)
            print("[3/3] Формирование обучающего датасета...", file=sys.stderr)
            examples, extraction_stats = build_training_examples(
                messages,
                dialog_id=settings.allowed_telegram_user_id,
                own_user_id=own_user_id,
                known_ai_message_ids=known_ai_ids,
                limit=settings.training_export_limit,
                context_limit=settings.training_export_context_limit,
            )
            summary = write_training_exports(
                output_directory=settings.training_export_directory,
                raw_examples=examples,
                extraction_stats=extraction_stats,
                redact_pii=settings.training_export_redact_pii,
                export_limit=settings.training_export_limit,
                context_limit=settings.training_export_context_limit,
            )
            print(
                f"Успешно экспортировано! Исходных примеров: {summary.get('raw_example_count', 0)}, "
                f"очищенных: {summary.get('cleaned_example_count', 0)} -> {settings.training_export_directory}",
                file=sys.stderr,
            )
            return summary
        finally:
            await client.disconnect()


async def _read_dialog_history(client: Any, peer: Any, limit: int | None = None) -> list[HistoryMessage]:
    raw_messages: list[Any] = []
    async for message in client.iter_messages(peer, limit=limit):
        raw_messages.append(message)
    raw_messages.sort(key=lambda m: getattr(m, "id", 0))
    messages: list[HistoryMessage] = []
    for message in raw_messages:
        raw_text = getattr(message, "raw_text", None)
        text = raw_text.strip() if isinstance(raw_text, str) else ""
        messages.append(
            HistoryMessage(
                id=int(getattr(message, "id", 0)),
                sender_id=getattr(message, "sender_id", None),
                text=text,
                date=getattr(message, "date", None),
                outgoing=bool(getattr(message, "out", False)),
                is_service=getattr(message, "action", None) is not None,
                has_media=getattr(message, "media", None) is not None,
                is_forwarded=getattr(message, "fwd_from", None) is not None,
            )
        )
    return messages


def _known_ai_ids(settings: Settings) -> set[int]:
    if not settings.feedback_database_path.exists():
        return set()
    repository = SQLiteFeedbackRepository(settings.feedback_database_path)
    repository.initialize()
    return repository.sent_message_ids(settings.allowed_telegram_user_id)


if __name__ == "__main__":
    raise SystemExit(main())
