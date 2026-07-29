"""Dataset preparation for local Telegram SLM experiments."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DatasetBuildSummary:
    examples: int
    train_examples: int
    test_examples: int
    duplicates_removed: int
    ai_generated_excluded: int
    fingerprint: str
    output_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "examples": self.examples,
            "train_examples": self.train_examples,
            "test_examples": self.test_examples,
            "duplicates_removed": self.duplicates_removed,
            "ai_generated_excluded": self.ai_generated_excluded,
            "fingerprint": self.fingerprint,
            "output_path": self.output_path,
        }


def build_sft_dataset(
    *,
    source_path: Path,
    output_path: Path,
    test_ratio: float = 0.2,
) -> DatasetBuildSummary:
    rows = _load_rows(source_path)
    seen: set[str] = set()
    examples: list[dict[str, Any]] = []
    duplicates = 0
    excluded_ai = 0
    for row in rows:
        if not bool(row.get("is_human_authored", False)):
            excluded_ai += 1
            continue
        target = str(row.get("target_reply", "")).strip()
        context = row.get("context", [])
        if not target or not isinstance(context, list):
            continue
        fingerprint = _stable_hash({"context": context, "target": target})
        if fingerprint in seen:
            duplicates += 1
            continue
        seen.add(fingerprint)
        split = _split_for_dialog(row.get("dialog_id"), test_ratio=test_ratio)
        examples.append(
            {
                "example_id": row.get("example_id", fingerprint[:16]),
                "dialog_id": row.get("dialog_id"),
                "split": split,
                "messages": context,
                "target": target,
                "provenance": {
                    "human_authored": True,
                    "source_message_ids": row.get("source_message_ids", []),
                },
            }
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in examples)
    output_path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")
    fingerprint = _stable_hash(examples)
    return DatasetBuildSummary(
        examples=len(examples),
        train_examples=sum(item["split"] == "train" for item in examples),
        test_examples=sum(item["split"] == "test" for item in examples),
        duplicates_removed=duplicates,
        ai_generated_excluded=excluded_ai,
        fingerprint=fingerprint,
        output_path=str(output_path),
    )


def _load_rows(source_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in source_path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _split_for_dialog(dialog_id: object, *, test_ratio: float) -> str:
    digest = hashlib.sha256(str(dialog_id).encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return "test" if bucket < test_ratio else "train"


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
