"""Compile a complete local style corpus into persistent runtime artifacts."""

from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from conversation_agent.storage.models import GeneratedReplyRecord
from conversation_agent.style.models import StyleExample, StyleRule


class StyleAnalyzer(Protocol):
    async def analyze_batch(
        self,
        examples: Sequence[StyleExample],
        *,
        batch_number: int,
    ) -> list[StyleRule]:
        """Extract observable rules from one complete batch."""
        ...

    async def merge_rules(self, rules: Sequence[StyleRule]) -> list[StyleRule]:
        """Merge all batch observations into a compact rulebook."""
        ...


async def build_style_bundle(
    *,
    source_path: Path,
    output_directory: Path,
    contact_id: int,
    source_limit: int,
    analyzer: StyleAnalyzer,
    analysis_model: str,
    feedback_records: Sequence[GeneratedReplyRecord] = (),
    batch_size: int = 25,
    verbose: bool = False,
) -> dict[str, Any]:
    if source_limit <= 0 or batch_size <= 0:
        raise ValueError("Style source limit and batch size must be positive")
    human_examples = load_human_examples(
        source_path,
        contact_id=contact_id,
        limit=source_limit,
    )
    feedback_examples, approved_ai_count = examples_from_feedback(
        feedback_records,
        contact_id=contact_id,
    )
    source_examples = [*feedback_examples, *human_examples]
    if not source_examples:
        raise ValueError(
            "No qualifying style examples found. Run scripts\\export_training_data.bat first."
        )
    bank_examples = _deduplicate(source_examples)

    observations: list[StyleRule] = []
    batches = [
        source_examples[index : index + batch_size]
        for index in range(0, len(source_examples), batch_size)
    ]
    if verbose:
        print(f"      Найдено примеров для анализа: {len(source_examples)} (Батчей: {len(batches)})", file=sys.stderr)
    for batch_number, batch in enumerate(batches, start=1):
        if verbose:
            print(f"      [Батч {batch_number}/{len(batches)}] Анализ {len(batch)} примеров стиля через OpenAI...", file=sys.stderr)
        batch_rules = await analyzer.analyze_batch(batch, batch_number=batch_number)
        if not batch_rules:
            raise ValueError(f"Style analysis batch {batch_number} returned no rules")
        observations.extend(batch_rules)
    if verbose:
        print("      Объединение и систематизация правил стиля...", file=sys.stderr)
    merged_rules = await analyzer.merge_rules(observations)
    if not merged_rules:
        raise ValueError("Style rule merge returned no rules")

    built_at = datetime.now(UTC).isoformat()
    summary: dict[str, Any] = {
        "built_at": built_at,
        "source_example_count": len(source_examples),
        "example_bank_count": len(bank_examples),
        "human_example_count": len(human_examples),
        "fix_example_count": sum(item.source_type == "fix" for item in source_examples),
        "negative_example_count": sum(
            item.polarity == "negative" for item in source_examples
        ),
        "approved_ai_evaluation_count": approved_ai_count,
        "batch_count": len(batches),
        "behavior_rule_count": len(merged_rules),
        "analysis_model": analysis_model,
        "contact_id": contact_id,
    }
    profile = {
        "version": "AA.1",
        "built_at": built_at,
        "rules": [rule.to_dict() for rule in merged_rules],
    }
    contact_rules = [rule.to_dict() for rule in merged_rules if rule.scope == "contact"]
    contact_profile = {
        "contact_id": contact_id,
        "built_at": built_at,
        "rules": contact_rules,
        "evidence_count": sum(
            item.contact_id == contact_id for item in source_examples
        ),
        "observed_statistics": _contact_statistics(source_examples, contact_id),
    }
    _write_complete_bundle(
        output_directory,
        rules=merged_rules,
        profile=profile,
        contact_profile=contact_profile,
        examples=bank_examples,
        summary=summary,
        contact_id=contact_id,
    )
    return summary


def load_human_examples(
    source_path: Path,
    *,
    contact_id: int,
    limit: int,
) -> list[StyleExample]:
    if not source_path.is_file():
        raise ValueError(
            f"Style source export not found: {source_path}. "
            "Run scripts\\export_training_data.bat first."
        )
    examples: list[StyleExample] = []
    for line_number, line in enumerate(
        source_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at line {line_number}") from exc
        if not isinstance(value, dict) or not bool(value.get("is_human_authored")):
            continue
        incoming = _last_user_text(value.get("context"))
        response = str(value.get("target_reply", "")).strip()
        if not incoming or not response:
            continue
        examples.append(
            StyleExample(
                example_id=str(value.get("example_id", f"human-{line_number}")),
                contact_id=int(value.get("dialog_id", contact_id)),
                incoming_text=incoming,
                response_text=response,
                source_type="human_matvey",
                polarity="positive",
                created_at=str(value.get("created_at", "")),
            )
        )
        if len(examples) >= limit:
            break
    return examples


def examples_from_feedback(
    records: Sequence[GeneratedReplyRecord],
    *,
    contact_id: int,
) -> tuple[list[StyleExample], int]:
    examples: list[StyleExample] = []
    approved_ai_count = 0
    for record in records:
        if record.dialog_id != contact_id:
            continue
        incoming = record.incoming_message_text or _last_user_text_json(record.context_json)
        if not incoming:
            continue
        if record.feedback_status == "corrected" and record.corrected_reply_text:
            examples.append(
                StyleExample(
                    example_id=f"fix-{record.id}",
                    contact_id=contact_id,
                    incoming_text=incoming,
                    response_text=record.corrected_reply_text,
                    source_type="fix",
                    polarity="positive",
                    created_at=record.feedback_updated_at or record.created_at,
                    feedback_id=record.id,
                )
            )
        elif record.feedback_status == "rejected":
            examples.append(
                StyleExample(
                    example_id=f"negative-{record.id}",
                    contact_id=contact_id,
                    incoming_text=incoming,
                    response_text=record.generated_reply_text,
                    source_type=(
                        "should_not_reply"
                        if record.feedback_category == "should_not_reply"
                        else "rejected"
                    ),
                    polarity="negative",
                    created_at=record.feedback_updated_at or record.created_at,
                    feedback_id=record.id,
                )
            )
        elif record.feedback_status == "approved":
            examples.append(
                StyleExample(
                    example_id=f"approved-ai-{record.id}",
                    contact_id=contact_id,
                    incoming_text=incoming,
                    response_text=record.generated_reply_text,
                    source_type="approved_ai",
                    polarity="evaluation",
                    created_at=record.feedback_updated_at or record.created_at,
                    feedback_id=record.id,
                )
            )
            approved_ai_count += 1
    return examples, approved_ai_count


def _write_complete_bundle(
    output_directory: Path,
    *,
    rules: Sequence[StyleRule],
    profile: dict[str, Any],
    contact_profile: dict[str, Any],
    examples: Sequence[StyleExample],
    summary: dict[str, Any],
    contact_id: int,
) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".style-build-",
        dir=output_directory,
    ) as temporary:
        temporary_path = Path(temporary)
        contacts = temporary_path / "contacts"
        contacts.mkdir()
        rules_text = "# Matvey Behavior Rules\n\n" + "\n".join(
            f"- {rule.text}" for rule in rules
        )
        (temporary_path / "matvey_behavior_rules.md").write_text(
            rules_text + "\n",
            encoding="utf-8",
        )
        _write_json(temporary_path / "style_profile.json", profile)
        _write_json(temporary_path / "build_summary.json", summary)
        _write_json(contacts / f"{contact_id}.json", contact_profile)
        with (temporary_path / "example_bank.jsonl").open(
            "w",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            for example in examples:
                handle.write(json.dumps(example.to_dict(), ensure_ascii=False, sort_keys=True))
                handle.write("\n")

        (output_directory / "contacts").mkdir(exist_ok=True)
        for relative in (
            Path("matvey_behavior_rules.md"),
            Path("style_profile.json"),
            Path("example_bank.jsonl"),
            Path("build_summary.json"),
            Path("contacts") / f"{contact_id}.json",
        ):
            source = temporary_path / relative
            destination = output_directory / relative
            source.replace(destination)


def _last_user_text(context: object) -> str:
    if not isinstance(context, list):
        return ""
    for turn in reversed(context):
        if isinstance(turn, dict) and turn.get("role") == "user":
            return str(turn.get("text", "")).strip()
    return ""


def _last_user_text_json(context_json: str) -> str:
    try:
        return _last_user_text(json.loads(context_json))
    except json.JSONDecodeError:
        return ""


def _deduplicate(examples: Sequence[StyleExample]) -> list[StyleExample]:
    seen: set[tuple[str, str, str]] = set()
    result: list[StyleExample] = []
    for example in examples:
        key = (
            " ".join(example.incoming_text.lower().split()),
            " ".join(example.response_text.lower().split()),
            example.polarity,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(example)
    return result


def _contact_statistics(
    examples: Sequence[StyleExample],
    contact_id: int,
) -> dict[str, Any]:
    positive = [
        item
        for item in examples
        if item.contact_id == contact_id and item.polarity == "positive"
    ]
    lengths = [len(item.response_text) for item in positive]
    if not positive:
        return {
            "positive_example_count": 0,
            "average_response_chars": 0,
            "lowercase_start_ratio": 0,
            "final_period_ratio": 0,
            "question_ratio": 0,
            "exclamation_ratio": 0,
            "profanity_ratio": 0,
            "source_counts": {},
        }
    source_counts: dict[str, int] = {}
    for item in positive:
        source_counts[item.source_type] = source_counts.get(item.source_type, 0) + 1
    count = len(positive)
    return {
        "positive_example_count": count,
        "average_response_chars": round(sum(lengths) / count, 2),
        "lowercase_start_ratio": round(
            sum(
                bool(item.response_text)
                and item.response_text[0].isalpha()
                and item.response_text[0].islower()
                for item in positive
            )
            / count,
            3,
        ),
        "final_period_ratio": round(
            sum(item.response_text.rstrip().endswith(".") for item in positive) / count,
            3,
        ),
        "question_ratio": round(
            sum("?" in item.response_text for item in positive) / count,
            3,
        ),
        "exclamation_ratio": round(
            sum("!" in item.response_text for item in positive) / count,
            3,
        ),
        "profanity_ratio": round(
            sum(
                any(
                    marker in item.response_text.lower()
                    for marker in ("хуй", "нахуй", "бля", "fuck", "shit")
                )
                for item in positive
            )
            / count,
            3,
        ),
        "source_counts": source_counts,
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
