"""Models used by provider-independent Telegram and feedback exports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class HistoryMessage:
    id: int
    sender_id: int | None
    text: str
    date: datetime | None
    outgoing: bool
    is_service: bool = False
    has_media: bool = False
    is_forwarded: bool = False


@dataclass(frozen=True)
class ContextTurn:
    role: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "text": self.text}


@dataclass(frozen=True)
class TrainingExample:
    example_id: str
    dialog_id: int
    context: tuple[ContextTurn, ...]
    target_reply: str
    source_message_ids: tuple[int, ...]
    created_at: str
    is_human_authored: bool
    target_is_forwarded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "example_id": self.example_id,
            "dialog_id": self.dialog_id,
            "context": [turn.to_dict() for turn in self.context],
            "target_reply": self.target_reply,
            "source_message_ids": list(self.source_message_ids),
            "created_at": self.created_at,
            "is_human_authored": self.is_human_authored,
        }


@dataclass
class ExtractionStats:
    messages_scanned: int = 0
    human_target_replies: int = 0
    ai_generated_excluded: int = 0
    service_messages_excluded: int = 0
    media_messages_excluded: int = 0
    empty_messages_excluded: int = 0


@dataclass
class CleaningStats:
    examples_removed: int = 0
    removal_reasons: dict[str, int] = field(default_factory=dict)
    redaction_counts: dict[str, int] = field(
        default_factory=lambda: {"email": 0, "phone": 0, "url": 0, "secret": 0}
    )

    def removed(self, reason: str) -> None:
        self.examples_removed += 1
        self.removal_reasons[reason] = self.removal_reasons.get(reason, 0) + 1
