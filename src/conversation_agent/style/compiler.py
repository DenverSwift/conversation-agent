"""Incrementally compile private style evidence into runtime artifacts."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from conversation_agent.storage.models import GeneratedReplyRecord
from conversation_agent.style.canonical import (
    NORMALIZATION_VERSION,
    canonical_json,
    canonicalize_example,
)
from conversation_agent.style.compiler_state import (
    STATE_SCHEMA_VERSION,
    CachedSource,
    CompilerState,
    load_compiler_state,
    update_last_build_mode,
    write_compiler_state,
)
from conversation_agent.style.models import StyleExample, StyleRule
from conversation_agent.style.openai_analyzer import (
    ANALYZER_PROMPT_TEMPLATE,
    ANALYZER_PROMPT_VERSION,
)

COMPILER_VERSION = "2"
OBSERVATION_SCHEMA_VERSION = "2"
BUNDLE_VERSION = "AA.2"


class StyleAnalyzer(Protocol):
    async def analyze_batch(
        self,
        examples: Sequence[StyleExample],
        *,
        batch_number: int,
    ) -> list[StyleRule]:
        """Extract structured observations from one delta batch."""
        ...


@dataclass(frozen=True)
class BuildPlan:
    examples: tuple[StyleExample, ...]
    current: dict[str, StyleExample]
    state: CompilerState | None
    fingerprint: str
    unchanged: tuple[str, ...]
    new: tuple[str, ...]
    modified: tuple[str, ...]
    deleted: tuple[str, ...]
    hashes_to_analyze: tuple[str, ...]
    duplicate_reuse: int
    invalid_sources: int
    full_rebuild: bool

    @property
    def requires_changes(self) -> bool:
        return bool(self.new or self.modified or self.deleted or self.full_rebuild)


async def build_style_bundle(
    *,
    source_path: Path,
    output_directory: Path,
    contact_id: int,
    source_limit: int,
    analyzer: StyleAnalyzer | None,
    analysis_model: str,
    feedback_records: Sequence[GeneratedReplyRecord] = (),
    batch_size: int = 50,
    state_path: Path | None = None,
    incremental: bool = True,
    full_rebuild: bool = False,
    dry_run: bool = False,
    force_resynthesize: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    if source_limit <= 0 or batch_size <= 0:
        raise ValueError("Style source limit and batch size must be positive")
    state_path = state_path or output_directory / "compiler_state.sqlite3"
    if not incremental and not full_rebuild:
        raise ValueError(
            "Incremental style compilation is disabled. "
            "Use --full-rebuild to explicitly analyze the corpus."
        )

    plan = plan_style_build(
        source_path=source_path,
        contact_id=contact_id,
        source_limit=source_limit,
        feedback_records=feedback_records,
        analysis_model=analysis_model,
        state_path=state_path,
        batch_size=batch_size,
        full_rebuild=full_rebuild,
    )
    planned_summary = _plan_summary(plan, batch_size=batch_size)
    if dry_run:
        return planned_summary
    if force_resynthesize and (plan.new or plan.modified or plan.deleted):
        raise ValueError(
            "--force-resynthesize requires source data to match the successful state"
        )
    if not plan.requires_changes and not force_resynthesize:
        update_last_build_mode(state_path, "no_op")
        return _no_op_summary(plan)
    if plan.hashes_to_analyze and analyzer is None:
        raise ValueError("A style analyzer is required for new or modified evidence")

    started_at = datetime.now(UTC).isoformat()
    build_id = uuid.uuid4().hex
    new_templates: dict[str, tuple[StyleRule, ...]] = {}
    representatives = _representatives(plan.examples, plan.hashes_to_analyze)
    batches = [
        representatives[index : index + batch_size]
        for index in range(0, len(representatives), batch_size)
    ]
    for batch_number, batch in enumerate(batches, start=1):
        assert analyzer is not None
        if verbose:
            print(
                f"Analyzing incremental style batch {batch_number}/{len(batches)} "
                f"({len(batch)} unique sources)...",
            )
        observations = await analyzer.analyze_batch(batch, batch_number=batch_number)
        if not observations:
            raise ValueError(f"Style analysis batch {batch_number} returned no rules")
        for example in batch:
            relevant = [
                item
                for item in observations
                if not item.supporting_source_hashes
                or example.content_hash in item.supporting_source_hashes
            ]
            if not relevant:
                raise ValueError(
                    f"Style analysis returned no observations for source hash "
                    f"{example.content_hash[:12]}"
                )
            new_templates[example.content_hash] = tuple(
                _bind_rule(item, source_key="", example=example) for item in relevant
            )

    compiled_at = datetime.now(UTC).isoformat()
    old_cache = {} if full_rebuild or plan.state is None else plan.state.content_cache
    content_cache = {**old_cache, **new_templates}
    for example in plan.examples:
        if example.polarity == "evaluation":
            content_cache.setdefault(example.content_hash, ())
    sources: dict[str, CachedSource] = {}
    for source_key, example in sorted(plan.current.items()):
        template = content_cache.get(example.content_hash)
        if template is None:
            raise ValueError(
                f"No compatible cached analysis for source hash {example.content_hash[:12]}"
            )
        observations = tuple(
            _bind_rule(item, source_key=source_key, example=example) for item in template
        )
        sources[source_key] = CachedSource(
            example=example,
            observations=observations,
            compiled_at=compiled_at,
            bundle_id=build_id,
        )

    merged_rules = merge_observations(
        rule
        for cached in sources.values()
        if cached.example.polarity != "evaluation"
        for rule in cached.observations
    )
    if not merged_rules:
        raise ValueError("Style compilation produced no supported behavior rules")

    completed_at = datetime.now(UTC).isoformat()
    previous_bundle_id = (
        plan.state.metadata.get("last_successful_bundle_id", "")
        if plan.state is not None
        else ""
    )
    mode = "full" if full_rebuild or plan.state is None else "incremental"
    summary = _build_summary(
        plan,
        build_id=build_id,
        build_mode=mode,
        started_at=started_at,
        completed_at=completed_at,
        previous_bundle_id=previous_bundle_id,
        batch_count=len(batches),
        analyzer=analyzer,
        analysis_model=analysis_model,
    )
    metadata = {
        "state_schema_version": STATE_SCHEMA_VERSION,
        "compiler_implementation_version": COMPILER_VERSION,
        "analyzer_prompt_version": ANALYZER_PROMPT_VERSION,
        "analyzer_model": analysis_model,
        "normalization_version": NORMALIZATION_VERSION,
        "observation_schema_version": OBSERVATION_SCHEMA_VERSION,
        "analysis_fingerprint": plan.fingerprint,
        "last_successful_bundle_id": build_id,
        "last_successful_build": completed_at,
        "last_build_mode": mode,
        "bundle_version": BUNDLE_VERSION,
    }
    _publish_successful_build(
        output_directory=output_directory,
        state_path=state_path,
        contact_id=contact_id,
        examples=plan.examples,
        sources=sources,
        content_cache=content_cache,
        rules=merged_rules,
        summary=summary,
        metadata=metadata,
    )
    return summary


def plan_style_build(
    *,
    source_path: Path,
    contact_id: int,
    source_limit: int,
    feedback_records: Sequence[GeneratedReplyRecord],
    analysis_model: str,
    state_path: Path,
    batch_size: int,
    full_rebuild: bool = False,
) -> BuildPlan:
    examples, invalid_sources = scan_source_examples(
        source_path,
        contact_id=contact_id,
        limit=source_limit,
        feedback_records=feedback_records,
    )
    if not examples:
        raise ValueError(
            "No qualifying style examples found. Run scripts\\export_training_data.bat first."
        )
    current = {item.source_key: item for item in examples}
    try:
        state = load_compiler_state(state_path)
    except ValueError:
        if not full_rebuild:
            raise
        state = None
    fingerprint = analysis_fingerprint(
        analysis_model=analysis_model,
        batch_size=batch_size,
    )
    if state is not None and not full_rebuild:
        old_fingerprint = state.metadata.get("analysis_fingerprint")
        if old_fingerprint != fingerprint:
            raise ValueError(
                "Style analyzer configuration changed. Run build_style_bundle "
                "with --full-rebuild to explicitly reanalyze all sources."
            )

    old_sources = state.sources if state is not None else {}
    unchanged = tuple(
        key
        for key, example in current.items()
        if key in old_sources
        and old_sources[key].example.content_hash == example.content_hash
        and not full_rebuild
    )
    modified = tuple(
        key
        for key, example in current.items()
        if key in old_sources
        and old_sources[key].example.content_hash != example.content_hash
        and not full_rebuild
    )
    new = tuple(
        key for key in current if key not in old_sources or full_rebuild
    )
    deleted = tuple(
        key for key in old_sources if key not in current and not full_rebuild
    )
    reusable_hashes = (
        set() if full_rebuild or state is None else set(state.content_cache)
    )
    pending = [current[key] for key in (*new, *modified)]
    hashes_to_analyze: list[str] = []
    seen_hashes: set[str] = set()
    duplicate_reuse = 0
    for example in pending:
        if example.polarity == "evaluation":
            if example.content_hash in reusable_hashes or example.content_hash in seen_hashes:
                duplicate_reuse += 1
            seen_hashes.add(example.content_hash)
            continue
        if example.content_hash in reusable_hashes or example.content_hash in seen_hashes:
            duplicate_reuse += 1
            continue
        seen_hashes.add(example.content_hash)
        hashes_to_analyze.append(example.content_hash)
    return BuildPlan(
        examples=examples,
        current=current,
        state=state,
        fingerprint=fingerprint,
        unchanged=unchanged,
        new=new,
        modified=modified,
        deleted=deleted,
        hashes_to_analyze=tuple(hashes_to_analyze),
        duplicate_reuse=duplicate_reuse,
        invalid_sources=invalid_sources,
        full_rebuild=full_rebuild,
    )


def scan_source_examples(
    source_path: Path,
    *,
    contact_id: int,
    limit: int,
    feedback_records: Sequence[GeneratedReplyRecord] = (),
) -> tuple[tuple[StyleExample, ...], int]:
    human_examples, invalid = _load_human_examples_with_stats(
        source_path,
        contact_id=contact_id,
        limit=limit,
    )
    feedback_examples, _ = examples_from_feedback(
        feedback_records,
        contact_id=contact_id,
    )
    canonical = [canonicalize_example(item) for item in (*feedback_examples, *human_examples)]
    unique: dict[str, StyleExample] = {}
    for example in canonical:
        existing = unique.get(example.source_key)
        if existing is not None:
            if existing.content_hash != example.content_hash:
                invalid += 1
            continue
        unique[example.source_key] = example
    return tuple(unique.values()), invalid


def load_human_examples(
    source_path: Path,
    *,
    contact_id: int,
    limit: int,
) -> list[StyleExample]:
    examples, _ = _load_human_examples_with_stats(
        source_path,
        contact_id=contact_id,
        limit=limit,
    )
    return list(examples)


def _load_human_examples_with_stats(
    source_path: Path,
    *,
    contact_id: int,
    limit: int,
) -> tuple[tuple[StyleExample, ...], int]:
    if not source_path.is_file():
        raise ValueError(
            f"Style source export not found: {source_path}. "
            "Run scripts\\export_training_data.bat first."
        )
    examples: list[StyleExample] = []
    invalid = 0
    for line_number, line in enumerate(
        source_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            invalid += 1
            continue
        if not isinstance(value, dict) or not bool(value.get("is_human_authored")):
            invalid += 1
            continue
        context = _normalize_context(value.get("context"))
        incoming = _last_user_text(context)
        response = str(value.get("target_reply", "")).strip()
        if not incoming or not response:
            invalid += 1
            continue
        source_ids = value.get("source_message_ids")
        source_identity = (
            ",".join(str(item) for item in source_ids)
            if isinstance(source_ids, list) and source_ids
            else str(value.get("example_id", f"line-{line_number}"))
        )
        dialog_id = int(value.get("dialog_id", contact_id))
        examples.append(
            StyleExample(
                example_id=str(value.get("example_id", f"human-{line_number}")),
                contact_id=dialog_id,
                incoming_text=incoming,
                response_text=response,
                source_type="human_matvey",
                polarity="positive",
                created_at=str(value.get("created_at", "")),
                context=context,
                provenance="human_matvey",
                source_key=f"telegram:{dialog_id}:{source_identity}",
            )
        )
        if len(examples) >= limit:
            break
    return tuple(examples), invalid


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
        context = _context_from_json(record.context_json)
        incoming = record.incoming_message_text or _last_user_text(context)
        if not incoming:
            continue
        common = {
            "example_id": f"feedback-{record.id}",
            "contact_id": contact_id,
            "incoming_text": incoming,
            "created_at": record.feedback_updated_at or record.created_at,
            "feedback_id": record.id,
            "context": context,
            "feedback_status": record.feedback_status,
            "feedback_category": record.feedback_category or "",
        }
        if record.feedback_status == "corrected" and record.corrected_reply_text:
            examples.append(
                StyleExample(
                    **common,
                    response_text=record.corrected_reply_text,
                    source_type="fix",
                    polarity="positive",
                    provenance="human_fix",
                    source_key=f"feedback:{record.id}:fix",
                )
            )
        elif record.feedback_status == "rejected":
            source_type = (
                "should_not_reply"
                if record.feedback_category == "should_not_reply"
                else "rejected"
            )
            examples.append(
                StyleExample(
                    **common,
                    response_text=record.generated_reply_text,
                    source_type=source_type,
                    polarity="negative",
                    provenance="ai_generated",
                    source_key=f"feedback:{record.id}:{source_type}",
                )
            )
        elif record.feedback_status == "approved":
            examples.append(
                StyleExample(
                    **common,
                    response_text=record.generated_reply_text,
                    source_type="approved_ai",
                    polarity="evaluation",
                    provenance="ai_generated",
                    source_key=f"feedback:{record.id}:good",
                )
            )
            approved_ai_count += 1
    return examples, approved_ai_count


def analysis_fingerprint(*, analysis_model: str, batch_size: int) -> str:
    value = {
        "analyzer_model": analysis_model,
        "analyzer_prompt_version": ANALYZER_PROMPT_VERSION,
        "analyzer_prompt_template": ANALYZER_PROMPT_TEMPLATE,
        "observation_schema_version": OBSERVATION_SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "batch_size": batch_size,
        "evidence_policy": "human-and-fix-positive;rejected-negative;ai-evaluation-only",
    }
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def merge_observations(observations: Any) -> tuple[StyleRule, ...]:
    grouped: dict[tuple[str, str, str, str, str], list[StyleRule]] = {}
    for rule in observations:
        key = (
            rule.behavior_category.strip().lower(),
            " ".join(rule.text.lower().split()),
            rule.applicable_context.strip().lower(),
            rule.scope.strip().lower(),
            rule.polarity.strip().lower(),
        )
        grouped.setdefault(key, []).append(rule)

    merged: list[StyleRule] = []
    for key, rules in sorted(grouped.items()):
        source_keys = tuple(
            sorted({item for rule in rules for item in rule.supporting_source_keys})
        )
        source_hashes = tuple(
            sorted({item for rule in rules for item in rule.supporting_source_hashes})
        )
        base_confidence = max(rule.confidence for rule in rules)
        confidence = min(0.99, base_confidence + 0.03 * max(len(source_keys) - 1, 0))
        source_types = {rule.source_type for rule in rules}
        semantic = "|".join(key)
        merged.append(
            replace(
                rules[0],
                observation_id=hashlib.sha256(semantic.encode("utf-8")).hexdigest()[:24],
                confidence=round(confidence, 4),
                evidence_count=len(source_keys),
                source_type=next(iter(source_types)) if len(source_types) == 1 else "mixed",
                supporting_source_keys=source_keys,
                supporting_source_hashes=source_hashes,
                source_priority=max(rule.source_priority for rule in rules),
            )
        )
    return tuple(merged)


def _bind_rule(
    rule: StyleRule,
    *,
    source_key: str,
    example: StyleExample,
) -> StyleRule:
    semantic = "|".join(
        (
            rule.behavior_category,
            " ".join(rule.text.lower().split()),
            rule.applicable_context,
            rule.scope,
            example.content_hash,
        )
    )
    return replace(
        rule,
        observation_id=hashlib.sha256(semantic.encode("utf-8")).hexdigest()[:24],
        supporting_source_keys=(source_key,) if source_key else (),
        supporting_source_hashes=(example.content_hash,),
        polarity=example.polarity,
        source_type=example.source_type,
        source_priority=_source_priority(example.source_type),
        evidence_count=1,
    )


def _publish_successful_build(
    *,
    output_directory: Path,
    state_path: Path,
    contact_id: int,
    examples: Sequence[StyleExample],
    sources: dict[str, CachedSource],
    content_cache: dict[str, tuple[StyleRule, ...]],
    rules: Sequence[StyleRule],
    summary: dict[str, Any],
    metadata: dict[str, str],
) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temp_parent = output_directory.parent
    with tempfile.TemporaryDirectory(prefix=".style-publish-", dir=temp_parent) as raw:
        staging = Path(raw)
        bundle_staging = staging / "bundle"
        state_staging = staging / "compiler_state.sqlite3"
        _write_bundle(
            bundle_staging,
            rules=rules,
            examples=examples,
            summary=summary,
            contact_id=contact_id,
        )
        write_compiler_state(
            state_staging,
            metadata=metadata,
            sources=sources,
            content_cache=content_cache,
            artifacts=_bundle_artifacts(bundle_staging, contact_id),
        )
        replacements = [
            (state_staging, state_path),
            (
                bundle_staging / "matvey_behavior_rules.md",
                output_directory / "matvey_behavior_rules.md",
            ),
            (bundle_staging / "style_profile.json", output_directory / "style_profile.json"),
            (bundle_staging / "example_bank.jsonl", output_directory / "example_bank.jsonl"),
            (bundle_staging / "build_summary.json", output_directory / "build_summary.json"),
            (
                bundle_staging / "contacts" / f"{contact_id}.json",
                output_directory / "contacts" / f"{contact_id}.json",
            ),
        ]
        _atomic_replace_many(replacements, staging / "backups")


def _write_bundle(
    directory: Path,
    *,
    rules: Sequence[StyleRule],
    examples: Sequence[StyleExample],
    summary: dict[str, Any],
    contact_id: int,
) -> None:
    directory.mkdir(parents=True)
    contacts = directory / "contacts"
    contacts.mkdir()
    rules_text = "# Matvey Behavior Rules\n\n" + "\n".join(
        f"- {rule.text}" for rule in rules
    )
    rules_path = directory / "matvey_behavior_rules.md"
    rules_path.write_text(rules_text + "\n", encoding="utf-8")
    profile = {
        "version": BUNDLE_VERSION,
        "built_at": summary["completed_at"],
        "rules": [rule.to_dict() for rule in rules],
    }
    _write_json(directory / "style_profile.json", profile)
    with (directory / "example_bank.jsonl").open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as handle:
        for example in _deduplicate(examples):
            handle.write(
                json.dumps(
                    example.to_dict(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            )
    contact_rules = [rule.to_dict() for rule in rules if rule.scope == "contact"]
    contact_profile = {
        "contact_id": contact_id,
        "built_at": summary["completed_at"],
        "rules": contact_rules,
        "evidence_count": sum(item.contact_id == contact_id for item in examples),
        "observed_statistics": _contact_statistics(examples, contact_id),
    }
    _write_json(contacts / f"{contact_id}.json", contact_profile)
    artifact_hashes = {
        "matvey_behavior_rules.md": _file_hash(rules_path),
        "style_profile.json": _file_hash(directory / "style_profile.json"),
        "example_bank.jsonl": _file_hash(directory / "example_bank.jsonl"),
        f"contacts/{contact_id}.json": _file_hash(contacts / f"{contact_id}.json"),
    }
    summary["bundle_artifact_hashes"] = artifact_hashes
    _write_json(directory / "build_summary.json", summary)


def _atomic_replace_many(
    replacements: Sequence[tuple[Path, Path]],
    backup_directory: Path,
) -> None:
    backup_directory.mkdir()
    completed: list[tuple[Path, Path | None]] = []
    try:
        for index, (source, destination) in enumerate(replacements):
            destination.parent.mkdir(parents=True, exist_ok=True)
            backup = None
            if destination.exists():
                backup = backup_directory / f"{index}.backup"
                shutil.copy2(destination, backup)
            source.replace(destination)
            completed.append((destination, backup))
    except Exception:
        for destination, backup in reversed(completed):
            if backup is None:
                destination.unlink(missing_ok=True)
            else:
                shutil.copy2(backup, destination)
        raise


def _build_summary(
    plan: BuildPlan,
    *,
    build_id: str,
    build_mode: str,
    started_at: str,
    completed_at: str,
    previous_bundle_id: str,
    batch_count: int,
    analyzer: StyleAnalyzer | None,
    analysis_model: str,
) -> dict[str, Any]:
    state_sources = plan.state.sources if plan.state is not None else {}
    summary: dict[str, Any] = {
        "build_id": build_id,
        "build_mode": build_mode,
        "started_at": started_at,
        "completed_at": completed_at,
        "built_at": completed_at,
        "total_sources_scanned": len(plan.examples),
        "source_example_count": len(plan.examples),
        "example_bank_count": len(_deduplicate(plan.examples)),
        "unchanged_sources": len(plan.unchanged),
        "new_sources": len(plan.new),
        "modified_sources": len(plan.modified),
        "deleted_sources": len(plan.deleted),
        "duplicate_sources_reusing_analysis": plan.duplicate_reuse,
        "invalid_sources": plan.invalid_sources,
        "cached_analyses_reused": len(plan.unchanged) + plan.duplicate_reuse,
        "new_analysis_batches": batch_count,
        "batch_count": batch_count,
        "OpenAI_request_count": _analyzer_metric(
            analyzer,
            "request_count",
            default=batch_count,
        ),
        "estimated_sources_not_reanalyzed": len(plan.unchanged) + plan.duplicate_reuse,
        "previous_bundle_id": previous_bundle_id,
        "analysis_fingerprint": plan.fingerprint,
        "analysis_model": analysis_model,
        "human_example_count": sum(
            item.source_type == "human_matvey" for item in plan.examples
        ),
        "fix_example_count": sum(item.source_type == "fix" for item in plan.examples),
        "negative_example_count": sum(
            item.polarity == "negative" for item in plan.examples
        ),
        "approved_ai_evaluation_count": sum(
            item.source_type == "approved_ai" for item in plan.examples
        ),
        "prior_cached_source_count": len(state_sources),
        "bundle_version": BUNDLE_VERSION,
    }
    input_tokens = _analyzer_metric(analyzer, "input_tokens_used", default=None)
    output_tokens = _analyzer_metric(analyzer, "output_tokens_used", default=None)
    if input_tokens is not None:
        summary["input_tokens_used"] = input_tokens
    if output_tokens is not None:
        summary["output_tokens_used"] = output_tokens
    return summary


def _plan_summary(plan: BuildPlan, *, batch_size: int) -> dict[str, Any]:
    return {
        "build_mode": "dry_run",
        "total_sources": len(plan.examples),
        "unchanged": len(plan.unchanged),
        "new": len(plan.new),
        "modified": len(plan.modified),
        "deleted": len(plan.deleted),
        "duplicate_analysis_reusable": plan.duplicate_reuse,
        "invalid_sources": plan.invalid_sources,
        "expected_analysis_batches": (
            (len(plan.hashes_to_analyze) + batch_size - 1) // batch_size
        ),
        "OpenAI_calls_required": bool(plan.hashes_to_analyze),
        "analysis_fingerprint": plan.fingerprint,
    }


def _no_op_summary(plan: BuildPlan) -> dict[str, Any]:
    previous_bundle_id = (
        plan.state.metadata.get("last_successful_bundle_id", "")
        if plan.state is not None
        else ""
    )
    return {
        "build_id": previous_bundle_id,
        "build_mode": "no_op",
        "total_sources_scanned": len(plan.examples),
        "unchanged_sources": len(plan.unchanged),
        "new_sources": 0,
        "modified_sources": 0,
        "deleted_sources": 0,
        "duplicate_sources_reusing_analysis": 0,
        "invalid_sources": plan.invalid_sources,
        "cached_analyses_reused": len(plan.unchanged),
        "new_analysis_batches": 0,
        "OpenAI_request_count": 0,
        "estimated_sources_not_reanalyzed": len(plan.unchanged),
        "previous_bundle_id": previous_bundle_id,
        "analysis_fingerprint": plan.fingerprint,
        "bundle_version": BUNDLE_VERSION,
    }


def _representatives(
    examples: Sequence[StyleExample],
    hashes: Sequence[str],
) -> list[StyleExample]:
    needed = set(hashes)
    result: list[StyleExample] = []
    seen: set[str] = set()
    for example in examples:
        if example.content_hash in needed and example.content_hash not in seen:
            seen.add(example.content_hash)
            result.append(example)
    return result


def _normalize_context(value: object) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        {
            "role": str(item.get("role", "")),
            "text": str(item.get("text", "")),
            "provenance": str(item.get("provenance", "")),
        }
        for item in value
        if isinstance(item, dict)
    )


def _context_from_json(value: str) -> tuple[dict[str, str], ...]:
    try:
        return _normalize_context(json.loads(value))
    except json.JSONDecodeError:
        return ()


def _last_user_text(context: object) -> str:
    if not isinstance(context, (list, tuple)):
        return ""
    for turn in reversed(context):
        if isinstance(turn, dict) and turn.get("role") == "user":
            return str(turn.get("text", "")).strip()
    return ""


def _deduplicate(examples: Sequence[StyleExample]) -> list[StyleExample]:
    seen: set[str] = set()
    result: list[StyleExample] = []
    for example in examples:
        key = example.content_hash
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
    if not positive:
        return {
            "positive_example_count": 0,
            "average_response_chars": 0,
            "lowercase_start_ratio": 0,
            "final_period_ratio": 0,
            "question_ratio": 0,
            "exclamation_ratio": 0,
            "source_counts": {},
        }
    count = len(positive)
    source_counts: dict[str, int] = {}
    for item in positive:
        source_counts[item.source_type] = source_counts.get(item.source_type, 0) + 1
    return {
        "positive_example_count": count,
        "average_response_chars": round(
            sum(len(item.response_text) for item in positive) / count,
            2,
        ),
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
        "source_counts": source_counts,
    }


def _source_priority(source_type: str) -> int:
    return {
        "fix": 100,
        "human_matvey": 80,
        "should_not_reply": 60,
        "rejected": 50,
        "approved_ai": 0,
    }.get(source_type, 10)


def _analyzer_metric(
    analyzer: StyleAnalyzer | None,
    name: str,
    *,
    default: Any,
) -> Any:
    return getattr(analyzer, name, default) if analyzer is not None else default


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bundle_artifacts(directory: Path, contact_id: int) -> dict[str, bytes]:
    relatives = (
        "matvey_behavior_rules.md",
        "style_profile.json",
        "example_bank.jsonl",
        "build_summary.json",
        f"contacts/{contact_id}.json",
    )
    return {
        relative: (directory / Path(relative)).read_bytes()
        for relative in relatives
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
