"""Data contracts for compiled style artifacts and runtime evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StyleExample:
    example_id: str
    contact_id: int
    incoming_text: str
    response_text: str
    source_type: str
    polarity: str
    created_at: str = ""
    feedback_id: int | None = None
    context: tuple[dict[str, str], ...] = ()
    provenance: str = ""
    feedback_status: str = ""
    feedback_category: str = ""
    source_key: str = ""
    content_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "example_id": self.example_id,
            "contact_id": self.contact_id,
            "incoming_text": self.incoming_text,
            "response_text": self.response_text,
            "source_type": self.source_type,
            "polarity": self.polarity,
            "created_at": self.created_at,
            "feedback_id": self.feedback_id,
            "context": list(self.context),
            "provenance": self.provenance,
            "feedback_status": self.feedback_status,
            "feedback_category": self.feedback_category,
            "source_key": self.source_key,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> StyleExample:
        return cls(
            example_id=str(value["example_id"]),
            contact_id=int(value["contact_id"]),
            incoming_text=str(value["incoming_text"]),
            response_text=str(value["response_text"]),
            source_type=str(value["source_type"]),
            polarity=str(value["polarity"]),
            created_at=str(value.get("created_at", "")),
            feedback_id=(
                int(value["feedback_id"]) if value.get("feedback_id") is not None else None
            ),
            context=tuple(
                {
                    "role": str(item.get("role", "")),
                    "text": str(item.get("text", "")),
                    "provenance": str(item.get("provenance", "")),
                }
                for item in value.get("context", [])
                if isinstance(item, dict)
            ),
            provenance=str(value.get("provenance", "")),
            feedback_status=str(value.get("feedback_status", "")),
            feedback_category=str(value.get("feedback_category", "")),
            source_key=str(value.get("source_key", "")),
            content_hash=str(value.get("content_hash", "")),
        )


@dataclass(frozen=True)
class StyleRule:
    text: str
    confidence: float
    evidence_count: int
    source_type: str
    applicable_context: str
    scope: str = "global"
    observation_id: str = ""
    behavior_category: str = "general"
    supporting_source_keys: tuple[str, ...] = ()
    supporting_source_hashes: tuple[str, ...] = ()
    polarity: str = "positive"
    source_priority: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "confidence": self.confidence,
            "evidence_count": self.evidence_count,
            "source_type": self.source_type,
            "applicable_context": self.applicable_context,
            "scope": self.scope,
            "observation_id": self.observation_id,
            "behavior_category": self.behavior_category,
            "supporting_source_keys": list(self.supporting_source_keys),
            "supporting_source_hashes": list(self.supporting_source_hashes),
            "polarity": self.polarity,
            "source_priority": self.source_priority,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> StyleRule:
        return cls(
            text=str(value["text"]),
            confidence=float(value.get("confidence", 0)),
            evidence_count=int(value.get("evidence_count", 0)),
            source_type=str(value.get("source_type", "mixed")),
            applicable_context=str(value.get("applicable_context", "general")),
            scope=str(value.get("scope", "global")),
            observation_id=str(value.get("observation_id", "")),
            behavior_category=str(value.get("behavior_category", "general")),
            supporting_source_keys=tuple(
                str(item) for item in value.get("supporting_source_keys", [])
            ),
            supporting_source_hashes=tuple(
                str(item) for item in value.get("supporting_source_hashes", [])
            ),
            polarity=str(value.get("polarity", "positive")),
            source_priority=int(value.get("source_priority", 0)),
        )


@dataclass(frozen=True)
class StyleBundle:
    rules_markdown: str
    rules: tuple[StyleRule, ...]
    examples: tuple[StyleExample, ...]
    contact_profiles: dict[int, dict[str, Any]]
    built_at: str
    source_example_count: int
    batch_count: int


@dataclass(frozen=True)
class SelectedEvidence:
    example: StyleExample
    score: float


@dataclass(frozen=True)
class ComposedPrompt:
    instructions: str
    messages: list[dict[str, str]]
    candidate_count: int
    selected_count: int
    selected_fix_count: int
    provenance_counts: dict[str, int]
    estimated_chars: int
