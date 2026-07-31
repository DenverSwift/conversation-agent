"""Private human-style dataset prototype with strict provenance gates."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from conversation_agent.local_slm.stage2_dataset import (
    BenchmarkTrainingLeakError,
    registered_benchmark_fingerprints,
    stable_fingerprint,
)
from conversation_agent.local_slm.stage3a_contract import HUMAN_STYLE_SOURCES

SourceType = Literal[
    "human_manual",
    "human_edit",
    "human_fix",
    "model_rejected",
    "model_accepted_unedited",
    "imported_human_verified",
]
SOURCE_TYPES = frozenset(
    {
        "human_manual",
        "human_edit",
        "human_fix",
        "model_rejected",
        "model_accepted_unedited",
        "imported_human_verified",
    }
)
POSITIVE_TARGET_SOURCES = HUMAN_STYLE_SOURCES


class StyleDatasetError(ValueError):
    pass


@dataclass(frozen=True)
class TrainingExample:
    example_id: str
    agent_id: str
    conversation_context: tuple[dict[str, str], ...]
    relationship_context: dict[str, Any]
    semantic_plan: dict[str, Any] | None
    adaptive_style_plan: dict[str, Any]
    human_target_bubbles: tuple[str, ...]
    style_evidence: tuple[dict[str, Any], ...]
    provenance: dict[str, Any]
    timestamp: str
    privacy_status: str
    approval_status: str
    source_type: SourceType
    quality_flags: tuple[str, ...] = ()
    previous_candidate: tuple[str, ...] = ()
    pii_flags: tuple[str, ...] = ()

    @property
    def is_positive_target(self) -> bool:
        return self.source_type in POSITIVE_TARGET_SOURCES

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in (
            "conversation_context",
            "human_target_bubbles",
            "style_evidence",
            "quality_flags",
            "previous_candidate",
            "pii_flags",
        ):
            value[key] = list(value[key])
        value["positive_human_target"] = self.is_positive_target
        return value

    @classmethod
    def from_dict(
        cls,
        value: dict[str, Any],
        *,
        source_type_override: str | None = None,
    ) -> TrainingExample:
        source = source_type_override or str(value.get("source_type", ""))
        if source not in SOURCE_TYPES:
            raise StyleDatasetError(f"invalid source_type: {source}")
        return cls(
            example_id=str(value.get("example_id", "")).strip(),
            agent_id=str(value.get("agent_id", "")).strip(),
            conversation_context=tuple(
                {"role": str(item.get("role", "")), "content": str(item.get("content", ""))}
                for item in value.get("conversation_context", [])
                if isinstance(item, dict)
            ),
            relationship_context=dict(value.get("relationship_context", {})),
            semantic_plan=(
                None
                if value.get("semantic_plan") is None
                else dict(value.get("semantic_plan", {}))
            ),
            adaptive_style_plan=dict(value.get("adaptive_style_plan", {})),
            human_target_bubbles=tuple(
                str(item).strip()
                for item in value.get("human_target_bubbles", [])
                if str(item).strip()
            ),
            style_evidence=tuple(
                dict(item)
                for item in value.get("style_evidence", [])
                if isinstance(item, dict)
            ),
            provenance=dict(value.get("provenance", {})),
            timestamp=str(value.get("timestamp") or datetime.now(UTC).isoformat()),
            privacy_status=str(value.get("privacy_status", "pending")),
            approval_status=str(value.get("approval_status", "pending")),
            source_type=source,  # type: ignore[arg-type]
            quality_flags=tuple(str(item) for item in value.get("quality_flags", [])),
            previous_candidate=tuple(
                str(item) for item in value.get("previous_candidate", [])
            ),
            pii_flags=tuple(str(item) for item in value.get("pii_flags", [])),
        )


@dataclass(frozen=True)
class DatasetValidation:
    valid: bool
    examples: int
    positive_targets: int
    rejected_examples: int
    duplicates: tuple[str, ...]
    errors: tuple[str, ...]
    fingerprint: str
    pii_flag_counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "duplicates": list(self.duplicates),
            "errors": list(self.errors),
        }


def init_style_dataset(root: Path) -> dict[str, Any]:
    for name in ("raw", "curated", "rejected", "manifests"):
        directory = root / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / ".gitkeep").touch(exist_ok=True)
    return {"dataset": str(root), "initialized": True}


def add_style_examples(
    *,
    root: Path,
    input_path: Path,
    source_type: str,
) -> dict[str, Any]:
    if source_type not in SOURCE_TYPES:
        raise StyleDatasetError(f"invalid source_type: {source_type}")
    rows = _load_rows(input_path)
    added: list[str] = []
    destination = "rejected" if source_type == "model_rejected" else "raw"
    for value in rows:
        example = TrainingExample.from_dict(
            value,
            source_type_override=source_type,
        )
        errors = validate_example(example)
        if errors:
            raise StyleDatasetError(
                f"{example.example_id or '<missing>'}: {', '.join(errors)}"
            )
        payload = example.to_dict()
        identifier = example.example_id or stable_fingerprint(payload)[:16]
        target = root / destination / f"{identifier}.json"
        if target.exists():
            raise StyleDatasetError(f"duplicate example_id: {identifier}")
        _atomic_json(target, payload)
        added.append(identifier)
    return {"added": len(added), "example_ids": added, "destination": destination}


def validate_style_dataset(root: Path) -> DatasetValidation:
    examples = load_style_examples(root)
    errors: list[str] = []
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    pii = {}
    for example in examples:
        errors.extend(
            f"{example.example_id}:{error}" for error in validate_example(example)
        )
        fingerprint = stable_fingerprint(
            {
                "context": example.conversation_context,
                "target": example.human_target_bubbles,
            }
        )
        if fingerprint in seen:
            duplicates.append(example.example_id)
        else:
            seen[fingerprint] = example.example_id
        for flag in example.pii_flags:
            pii[flag] = pii.get(flag, 0) + 1
    errors.extend(f"{item}:duplicate_content" for item in duplicates)
    fingerprint = stable_fingerprint([item.to_dict() for item in examples])
    return DatasetValidation(
        valid=not errors,
        examples=len(examples),
        positive_targets=sum(item.is_positive_target for item in examples),
        rejected_examples=sum(item.source_type == "model_rejected" for item in examples),
        duplicates=tuple(duplicates),
        errors=tuple(errors),
        fingerprint=fingerprint,
        pii_flag_counts=pii,
    )


def build_style_dataset(*, root: Path, output: Path) -> dict[str, Any]:
    validation = validate_style_dataset(root)
    if not validation.valid:
        raise StyleDatasetError("dataset validation failed: " + ", ".join(validation.errors))
    eligible = [
        item
        for item in load_style_examples(root)
        if item.is_positive_target
        and item.approval_status == "approved"
        and item.privacy_status in {"approved", "cleared"}
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(
        json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True)
        for item in eligible
    )
    output.write_text(payload + ("\n" if payload else ""), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "examples": len(eligible),
        "dataset_fingerprint": stable_fingerprint(
            [item.to_dict() for item in eligible]
        ),
        "source_fingerprint": validation.fingerprint,
        "benchmark_data_allowed": False,
        "positive_source_types": sorted(POSITIVE_TARGET_SOURCES),
        "local_only": True,
    }
    _atomic_json(root / "manifests" / f"{manifest['dataset_fingerprint']}.json", manifest)
    return {**manifest, "output": str(output)}


def style_dataset_stats(root: Path) -> dict[str, Any]:
    examples = load_style_examples(root)
    validation = validate_style_dataset(root)
    return {
        **validation.to_dict(),
        "source_types": dict(Counter(item.source_type for item in examples)),
        "approval_statuses": dict(Counter(item.approval_status for item in examples)),
        "privacy_statuses": dict(Counter(item.privacy_status for item in examples)),
    }


def inspect_style_dataset(root: Path, *, limit: int = 30) -> dict[str, Any]:
    examples = load_style_examples(root)
    return {
        "examples": [
            {
                "example_id": item.example_id,
                "agent_id": item.agent_id,
                "source_type": item.source_type,
                "positive_human_target": item.is_positive_target,
                "approval_status": item.approval_status,
                "privacy_status": item.privacy_status,
                "target_bubbles": list(item.human_target_bubbles),
                "quality_flags": list(item.quality_flags),
            }
            for item in examples[: max(0, limit)]
        ],
        "total": len(examples),
    }


def validate_example(example: TrainingExample) -> tuple[str, ...]:
    errors: list[str] = []
    if not example.example_id:
        errors.append("missing_example_id")
    if not example.agent_id:
        errors.append("missing_agent_id")
    if not example.conversation_context:
        errors.append("missing_context")
    if not example.provenance:
        errors.append("missing_provenance")
    benchmark_fingerprint = str(example.provenance.get("benchmark_fingerprint", ""))
    if (
        example.provenance.get("purpose") == "benchmark_only"
        or benchmark_fingerprint in registered_benchmark_fingerprints()
    ):
        errors.append("benchmark_training_forbidden")
    if example.is_positive_target:
        if not example.human_target_bubbles:
            errors.append("empty_human_target")
        if not example.style_evidence:
            errors.append("missing_style_evidence")
        if str(example.provenance.get("origin", "")) != "human":
            errors.append("positive_target_not_human")
    elif example.source_type == "model_accepted_unedited":
        if example.human_target_bubbles:
            errors.append("accepted_ai_cannot_be_human_target")
    text = json.dumps(example.to_dict(), ensure_ascii=False)
    if _contains_credentials(text):
        errors.append("credential_leak")
    return tuple(errors)


def load_style_examples(root: Path) -> list[TrainingExample]:
    examples = []
    for directory in (root / "raw", root / "rejected"):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(value, dict):
                examples.append(TrainingExample.from_dict(value))
    return examples


@dataclass(frozen=True)
class StyleFeedbackEvent:
    event_id: str
    event_type: Literal[
        "human_manual_reply",
        "human_edit",
        "human_fix",
        "human_rejected",
        "ai_accepted_unchanged",
    ]
    agent_id: str
    final_bubbles: tuple[str, ...]
    ai_draft: tuple[str, ...] = ()
    context: tuple[dict[str, str], ...] = ()
    relationship_context: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class InMemoryStyleFeedbackRepository:
    def __init__(self) -> None:
        self.events: list[StyleFeedbackEvent] = []

    def save(self, event: StyleFeedbackEvent) -> None:
        self.events.append(event)

    def training_candidate(self, event: StyleFeedbackEvent) -> dict[str, Any]:
        mapping = {
            "human_manual_reply": ("human_manual", True, 0),
            "human_edit": ("human_edit", True, 1),
            "human_fix": ("human_fix", True, 2),
            "human_rejected": ("model_rejected", False, 0),
            "ai_accepted_unchanged": ("model_accepted_unedited", False, 0),
        }
        source_type, positive, priority = mapping[event.event_type]
        return {
            "source_type": source_type,
            "positive_human_target": positive,
            "style_evidence": positive,
            "priority": priority,
            "target_bubbles": list(event.final_bubbles) if positive else [],
            "previous_candidate": list(event.ai_draft),
        }


def assert_not_benchmark_training(example: TrainingExample) -> None:
    if "benchmark_training_forbidden" in validate_example(example):
        raise BenchmarkTrainingLeakError("benchmark data cannot be a style target")


def dataset_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Private human style training example",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "example_id",
            "agent_id",
            "conversation_context",
            "relationship_context",
            "semantic_plan",
            "adaptive_style_plan",
            "human_target_bubbles",
            "style_evidence",
            "provenance",
            "timestamp",
            "privacy_status",
            "approval_status",
            "source_type",
            "quality_flags",
        ],
        "properties": {
            "example_id": {"type": "string", "minLength": 1},
            "agent_id": {"type": "string", "minLength": 1},
            "conversation_context": {"type": "array", "minItems": 1},
            "relationship_context": {"type": "object"},
            "semantic_plan": {"type": ["object", "null"]},
            "adaptive_style_plan": {"type": "object"},
            "human_target_bubbles": {"type": "array", "items": {"type": "string"}},
            "style_evidence": {"type": "array", "items": {"type": "object"}},
            "provenance": {"type": "object"},
            "timestamp": {"type": "string"},
            "privacy_status": {"type": "string"},
            "approval_status": {"type": "string"},
            "source_type": {"enum": sorted(SOURCE_TYPES)},
            "quality_flags": {"type": "array", "items": {"type": "string"}},
            "previous_candidate": {"type": "array", "items": {"type": "string"}},
            "pii_flags": {"type": "array", "items": {"type": "string"}},
        },
    }


def _load_rows(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.casefold() == ".jsonl":
        values = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        raw = json.loads(text)
        values = raw if isinstance(raw, list) else [raw]
    if not all(isinstance(item, dict) for item in values):
        raise StyleDatasetError("input must contain JSON objects")
    return values


def _contains_credentials(text: str) -> bool:
    return bool(
        re.search(
            r"(?i)(?:sk-[a-z0-9_-]{16,}|[0-9]{8,}:[A-Za-z0-9_-]{20,}|"
            r"(?:password|пароль|api[_ -]?key|token)\s*[:=]\s*\S+)",
            text,
        )
    )


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
