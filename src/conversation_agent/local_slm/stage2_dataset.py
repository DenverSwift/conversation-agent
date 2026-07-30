"""Frozen benchmark dataset contracts and training leakage protection."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BENCHMARK_PURPOSE = "benchmark_only"
STAGE2_DATASET_NAME = "local-slm-stage2-v1"


class BenchmarkDatasetError(ValueError):
    """Raised when a frozen benchmark violates its manifest contract."""


class BenchmarkTrainingLeakError(ValueError):
    """Raised when benchmark-only data is passed into a training workflow."""


@dataclass(frozen=True)
class BenchmarkScenario:
    id: str
    category: str
    tags: tuple[str, ...]
    language: str
    agent_profile: str
    relationship: dict[str, Any]
    conversation: tuple[dict[str, Any], ...]
    known_facts: tuple[str, ...]
    goal: str
    expected_actions: tuple[str, ...]
    required_facts: tuple[str, ...]
    forbidden_claims: tuple[str, ...]
    min_bubbles: int
    max_bubbles: int
    max_total_chars: int
    evaluation_notes: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BenchmarkScenario:
        return cls(
            id=str(value["id"]),
            category=str(value["category"]),
            tags=tuple(str(item) for item in value.get("tags", [])),
            language=str(value.get("language", "ru")),
            agent_profile=str(value.get("agent_profile", "informal_manager")),
            relationship=dict(value.get("relationship", {})),
            conversation=tuple(dict(item) for item in value.get("conversation", [])),
            known_facts=tuple(str(item) for item in value.get("known_facts", [])),
            goal=str(value.get("goal", "respond_safely")),
            expected_actions=tuple(str(item) for item in value.get("expected_actions", [])),
            required_facts=tuple(str(item) for item in value.get("required_facts", [])),
            forbidden_claims=tuple(str(item) for item in value.get("forbidden_claims", [])),
            min_bubbles=int(value.get("min_bubbles", 0)),
            max_bubbles=int(value.get("max_bubbles", 4)),
            max_total_chars=int(value.get("max_total_chars", 700)),
            evaluation_notes=str(value.get("evaluation_notes", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "tags": list(self.tags),
            "language": self.language,
            "agent_profile": self.agent_profile,
            "relationship": self.relationship,
            "conversation": list(self.conversation),
            "known_facts": list(self.known_facts),
            "goal": self.goal,
            "expected_actions": list(self.expected_actions),
            "required_facts": list(self.required_facts),
            "forbidden_claims": list(self.forbidden_claims),
            "min_bubbles": self.min_bubbles,
            "max_bubbles": self.max_bubbles,
            "max_total_chars": self.max_total_chars,
            "evaluation_notes": self.evaluation_notes,
        }

    @property
    def all_categories(self) -> set[str]:
        return {self.category, *self.tags}

    @property
    def incoming_messages(self) -> tuple[str, ...]:
        messages: list[str] = []
        for turn in self.conversation:
            if str(turn.get("role", "")) not in {"contact", "user"}:
                continue
            raw = turn.get("messages", [])
            if isinstance(raw, list):
                messages.extend(str(item) for item in raw if str(item).strip())
            elif str(raw).strip():
                messages.append(str(raw))
        return tuple(messages)

    @property
    def flat_conversation(self) -> tuple[dict[str, str], ...]:
        result: list[dict[str, str]] = []
        for turn in self.conversation:
            role = str(turn.get("role", "contact"))
            raw = turn.get("messages", [])
            values = raw if isinstance(raw, list) else [raw]
            for item in values:
                text = str(item).strip()
                if text:
                    result.append({"role": role, "content": text})
        return tuple(result)


@dataclass(frozen=True)
class FrozenBenchmark:
    scenarios: tuple[BenchmarkScenario, ...]
    manifest: dict[str, Any]
    fingerprint: str
    dataset_path: Path


def load_frozen_benchmark(dataset_path: Path) -> FrozenBenchmark:
    rows = load_jsonl(dataset_path)
    scenarios = tuple(BenchmarkScenario.from_dict(item) for item in rows)
    ids = [scenario.id for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise BenchmarkDatasetError("benchmark scenario IDs must be unique")
    manifest_path = dataset_path.parent / "manifest.json"
    if not manifest_path.is_file():
        raise BenchmarkDatasetError(f"benchmark manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fingerprint = benchmark_fingerprint(rows)
    if int(manifest.get("scenario_count", -1)) != len(rows):
        raise BenchmarkDatasetError("manifest scenario_count does not match scenarios.jsonl")
    if manifest.get("fingerprint") != fingerprint:
        raise BenchmarkDatasetError(
            "frozen benchmark fingerprint mismatch; create a new version instead of editing v1"
        )
    if manifest.get("purpose") != BENCHMARK_PURPOSE:
        raise BenchmarkDatasetError("benchmark purpose must be benchmark_only")
    if manifest.get("allowed_for_training") is not False:
        raise BenchmarkDatasetError("benchmark manifest must forbid training")
    return FrozenBenchmark(
        scenarios=scenarios,
        manifest=manifest,
        fingerprint=fingerprint,
        dataset_path=dataset_path,
    )


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise BenchmarkDatasetError(f"{path}:{line_number} is not a JSON object")
        rows.append(value)
    return rows


def benchmark_fingerprint(rows: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> str:
    canonical = json.dumps(
        list(rows),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stable_fingerprint(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def registered_benchmark_fingerprints(repo_root: Path | None = None) -> set[str]:
    root = (repo_root or Path.cwd()).resolve()
    values: set[str] = set()
    for manifest_path in root.glob("benchmarks/*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if manifest.get("purpose") == BENCHMARK_PURPOSE:
            fingerprint = str(manifest.get("fingerprint", "")).strip()
            if fingerprint:
                values.add(fingerprint)
    return values


def assert_training_source_allowed(source_path: Path, *, repo_root: Path | None = None) -> None:
    manifest_path = source_path.parent / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("purpose") == BENCHMARK_PURPOSE
            or manifest.get("allowed_for_training") is False
        ):
            raise BenchmarkTrainingLeakError(
                f"training is forbidden for benchmark-only dataset: {source_path}"
            )
    try:
        rows = load_jsonl(source_path)
    except (OSError, json.JSONDecodeError, BenchmarkDatasetError):
        return
    fingerprint = benchmark_fingerprint(rows)
    if fingerprint in registered_benchmark_fingerprints(repo_root):
        raise BenchmarkTrainingLeakError(
            f"training is forbidden for registered benchmark fingerprint: {fingerprint}"
        )


def coverage_summary(scenarios: tuple[BenchmarkScenario, ...]) -> dict[str, Any]:
    actions = ("reply", "no_reply", "reaction", "handoff")
    return {
        "scenario_count": len(scenarios),
        "unique_ids": len({scenario.id for scenario in scenarios}),
        "categories": sorted(
            {category for scenario in scenarios for category in scenario.all_categories}
        ),
        "action_coverage": {
            action: sum(action in scenario.expected_actions for scenario in scenarios)
            for action in actions
        },
        "hallucination_risk": sum(
            "hallucination_risk" in scenario.all_categories for scenario in scenarios
        ),
        "relationship_profiles": sum(bool(scenario.relationship) for scenario in scenarios),
        "multi_message_bursts": sum(
            len(scenario.incoming_messages) >= 2 for scenario in scenarios
        ),
        "conflict_or_emotional": sum(
            bool(
                scenario.all_categories
                & {"conflict", "irritation", "emotional_support"}
            )
            for scenario in scenarios
        ),
    }


def import_private_benchmark(
    *,
    input_path: Path,
    output_dir: Path,
    anonymize: bool,
    purpose: str,
    confirm_save_source: bool = False,
) -> dict[str, Any]:
    resolved_output = output_dir.resolve()
    runtime_root = (Path.cwd() / ".runtime").resolve()
    if not resolved_output.is_relative_to(runtime_root):
        raise BenchmarkDatasetError("private benchmark output must stay under .runtime")
    if purpose != BENCHMARK_PURPOSE:
        raise BenchmarkDatasetError("private imports must use purpose=benchmark_only")
    if not anonymize and not confirm_save_source:
        raise BenchmarkDatasetError(
            "saving source text requires --confirm-save-source or use --anonymize"
        )

    rows = load_jsonl(input_path)
    normalized = [_anonymize_value(row) if anonymize else row for row in rows]
    if anonymize:
        leaked = [item for item in normalized if _contains_obvious_pii(item)]
        if leaked:
            raise BenchmarkDatasetError("PII check failed after anonymization")
    fingerprint = benchmark_fingerprint(normalized)
    manifest = {
        "name": f"private-{fingerprint[:12]}",
        "purpose": BENCHMARK_PURPOSE,
        "version": 1,
        "scenario_count": len(normalized),
        "language": "ru",
        "created_from_private_data": True,
        "allowed_for_training": False,
        "fingerprint": fingerprint,
        "source_text_saved": not anonymize,
    }
    resolved_output.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        resolved_output / "scenarios.jsonl",
        "".join(
            json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
            for item in normalized
        ),
    )
    atomic_write_json(resolved_output / "manifest.json", manifest)
    return {"output": str(resolved_output), **manifest}


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def _anonymize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _anonymize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_anonymize_value(item) for item in value]
    if not isinstance(value, str):
        return value
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[EMAIL]", value)
    text = re.sub(r"(?<!\d)(?:\+?\d[\d ()-]{7,}\d)(?!\d)", "[PHONE]", text)
    text = re.sub(r"(?i)(?:telegram|tg|телеграм)[:\s]+@[A-Za-z0-9_]{5,}", "[HANDLE]", text)
    return text


def _contains_obvious_pii(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False)
    return bool(
        re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
        or re.search(r"(?<!\d)(?:\+?\d[\d ()-]{7,}\d)(?!\d)", text)
    )
