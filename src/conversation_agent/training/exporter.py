"""Deterministic extraction of human-authored Telegram reply examples."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from conversation_agent.training.cleaning import clean_examples
from conversation_agent.training.models import (
    ContextTurn,
    ExtractionStats,
    HistoryMessage,
    TrainingExample,
)

FRAGMENT_GAP_SECONDS = 180


@dataclass
class _MessageGroup:
    role: str
    texts: list[str]
    message_ids: list[int]
    first_date: datetime | None
    last_date: datetime | None
    is_forwarded: bool

    @property
    def text(self) -> str:
        return "\n".join(self.texts)


def build_training_examples(
    messages: list[HistoryMessage],
    *,
    dialog_id: int,
    own_user_id: int,
    known_ai_message_ids: set[int],
    limit: int,
    context_limit: int,
) -> tuple[list[TrainingExample], ExtractionStats]:
    stats = ExtractionStats()
    groups: list[_MessageGroup] = []
    merge_allowed = False

    for message in sorted(messages, key=_message_order_key):
        stats.messages_scanned += 1
        if message.is_service:
            stats.service_messages_excluded += 1
            merge_allowed = False
            continue
        if message.has_media:
            stats.media_messages_excluded += 1
            merge_allowed = False
            continue
        if not message.text.strip():
            stats.empty_messages_excluded += 1
            merge_allowed = False
            continue
        if message.id in known_ai_message_ids:
            stats.ai_generated_excluded += 1
            merge_allowed = False
            continue

        role = "assistant" if message.outgoing or message.sender_id == own_user_id else "user"
        if merge_allowed and groups and _can_merge(groups[-1], message, role):
            group = groups[-1]
            group.texts.append(message.text.strip())
            group.message_ids.append(message.id)
            group.last_date = message.date
            group.is_forwarded = group.is_forwarded or message.is_forwarded
        else:
            groups.append(
                _MessageGroup(
                    role=role,
                    texts=[message.text.strip()],
                    message_ids=[message.id],
                    first_date=message.date,
                    last_date=message.date,
                    is_forwarded=message.is_forwarded,
                )
            )
        merge_allowed = True

    examples: list[TrainingExample] = []
    for index, group in enumerate(groups):
        if group.role != "assistant":
            continue
        stats.human_target_replies += 1
        previous_groups = groups[max(0, index - context_limit) : index]
        context = tuple(ContextTurn(role=item.role, text=item.text) for item in previous_groups)
        examples.append(
            TrainingExample(
                example_id=_example_id(dialog_id, group.message_ids),
                dialog_id=dialog_id,
                context=context,
                target_reply=group.text,
                source_message_ids=tuple(group.message_ids),
                created_at=_isoformat(group.last_date),
                is_human_authored=True,
                target_is_forwarded=group.is_forwarded,
            )
        )

    return examples[-limit:], stats


def write_training_exports(
    *,
    output_directory: Path,
    raw_examples: list[TrainingExample],
    extraction_stats: ExtractionStats,
    redact_pii: bool,
    export_limit: int,
    context_limit: int,
) -> dict[str, Any]:
    output_directory.mkdir(parents=True, exist_ok=True)
    cleaned_examples, cleaning_stats = clean_examples(raw_examples, redact_pii=redact_pii)

    _write_jsonl(output_directory / "raw_examples.jsonl", raw_examples)
    _write_jsonl(output_directory / "cleaned_examples.jsonl", cleaned_examples)
    summary: dict[str, Any] = {
        "messages_scanned": extraction_stats.messages_scanned,
        "human_authored_target_replies_found": extraction_stats.human_target_replies,
        "ai_generated_messages_excluded": extraction_stats.ai_generated_excluded,
        "service_messages_excluded": extraction_stats.service_messages_excluded,
        "media_messages_excluded": extraction_stats.media_messages_excluded,
        "empty_messages_excluded": extraction_stats.empty_messages_excluded,
        "examples_exported": len(cleaned_examples),
        "examples_removed_during_cleaning": cleaning_stats.examples_removed,
        "removal_reasons": cleaning_stats.removal_reasons,
        "redaction_counts": cleaning_stats.redaction_counts,
        "configured_limits": {
            "examples": export_limit,
            "context_messages": context_limit,
        },
        "redact_pii": redact_pii,
        "export_timestamp": datetime.now(UTC).isoformat(),
    }
    _write_json(output_directory / "export_summary.json", summary)
    return summary


def _can_merge(group: _MessageGroup, message: HistoryMessage, role: str) -> bool:
    if group.role != role:
        return False
    if group.last_date is None or message.date is None:
        return True
    return 0 <= (message.date - group.last_date).total_seconds() <= FRAGMENT_GAP_SECONDS


def _message_order_key(message: HistoryMessage) -> tuple[bool, datetime, int]:
    return (
        message.date is None,
        message.date or datetime.min.replace(tzinfo=UTC),
        message.id,
    )


def _example_id(dialog_id: int, message_ids: list[int]) -> str:
    source = f"{dialog_id}:{','.join(str(message_id) for message_id in message_ids)}"
    return hashlib.sha256(source.encode("ascii")).hexdigest()[:24]


def _isoformat(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def _write_jsonl(path: Path, examples: list[TrainingExample]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for example in examples:
            handle.write(json.dumps(example.to_dict(), ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
