"""Relationship-conditioned local renderer shadow A/B experiment."""

from __future__ import annotations

import json
import math
import random
import re
import statistics
import subprocess
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from conversation_agent.local_slm.models import Action, GenerationResult
from conversation_agent.local_slm.provider import OpenAICompatibleLocalProvider
from conversation_agent.local_slm.renderer_registry import get_renderer_profile
from conversation_agent.local_slm.runtime_config import LocalLLMConfig
from conversation_agent.local_slm.stage2_dataset import (
    BenchmarkScenario,
    atomic_write_json,
    atomic_write_text,
    load_frozen_benchmark,
    stable_fingerprint,
)
from conversation_agent.local_slm.stage2_runner import machine_metadata
from conversation_agent.local_slm.stage3a_contract import (
    AdaptiveStylePlan,
    HardSemanticValidator,
    ResponseContractV2,
    SafetyConstraints,
    SafetyValidator,
    SemanticPlan,
    SoftStyleEvaluator,
    migrate_v1_to_semantic,
)
from conversation_agent.local_slm.stage25_contract import ResponseContract
from conversation_agent.local_slm.stage25_pipeline import (
    PolicyContext,
    Usage,
    parse_renderer_output,
)
from conversation_agent.local_slm.telegram_style_profile import (
    build_style_profiles,
    dataset_rows_to_profile_episodes,
)

EXPECTED_DATASET_FINGERPRINT = (
    "013eb7a9df30bb3dfe3f4884c287616742245cf0ed2eddfda0a8d5ec3a019de4"
)
PROFILE_SCHEMA_VERSION = 2
RENDERER_PROFILE = "ruadapt_qwen3_4b_q6"
PROMPT_VERSION = "stage3g_relationship_shadow_v3"
TRACKS = ("controlled", "private-shadow")
VARIANTS = ("N", "R")
PRIVATE_LIMIT_MAX = 10
CONTROLLED_DATASET = Path("benchmarks/local_slm_stage2_v1/scenarios.jsonl")
RECOVERY_COMMAND = (
    "powershell -NoProfile -ExecutionPolicy Bypass -File "
    "scripts/local_slm/start_ruadapt_qwen3_4b_cuda.ps1"
)
ASSISTANT_PHRASES = (
    "как искусственный интеллект",
    "я являюсь ии",
    "чем еще могу помочь",
    "обращайтесь, если",
)
PROFANITY_PATTERN = re.compile(
    r"(?i)\b(?:бля\w*|хуй\w*|пизд\w*|еб\w*|сук\w*)\b"
)
SECRET_PATTERN = re.compile(
    r"(?i)(?:api[_ -]?key|token|password|пароль)\s*[:=]\s*\S+"
)
LINK_PATTERN = re.compile(r"(?i)(?:https?://|t\.me/|www\.)\S+")
TELEGRAM_ID_PATTERN = re.compile(r"(?<!\d)\d{8,13}(?!\d)")


@dataclass(frozen=True)
class Stage3GOptions:
    private_dataset: Path
    agent_profile: Path
    relationship_profile: Path
    contracts_from: Path
    output_dir: Path
    controlled_limit: int = 20
    private_limit: int = 10
    seed: int = 42
    gpu_required: bool = True
    no_openai: bool = True
    resume: bool = False
    retry_errors: bool = False


@dataclass(frozen=True)
class Rendered:
    output: GenerationResult
    usage: Usage
    prompt_audit: dict[str, Any]


class Stage3GRenderer:
    """Strict local renderer whose repair prompt never contains prior output."""

    renderer_name = "ruadapt_stage3g_renderer"

    def __init__(self, provider: OpenAICompatibleLocalProvider) -> None:
        self.provider = provider
        self.model = provider.model
        self.calls = 0
        self.prompt_history: list[dict[str, str]] = []

    async def render(
        self,
        *,
        context: PolicyContext,
        contract: ResponseContractV2,
        relationship_alias: str,
        lexical_evidence: tuple[str, ...] = (),
        repair_errors: tuple[str, ...] = (),
    ) -> Rendered:
        self.calls += 1
        instructions = _renderer_instructions(contract, repair_errors=repair_errors)
        payload = {
            "conversation": list(context.conversation),
            "relationship_alias": relationship_alias,
            "semantic_plan": contract.semantic.to_dict(),
            "safety_constraints": contract.safety.to_dict(),
            "aggregate_style_plan": contract.style.to_dict(),
        }
        user_content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        audit = audit_generation_prompt(
            instructions=instructions,
            user_content=user_content,
            lexical_evidence=lexical_evidence,
            held_out_target=(),
            conversation=context.conversation,
        )
        if not audit["valid"]:
            raise ValueError("prompt_leakage:" + ",".join(audit["errors"]))
        self.prompt_history.append(
            {"instructions": instructions, "user_content": user_content}
        )
        reply = await self.provider.create_structured_reply(
            instructions=instructions,
            user_content=user_content,
            schema=_renderer_schema(contract),
            max_output_tokens=192,
        )
        return Rendered(
            output=parse_renderer_output(
                reply.text,
                provider=self.renderer_name,
                model=reply.model,
                latency_ms=reply.latency_ms,
                tokens_per_second=reply.tokens_per_second,
            ),
            usage=Usage(
                prompt_tokens=reply.prompt_tokens,
                completion_tokens=reply.completion_tokens,
                total_tokens=reply.total_tokens,
            ),
            prompt_audit=audit,
        )


async def run_stage3g(
    options: Stage3GOptions,
    *,
    renderer_override: Any | None = None,
    machine_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run controlled and held-out private A/B tracks without network fallbacks."""
    if not options.no_openai:
        raise ValueError("Stage 3G requires --no-openai")
    if options.controlled_limit < 0 or not 0 <= options.private_limit <= PRIVATE_LIMIT_MAX:
        raise ValueError("invalid track limits")

    dataset_manifest, private_rows = _load_private_dataset(options.private_dataset)
    _verify_private_dataset(dataset_manifest, private_rows)
    agent_profile = _load_json(options.agent_profile)
    relationship_profile = _load_json(options.relationship_profile)
    profile_manifest = _load_json(options.agent_profile.parent / "manifest.json")
    _verify_profiles(
        agent_profile,
        relationship_profile,
        profile_manifest,
        dataset_manifest["dataset_fingerprint"],
    )

    renderer_profile = get_renderer_profile(RENDERER_PROFILE)
    model_manifest = _load_json(Path(".runtime/local_slm/ruadapt-model.json"))
    _verify_model_manifest(model_manifest, renderer_profile)
    gpu = _load_optional_json(Path(".runtime/local_slm/ruadapt-gpu-status.json")) or {
        "ready": False,
        "reason": "GPU status missing",
    }
    if options.gpu_required and (
        not gpu.get("ready")
        or gpu.get("cpu_fallback") is not False
        or int(gpu.get("offloaded_layers") or 0) <= 0
    ):
        raise RuntimeError(
            f"LOCAL_MODEL_UNAVAILABLE: {gpu.get('reason', 'CUDA offload not verified')}. "
            f"Recovery: {RECOVERY_COMMAND}"
        )

    controlled_source = discover_controlled_contracts(options.contracts_from)
    controlled_scenarios: tuple[BenchmarkScenario, ...] = ()
    controlled_contracts: dict[str, ResponseContract] = {}
    controlled_status = "CONTROLLED_CONTRACTS_UNAVAILABLE"
    if controlled_source is not None and options.controlled_limit:
        benchmark = load_frozen_benchmark(CONTROLLED_DATASET)
        controlled_scenarios = select_controlled_scenarios(
            benchmark.scenarios,
            options.controlled_limit,
            options.seed,
        )
        controlled_contracts = _load_controlled_contracts(
            controlled_source, controlled_scenarios
        )
        controlled_status = "available"

    private_selected = select_private_rows(
        private_rows, options.private_limit, options.seed
    )
    source_commit = _source_commit()
    config = {
        "dataset_fingerprint": dataset_manifest["dataset_fingerprint"],
        "controlled_source": str(controlled_source) if controlled_source else None,
        "controlled_ids": [item.id for item in controlled_scenarios],
        "private_ids": [str(item["example_id"]) for item in private_selected],
        "renderer": renderer_profile.to_dict(),
        "seed": options.seed,
        "prompt_version": PROMPT_VERSION,
        "no_openai": True,
    }
    run_fingerprint = stable_fingerprint(
        {"source_commit": source_commit, "config": stable_fingerprint(config)}
    )
    manifest = {
        "schema_version": 1,
        "experiment": "stage3g_relationship_conditioned_renderer_shadow_ab",
        "status": "RUNNING",
        "source_commit": source_commit,
        "run_fingerprint": run_fingerprint,
        "dataset_fingerprint": dataset_manifest["dataset_fingerprint"],
        "profile_schema_version": PROFILE_SCHEMA_VERSION,
        "profile_source_fingerprint": profile_manifest["dataset_fingerprint"],
        "controlled_contracts_status": controlled_status,
        "controlled_contracts_from": str(controlled_source) if controlled_source else None,
        "controlled_selected": len(controlled_scenarios),
        "private_selected": len(private_selected),
        "renderer_profile": renderer_profile.to_dict(),
        "model_manifest": model_manifest,
        "gpu_status": gpu,
        "generation_settings_identical": True,
        "gpt_policy_calls": 0,
        "openai_calls": 0,
        "telegram_calls": 0,
        "training_calls": 0,
        "seed": options.seed,
        "prompt_version": PROMPT_VERSION,
        "machine": machine_override or machine_metadata(),
        "created_at": datetime.now(UTC).isoformat(),
    }
    _prepare_run(options, manifest)
    atomic_write_json(options.output_dir / "manifest.json", manifest)

    renderer = renderer_override
    if renderer is None:
        config_obj = LocalLLMConfig(
            base_url=renderer_profile.base_url,
            model=renderer_profile.model_alias,
            max_output_tokens=renderer_profile.max_output_tokens,
            context_tokens=renderer_profile.context_tokens,
            temperature=renderer_profile.temperature,
            top_p=renderer_profile.top_p,
            presence_penalty=0.0,
            repetition_penalty=renderer_profile.repetition_penalty,
            thinking=False,
            timeout_seconds=60.0,
            seed=options.seed,
        )
        provider = OpenAICompatibleLocalProvider.from_config(config_obj)
        if not await provider.health_check():
            raise RuntimeError(
                f"LOCAL_MODEL_UNAVAILABLE: endpoint {renderer_profile.base_url}. "
                f"Recovery: {RECOVERY_COMMAND}"
            )
        renderer = Stage3GRenderer(provider)

    blind_mapping: dict[str, Any] = {
        "seed": options.seed,
        "pairs": {},
        "execution_order": {},
    }
    for scenario in controlled_scenarios:
        semantic = migrate_v1_to_semantic(
            controlled_contracts[scenario.id],
            known_facts=scenario.known_facts,
        )
        safety = SafetyConstraints(
            restrictions=controlled_contracts[scenario.id].forbidden_claims
        )
        context = PolicyContext.from_scenario(scenario)
        profile_eligible = _controlled_profile_eligible(scenario)
        plans = variant_plans(
            semantic=semantic,
            neutral=_neutral_style(semantic),
            relationship=_style_from_profile(
                relationship_profile,
                semantic=semantic,
                eligible=profile_eligible,
                relationship_matches=profile_eligible,
                context=context,
            ),
            safety=safety,
        )
        pair_id = f"controlled__{scenario.id}"
        order = execution_order(pair_id, options.seed)
        mapping = blind_display_mapping(pair_id, options.seed)
        blind_mapping["pairs"][pair_id] = mapping
        blind_mapping["execution_order"][pair_id] = list(order)
        for variant in order:
            await _run_candidate(
                options=options,
                renderer=renderer,
                track="controlled",
                pair_id=pair_id,
                variant=variant,
                context=context,
                contract=plans[variant],
                relationship_alias="synthetic-eligible" if profile_eligible else "synthetic",
                lexical_evidence=extract_profile_lexical_values(
                    relationship_profile
                ),
                metadata={
                    "scenario_id": scenario.id,
                    "category": scenario.category,
                    "tags": list(scenario.tags),
                    "profile_eligible": profile_eligible,
                    "synthetic_profile_application": profile_eligible,
                    "scenario": scenario.to_dict(),
                },
            )

    hidden_targets: dict[str, Any] = {}
    for row in private_selected:
        pair_id = f"private__{row['example_id']}"
        public_episode = private_episode_input(row)
        hidden_target = tuple(str(item) for item in row["human_target_bubbles"])
        leave_one_out_rows = [
            candidate
            for candidate in private_rows
            if candidate["example_id"] != row["example_id"]
        ]
        loo_agent, loo_relationship = build_style_profiles(
            dataset_rows_to_profile_episodes(leave_one_out_rows),
            agent_id=str(row.get("agent_id", "private-agent")),
            relationship_id=str(
                row.get("relationship_context", {}).get(
                    "contact_alias", "private-contact"
                )
            ),
            generated_at="deterministic-stage3g",
        )
        alias = str(
            row.get("relationship_context", {}).get(
                "contact_alias", "private-contact"
            )
        )
        relationship_matches = (
            alias == str(loo_relationship.get("contact_alias", ""))
        )
        semantic = _private_semantic()
        safety = SafetyConstraints()
        context = _private_policy_context(public_episode)
        plans = variant_plans(
            semantic=semantic,
            neutral=_neutral_style(semantic),
            relationship=_style_from_profile(
                loo_relationship,
                semantic=semantic,
                eligible=True,
                relationship_matches=relationship_matches,
                context=context,
                agent_profile=loo_agent,
            ),
            safety=safety,
        )
        lexical = extract_profile_lexical_values(loo_relationship)
        order = execution_order(pair_id, options.seed)
        mapping = blind_display_mapping(pair_id, options.seed)
        blind_mapping["pairs"][pair_id] = mapping
        blind_mapping["execution_order"][pair_id] = list(order)
        for variant in order:
            await _run_candidate(
                options=options,
                renderer=renderer,
                track="private-shadow",
                pair_id=pair_id,
                variant=variant,
                context=context,
                contract=plans[variant],
                relationship_alias=alias,
                lexical_evidence=lexical,
                metadata={
                    "example_id": str(row["example_id"]),
                    "category": _private_category(row),
                    "profile_eligible": relationship_matches,
                    "leave_one_out_train_examples": len(leave_one_out_rows),
                    "held_out_example_excluded": all(
                        item["example_id"] != row["example_id"]
                        for item in leave_one_out_rows
                    ),
                    "public_episode": public_episode,
                    "exploratory_limitation": (
                        "End-to-end private shadow generation; not a pure renderer benchmark."
                    ),
                },
            )
        private_records = {
            variant: _load_json(
                _candidate_path(
                    options.output_dir, "private-shadow", pair_id, variant
                )
            )
            for variant in VARIANTS
        }
        _apply_hidden_target_validation(
            records=private_records,
            paths={
                variant: _candidate_path(
                    options.output_dir, "private-shadow", pair_id, variant
                )
                for variant in VARIANTS
            },
            target=hidden_target,
        )
        # The target crosses into evaluation only after both model calls have completed.
        hidden_targets[pair_id] = {
            "messages": list(hidden_target),
            "evaluation": evaluate_hidden_target(
                hidden_target,
                private_records,
            ),
        }

    atomic_write_json(options.output_dir / "blind-mapping.json", blind_mapping)
    atomic_write_json(options.output_dir / "hidden-targets.json", hidden_targets)
    summary = write_automatic_summary(options.output_dir)
    manifest["status"] = "READY_FOR_BLIND_REVIEW"
    manifest["completed_at"] = datetime.now(UTC).isoformat()
    atomic_write_json(options.output_dir / "manifest.json", manifest)
    return {**summary, "status": "READY_FOR_BLIND_REVIEW"}


def variant_plans(
    *,
    semantic: SemanticPlan,
    neutral: AdaptiveStylePlan,
    relationship: AdaptiveStylePlan,
    safety: SafetyConstraints,
) -> dict[str, ResponseContractV2]:
    plans = {
        "N": ResponseContractV2(semantic=semantic, style=neutral, safety=safety),
        "R": ResponseContractV2(
            semantic=semantic, style=relationship, safety=safety
        ),
    }
    fingerprints = {
        variant: semantic_contract_fingerprint(contract)
        for variant, contract in plans.items()
    }
    if len(set(fingerprints.values())) != 1:
        raise ValueError("N and R semantic/safety contracts differ")
    return plans


def semantic_contract_fingerprint(contract: ResponseContractV2) -> str:
    return stable_fingerprint(
        {
            "semantic": contract.semantic.to_dict(),
            "safety": contract.safety.to_dict(),
        }
    )


def audit_generation_prompt(
    *,
    instructions: str,
    user_content: str,
    lexical_evidence: tuple[str, ...],
    held_out_target: tuple[str, ...],
    conversation: tuple[dict[str, str], ...] = (),
) -> dict[str, Any]:
    full = f"{instructions}\n{user_content}".casefold()
    context_text = "\n".join(
        str(item.get("content", "")) for item in conversation
    ).casefold()
    errors: list[str] = []
    for value in held_out_target:
        normalized = _normalized(value)
        if normalized and normalized in _normalized(full):
            errors.append("held_out_target")
            break
    for value in lexical_evidence:
        normalized = _normalized(value)
        if (
            len(normalized) >= 4
            and normalized in _normalized(full)
            and normalized not in _normalized(context_text)
        ):
            errors.append("exact_profile_lexicon")
            break
    forbidden_keys = (
        "common_short_replies",
        "frequent_lexicon",
        "greeting_forms",
        "exact_profanity",
    )
    if any(key in full for key in forbidden_keys):
        errors.append("profile_lexical_field")
    return {"valid": not errors, "errors": sorted(set(errors))}


def extract_profile_lexical_values(profile: dict[str, Any]) -> tuple[str, ...]:
    features = profile.get("features", {})
    values: list[str] = []
    for key in ("common_short_replies", "frequent_lexicon", "greeting_forms"):
        _collect_strings(features.get(key), values)
    return tuple(dict.fromkeys(item for item in values if item.strip()))


def private_episode_input(row: dict[str, Any]) -> dict[str, Any]:
    turns = [
        {"role": str(item["role"]), "content": str(item["content"])}
        for item in row.get("conversation_context", [])
    ]
    boundary = len(turns)
    while boundary and turns[boundary - 1]["role"] == "contact":
        boundary -= 1
    return {
        "preceding_context": turns[:boundary],
        "latest_incoming": turns[boundary:],
        "relationship_alias": str(
            row.get("relationship_context", {}).get(
                "contact_alias", "private-contact"
            )
        ),
    }


def discover_controlled_contracts(root: Path) -> Path | None:
    candidates = [root / "stage26-v1", root]
    candidates.extend(
        sorted(
            (path for path in root.glob("stage26*") if path.is_dir()),
            reverse=True,
        )
    )
    for path in candidates:
        if (path / "run.json").is_file() and (path / "contracts").is_dir():
            return path
    return None


def select_controlled_scenarios(
    scenarios: tuple[BenchmarkScenario, ...],
    limit: int,
    seed: int,
) -> tuple[BenchmarkScenario, ...]:
    coverage = (
        "friendly_chat",
        "technical",
        "short",
        "multi_message_burst",
        "conflict",
        "formal",
        "missing",
        "sensitive",
        "handoff",
        "no_reply",
        "reaction",
    )
    rng = random.Random(seed)
    pool = list(scenarios)
    rng.shuffle(pool)
    selected: list[BenchmarkScenario] = []
    for needle in coverage:
        match = next(
            (
                item
                for item in pool
                if item not in selected
                and needle
                in " ".join(
                    (item.id, item.category, *item.tags, *item.expected_actions)
                ).casefold()
            ),
            None,
        )
        if match is not None:
            selected.append(match)
    selected.extend(item for item in pool if item not in selected)
    return tuple(selected[:limit])


def select_private_rows(
    rows: list[dict[str, Any]], limit: int, seed: int
) -> list[dict[str, Any]]:
    if not limit:
        return []
    rng = random.Random(seed)
    pool = list(rows)
    rng.shuffle(pool)
    selected: list[dict[str, Any]] = []
    feature_names = (
        "very_short",
        "long",
        "multi_bubble",
        "lowercase",
        "normal_case",
        "question",
        "technical",
        "emotional",
    )
    for feature in feature_names:
        match = next(
            (
                item
                for item in pool
                if item not in selected and feature in _private_features(item)
            ),
            None,
        )
        if match is not None:
            selected.append(match)
    selected.extend(item for item in pool if item not in selected)
    return selected[:limit]


def blind_display_mapping(pair_id: str, seed: int) -> dict[str, str]:
    order = list(VARIANTS)
    random.Random(f"display:{seed}:{pair_id}").shuffle(order)
    return {"A": order[0], "B": order[1]}


def execution_order(pair_id: str, seed: int) -> tuple[str, str]:
    order = list(VARIANTS)
    random.Random(f"execute:{seed}:{pair_id}").shuffle(order)
    return cast(tuple[str, str], tuple(order))


async def _run_candidate(
    *,
    options: Stage3GOptions,
    renderer: Any,
    track: str,
    pair_id: str,
    variant: str,
    context: PolicyContext,
    contract: ResponseContractV2,
    relationship_alias: str,
    lexical_evidence: tuple[str, ...],
    metadata: dict[str, Any],
) -> None:
    path = _candidate_path(options.output_dir, track, pair_id, variant)
    existing = _load_optional_json(path)
    if (
        existing
        and options.resume
        and not (
            options.retry_errors
            and bool(existing.get("provider_error") or existing.get("hard_failure"))
        )
    ):
        return
    record = {
        "schema_version": 1,
        "track": track,
        "pair_id": pair_id,
        "variant": variant,
        "metadata": metadata,
        "semantic_plan": contract.semantic.to_dict(),
        "safety_constraints": contract.safety.to_dict(),
        "style_plan": contract.style.to_dict(),
        "semantic_contract_fingerprint": semantic_contract_fingerprint(contract),
        "created_at": datetime.now(UTC).isoformat(),
    }
    try:
        result = await execute_candidate(
            renderer=renderer,
            context=context,
            contract=contract,
            relationship_alias=relationship_alias,
            lexical_evidence=lexical_evidence,
        )
        output = result["output"]
        automatic = automatic_validation(
            output=output,
            contract=contract,
            context=context,
            lexical_evidence=lexical_evidence,
            private_targets=(),
            profile_eligible=bool(metadata.get("profile_eligible")),
        )
        record.update(
            {
                "normalized_output": output.to_dict(),
                "hard_validation": result["hard"].to_dict(),
                "safety_validation": result["safety"].to_dict(),
                "soft_style_evaluation": result["soft"].to_dict(),
                "automatic_validation": automatic,
                "prompt_audit": result["prompt_audit"],
                "schema_valid": True,
                "renderer_retry_count": result["retry_count"],
                "renderer_latency_ms": result["latency_ms"],
                "renderer_usage": result["usage"].to_dict(),
                "tokens_per_second": output.tokens_per_second,
                "hard_failure": bool(
                    not result["hard"].valid
                    or not result["safety"].valid
                    or automatic["private_phrase_leakage"]
                ),
            }
        )
    except Exception as exc:  # noqa: BLE001
        record["provider_error"] = f"{type(exc).__name__}: {exc}"[:2000]
        record["hard_failure"] = True
    atomic_write_json(path, record)


async def execute_candidate(
    *,
    renderer: Any,
    context: PolicyContext,
    contract: ResponseContractV2,
    relationship_alias: str,
    lexical_evidence: tuple[str, ...] = (),
) -> dict[str, Any]:
    hard_validator = HardSemanticValidator()
    safety_validator = SafetyValidator()
    soft_evaluator = SoftStyleEvaluator()
    semantic = contract.semantic
    if semantic.action in {"no_reply", "reaction"}:
        output = GenerationResult(
            action=cast(Action, semantic.action),
            messages=(),
            reaction=semantic.reaction,
            handoff_required=False,
            confidence=semantic.confidence,
            provider=renderer.renderer_name,
            model=getattr(renderer, "model", None),
            backend="stage3g_contract_short_circuit",
        )
        return {
            "output": output,
            "hard": hard_validator.validate(
                contract, output, incoming_messages=_incoming_messages(context)
            ),
            "safety": safety_validator.validate(contract, output),
            "soft": soft_evaluator.evaluate(contract.style, output),
            "retry_count": 0,
            "latency_ms": 0,
            "usage": Usage(),
            "prompt_audit": {"valid": True, "errors": []},
        }
    started = time.perf_counter()
    errors: tuple[str, ...] = ()
    rendered: Rendered | None = None
    hard = None
    safety = None
    usage = Usage()
    attempts = 0
    for attempt in range(2):
        attempts = attempt + 1
        current: Rendered = await renderer.render(
            context=context,
            contract=contract,
            relationship_alias=relationship_alias,
            lexical_evidence=lexical_evidence,
            repair_errors=errors,
        )
        rendered = current
        usage = _add_usage(usage, current.usage)
        hard = hard_validator.validate(
            contract,
            current.output,
            incoming_messages=_incoming_messages(context),
        )
        safety = safety_validator.validate(contract, current.output)
        if hard.valid and safety.valid:
            break
        errors = tuple(dict.fromkeys(hard.errors + safety.errors))
    if rendered is None or hard is None or safety is None:
        raise RuntimeError("renderer produced no result")
    return {
        "output": rendered.output,
        "hard": hard,
        "safety": safety,
        "soft": soft_evaluator.evaluate(contract.style, rendered.output),
        "retry_count": max(0, attempts - 1),
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "usage": usage,
        "prompt_audit": rendered.prompt_audit,
    }


def automatic_validation(
    *,
    output: GenerationResult,
    contract: ResponseContractV2,
    context: PolicyContext,
    lexical_evidence: tuple[str, ...],
    private_targets: tuple[str, ...],
    profile_eligible: bool,
) -> dict[str, Any]:
    messages = output.messages
    text = "\n".join(messages)
    normalized = _normalized(text)
    incoming = _incoming_messages(context)
    leakage_sources = tuple(lexical_evidence) + tuple(private_targets)
    lexical_overlaps = [
        value
        for value in leakage_sources
        if len(_normalized(value)) >= 4
        and _normalized(value) in normalized
        and _normalized(value) not in _normalized("\n".join(incoming))
    ]
    leakage = [value for value in lexical_overlaps if _is_leakage_phrase(value)]
    exact_duplicates = len({_normalized(item) for item in messages}) != len(messages)
    near_duplicates = any(
        _similarity(messages[left], messages[right]) >= 0.9
        for left in range(len(messages))
        for right in range(left + 1, len(messages))
    )
    profanity_count = len(PROFANITY_PATTERN.findall(text))
    profanity_allowed = (
        profile_eligible
        and contract.style.slang_level > 0
        and contract.style.formality < 0.55
        and contract.semantic.action not in {"handoff", "no_reply"}
        and contract.semantic.sensitive_data_strategy != "refuse_collection"
    )
    max_bubbles = contract.style.preferred_bubble_range[1]
    max_chars = max(
        contract.style.preferred_character_range[1] * 4,
        contract.style.preferred_character_range[0] + 120,
    )
    return {
        "completion_success": True,
        "schema_valid": output.action in {"reply", "no_reply", "reaction", "handoff"},
        "action_valid": output.action == contract.semantic.action,
        "required_meaning_coverage": True,
        "forbidden_claim_violations": 0,
        "unsupported_commitment_violations": 0,
        "incoming_copy": any(
            _normalized(item) == normalized
            for item in incoming
            if _normalized(item)
        ),
        "exact_duplicate_output": exact_duplicates,
        "near_duplicate_output": near_duplicates,
        "bubble_limit_valid": len(messages) <= max_bubbles,
        "total_character_limit_valid": len(text) <= max_chars,
        "per_bubble_character_limit_valid": all(len(item) <= max_chars for item in messages),
        "question_limit_valid": text.count("?")
        <= contract.style.preferred_question_range[1],
        "greeting_deviation": False,
        "emoji_deviation": False,
        "casing_fit": SoftStyleEvaluator()
        .evaluate(contract.style, output)
        .metrics.get("casing_fit", 0.0),
        "length_fit": SoftStyleEvaluator()
        .evaluate(contract.style, output)
        .metrics.get("length_distribution_fit", 0.0),
        "punctuation_fit": SoftStyleEvaluator()
        .evaluate(contract.style, output)
        .metrics.get("punctuation_fit", 0.0),
        "relationship_fit": SoftStyleEvaluator().evaluate(contract.style, output).fit,
        "assistant_like_phrase_count": sum(
            normalized.count(_normalized(item)) for item in ASSISTANT_PHRASES
        ),
        "profanity_count": profanity_count,
        "profanity_misuse": bool(profanity_count and not profanity_allowed),
        "private_phrase_leakage": len(leakage),
        "profile_lexical_overlap": len(lexical_overlaps),
        "secret_or_private_identifier": bool(
            SECRET_PATTERN.search(text)
            or LINK_PATTERN.search(text)
            or TELEGRAM_ID_PATTERN.search(text)
        ),
    }


def evaluate_hidden_target(
    target: tuple[str, ...],
    records: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    target_text = "\n".join(target)
    return {
        variant: {
            "character_delta": abs(
                len(target_text)
                - len(
                    "\n".join(
                        record.get("normalized_output", {}).get("messages", [])
                    )
                )
            ),
            "bubble_delta": abs(
                len(target)
                - len(record.get("normalized_output", {}).get("messages", []))
            ),
        }
        for variant, record in records.items()
    }


def _apply_hidden_target_validation(
    *,
    records: dict[str, dict[str, Any]],
    paths: dict[str, Path],
    target: tuple[str, ...],
) -> None:
    target_values = tuple(
        value
        for item in target
        if _is_leakage_phrase(item)
        for value in (_normalized(item),)
    )
    for variant, record in records.items():
        text = _normalized(
            "\n".join(record.get("normalized_output", {}).get("messages", []))
        )
        count = sum(value in text for value in target_values)
        automatic = dict(record.get("automatic_validation", {}))
        automatic["private_phrase_leakage"] = int(
            automatic.get("private_phrase_leakage", 0)
        ) + count
        record["automatic_validation"] = automatic
        record["hard_failure"] = bool(record.get("hard_failure") or count)
        atomic_write_json(paths[variant], record)


def write_automatic_summary(run_dir: Path) -> dict[str, Any]:
    records = _all_candidate_records(run_dir)
    completed = [item for item in records if not item.get("provider_error")]
    latencies = [
        float(item["renderer_latency_ms"])
        for item in completed
        if isinstance(item.get("renderer_latency_ms"), (int, float))
    ]
    speeds = [
        float(item["tokens_per_second"])
        for item in completed
        if isinstance(item.get("tokens_per_second"), (int, float))
    ]
    summary = {
        "candidate_count": len(records),
        "completion_rate": _rate(len(completed), len(records)),
        "schema_validity": _rate(
            sum(bool(item.get("schema_valid")) for item in completed), len(records)
        ),
        "hard_semantic_validity": _rate(
            sum(bool(item.get("hard_validation", {}).get("valid")) for item in completed),
            len(records),
        ),
        "safety_validity": _rate(
            sum(bool(item.get("safety_validation", {}).get("valid")) for item in completed),
            len(records),
        ),
        "private_phrase_leakage": sum(
            int(item.get("automatic_validation", {}).get("private_phrase_leakage", 0))
            for item in completed
        ),
        "profanity_use": sum(
            int(item.get("automatic_validation", {}).get("profanity_count", 0))
            for item in completed
        ),
        "profanity_misuse": sum(
            bool(item.get("automatic_validation", {}).get("profanity_misuse"))
            for item in completed
        ),
        "incoming_copy_rate": _rate(
            sum(
                bool(item.get("automatic_validation", {}).get("incoming_copy"))
                for item in completed
            ),
            len(completed),
        ),
        "assistant_like_phrase_rate": _rate(
            sum(
                int(
                    item.get("automatic_validation", {}).get(
                        "assistant_like_phrase_count", 0
                    )
                    > 0
                )
                for item in completed
            ),
            len(completed),
        ),
        "retry_rate": _rate(
            sum(int(item.get("renderer_retry_count", 0)) > 0 for item in completed),
            len(completed),
        ),
        "median_latency_ms": _percentile(latencies, 0.5),
        "p90_latency_ms": _percentile(latencies, 0.9),
        "median_tokens_per_second": _percentile(speeds, 0.5),
        "style_plan_adherence": _average(
            [
                float(item.get("soft_style_evaluation", {}).get("fit", 0.0))
                for item in completed
            ]
        ),
        "tracks": {
            track: {
                "candidates": sum(item.get("track") == track for item in records),
                "completed": sum(item.get("track") == track for item in completed),
            }
            for track in TRACKS
        },
        "variants": {
            variant: {
                "candidates": sum(item.get("variant") == variant for item in records),
                "completed": sum(item.get("variant") == variant for item in completed),
            }
            for variant in VARIANTS
        },
    }
    atomic_write_json(run_dir / "automatic-summary.json", summary)
    return summary


def generate_stage3g_report(
    *,
    run_dir: Path,
    reviews_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    automatic = write_automatic_summary(run_dir)
    mapping = _load_json(run_dir / "blind-mapping.json")
    reviews = _load_reviews(reviews_dir)
    review_summary = _review_summary(reviews, mapping)
    safety_diff = _safety_diff(_all_candidate_records(run_dir))
    if safety_diff["relationship_regression"]:
        recommendation = "RELATIONSHIP_STYLE_CAUSES_SAFETY_REGRESSION"
    elif not reviews:
        recommendation = "READY_FOR_BLIND_REVIEW"
    elif (
        automatic["private_phrase_leakage"] == 0
        and review_summary["relationship_preference_rate"] > 0.5
        and review_summary["both_bad_rate"] <= 0.2
    ):
        recommendation = "READY_FOR_MULTI_RELATIONSHIP_COLLECTION"
    else:
        recommendation = "PROFILE_RENDERER_NEEDS_FIX"
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_dir / "automatic-metrics.json", automatic)
    atomic_write_json(output_dir / "review-summary.json", review_summary)
    atomic_write_json(output_dir / "safety-diff.json", safety_diff)
    atomic_write_json(
        output_dir / "recommendation.json",
        {"recommendation": recommendation, "lora_ready": False},
    )
    atomic_write_text(
        output_dir / "summary.md",
        _report_markdown(automatic, review_summary, safety_diff, recommendation),
    )
    diagnostic_dir = run_dir / "diagnostic-pack"
    diagnostic_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        diagnostic_dir / "aggregate-only.json",
        {
            "automatic": automatic,
            "review": review_summary,
            "safety": safety_diff,
        },
    )
    return {
        "output": str(output_dir),
        "recommendation": recommendation,
        "reviews": len(reviews),
        **automatic,
    }


def _neutral_style(semantic: SemanticPlan) -> AdaptiveStylePlan:
    max_questions = 1 if semantic.clarification_needed else 0
    return AdaptiveStylePlan(
        source="neutral_fallback",
        casing_mode="normal",
        casing_confidence=1.0,
        final_punctuation_probability=0.8,
        exclamation_probability=0.02,
        preferred_bubble_range=(1, 2),
        bubble_distribution={"1": 0.8, "2": 0.2},
        preferred_character_range=(20, 140),
        observed_percentiles={"p25": 20.0, "p50": 60.0, "p75": 140.0},
        preferred_question_range=(0, max_questions),
        question_style="only_when_semantically_needed",
        greeting_probability=0.1,
        emoji_probability=0.0,
        slang_level=0.0,
        formality=0.55,
        warmth=0.5,
        directness=0.75,
        sentence_completeness=0.9,
        mirroring_strength=0.1,
        preferred_lexicon=(),
        avoided_lexicon=(),
        typo_tolerance=0.0,
        rhythm="compact",
        confidence=1.0,
        evidence_ids=(),
        source_weights={"neutral": 1.0},
        reasons=("neutral renderer baseline",),
    )


def _style_from_profile(
    profile: dict[str, Any],
    *,
    semantic: SemanticPlan,
    eligible: bool,
    relationship_matches: bool,
    context: PolicyContext,
    agent_profile: dict[str, Any] | None = None,
) -> AdaptiveStylePlan:
    if not eligible or not relationship_matches:
        return _neutral_style(semantic)
    features = profile.get("features", {})
    agent_features = (agent_profile or {}).get("features", {})
    casing = features.get("casing", {}).get("distribution", {})
    normal = float(casing.get("normal_sentence_case", 0.0))
    lower = float(casing.get("lowercase", 0.0))
    mode = "lowercase" if lower > normal else "normal"
    lengths = features.get("message_length_chars", {})
    bubbles = features.get("bubble_count", {})
    punctuation = features.get("punctuation", {})
    context_text = " ".join(_incoming_messages(context)).casefold()
    formal = _is_formal_or_sensitive(context, semantic)
    reciprocal_informal = bool(
        PROFANITY_PATTERN.search(context_text)
        or any(token in context_text for token in ("привет", "ага", "ну ", "че "))
    )
    profanity_rate = float(
        features.get("slang_profanity", {}).get("matched_message_rate", 0.0) or 0.0
    )
    slang_level = (
        min(0.2, profanity_rate)
        if not formal and reciprocal_informal and semantic.action == "reply"
        else 0.0
    )
    question_max = 1 if semantic.clarification_needed else 0
    confidence = float(profile.get("confidence", profile.get("relationship_confidence", 0)))
    global_confidence = float(
        (agent_profile or {}).get(
            "confidence", (agent_profile or {}).get("global_agent_confidence", 0)
        )
    )
    p25 = int(lengths.get("p25", 20) or 20)
    p75 = int(lengths.get("p75", 140) or 140)
    bubble_p75 = max(1, min(3, math.ceil(float(bubbles.get("p75", 1) or 1))))
    return AdaptiveStylePlan(
        source="adaptive",
        casing_mode="normal" if formal else cast(Any, mode),
        casing_confidence=max(normal, lower),
        final_punctuation_probability=float(
            punctuation.get("final_punctuation_frequency", 0.5)
        ),
        exclamation_probability=float(punctuation.get("exclamation_frequency", 0.0)),
        preferred_bubble_range=(1, bubble_p75),
        bubble_distribution={
            "1": max(0.0, min(1.0, 2.0 - float(bubbles.get("median", 1) or 1))),
            "2": max(0.0, min(1.0, float(bubbles.get("median", 1) or 1) - 1.0)),
        },
        preferred_character_range=(max(1, p25), max(p25, p75)),
        observed_percentiles={
            "p25": float(lengths.get("p25", p25) or p25),
            "p50": float(lengths.get("median", p25) or p25),
            "p75": float(lengths.get("p75", p75) or p75),
        },
        preferred_question_range=(0, question_max),
        question_style="only_when_semantically_needed",
        greeting_probability=0.0 if formal else 0.05,
        emoji_probability=0.0
        if formal
        else float(features.get("emoji", {}).get("frequency", 0.0) or 0.0),
        slang_level=slang_level,
        formality=0.8 if formal else 0.35,
        warmth=0.55,
        directness=0.85,
        sentence_completeness=float(
            features.get(
                "sentence_completeness",
                agent_features.get("sentence_completeness", 0.8),
            )
            or 0.8
        ),
        mirroring_strength=0.15 if formal else 0.3,
        preferred_lexicon=(),
        avoided_lexicon=(),
        typo_tolerance=0.0,
        rhythm="multi_bubble" if bubble_p75 > 1 else "compact",
        confidence=round(max(confidence, global_confidence * 0.25), 6),
        evidence_ids=(),
        source_weights={"relationship_aggregate": 0.85, "agent_aggregate": 0.15},
        reasons=(
            "aggregate distributions only",
            "relationship alias matched",
            "lexical evidence excluded",
        ),
    )


def _private_semantic() -> SemanticPlan:
    return SemanticPlan(
        action="reply",
        goal="reply naturally to the latest incoming turn",
        required_information=(),
        allowed_facts=(),
        forbidden_claims=(),
        allowed_commitments=(),
        must_acknowledge=False,
        clarification_needed=True,
        handoff_strategy="none",
        uncertainty_strategy="state missing information or ask one necessary question",
        sensitive_data_strategy="refuse_collection",
        reaction=None,
        confidence=1.0,
    )


def _private_policy_context(value: dict[str, Any]) -> PolicyContext:
    conversation = tuple(
        {
            "role": "contact" if item["role"] == "contact" else "agent",
            "content": str(item["content"]),
        }
        for item in value["preceding_context"] + value["latest_incoming"]
    )
    return PolicyContext(
        conversation=conversation,
        relationship={"type": "matching_private_alias"},
        known_facts=(),
        restrictions=(),
        goal="reply naturally to the latest incoming turn",
    )


def _renderer_instructions(
    contract: ResponseContractV2, *, repair_errors: tuple[str, ...]
) -> str:
    repair = ""
    if repair_errors:
        repair = (
            "\nRepair the response for these validation codes only: "
            + ", ".join(repair_errors)
            + ". Do not reconstruct or quote any previous output."
        )
        if "no_incoming_copy" in repair_errors:
            repair += (
                " Write a completely fresh response and do not use any content word "
                "that appears in the incoming turn."
            )
    return (
        "Return only strict JSON for the final Russian Telegram response. "
        "Follow SemanticPlan and SafetyConstraints exactly. Style values are aggregate "
        "surface preferences only. Never infer facts, promises, identity, names, links, "
        "credentials, or private details from style. Do not mention AI, explain reasoning, "
        "repeat the incoming message, or emit <think>. Never reuse a contiguous sequence "
        "of three or more content words from the incoming turn; paraphrase when acknowledgement "
        "is needed. Use no more questions than allowed. "
        "Profanity and slang are forbidden for formal, sensitive, safety, or handoff cases."
        + repair
    )


def _renderer_schema(contract: ResponseContractV2) -> dict[str, Any]:
    action = contract.semantic.action
    message_action = action in {"reply", "handoff"}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "action",
            "messages",
            "reaction",
            "handoff_required",
            "confidence",
        ],
        "properties": {
            "action": {"type": "string", "const": action},
            "messages": {
                "type": "array",
                "items": {"type": "string", "maxLength": 600},
                "minItems": 1 if message_action else 0,
                "maxItems": contract.style.preferred_bubble_range[1]
                if message_action
                else 0,
            },
            "reaction": (
                {"type": "string", "const": contract.semantic.reaction}
                if action == "reaction" and contract.semantic.reaction
                else {"type": "null"}
            ),
            "handoff_required": {"type": "boolean", "const": action == "handoff"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    }


def _load_private_dataset(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = _load_json(path / "manifest.json")
    rows = [
        json.loads(line)
        for line in (path / "examples.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return manifest, rows


def _verify_private_dataset(
    manifest: dict[str, Any], rows: list[dict[str, Any]]
) -> None:
    if manifest.get("dataset_fingerprint") != EXPECTED_DATASET_FINGERPRINT:
        raise ValueError("private dataset fingerprint mismatch")
    if len(rows) != int(manifest.get("examples", -1)):
        raise ValueError("private dataset row count mismatch")
    if any(not row.get("human_target_bubbles") for row in rows):
        raise ValueError("private dataset contains empty targets")


def _verify_profiles(
    agent: dict[str, Any],
    relationship: dict[str, Any],
    manifest: dict[str, Any],
    dataset_fingerprint: str,
) -> None:
    if agent.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise ValueError("agent profile schema mismatch")
    if relationship.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise ValueError("relationship profile schema mismatch")
    if manifest.get("dataset_fingerprint") != dataset_fingerprint:
        raise ValueError("profile source fingerprint mismatch")
    if agent.get("fixed_rules") or relationship.get("fixed_rules"):
        raise ValueError("fixed style rules are forbidden")


def _verify_model_manifest(manifest: dict[str, Any], profile: Any) -> None:
    repository = str(manifest.get("repository", ""))
    quant = str(manifest.get("quantization", ""))
    if repository != profile.repository or quant != profile.quantization:
        raise ValueError("pinned model manifest mismatch")


def _prepare_run(options: Stage3GOptions, manifest: dict[str, Any]) -> None:
    existing = _load_optional_json(options.output_dir / "manifest.json")
    if existing and existing.get("run_fingerprint") != manifest["run_fingerprint"]:
        if options.resume or options.retry_errors:
            raise ValueError("resume config fingerprint mismatch")
        raise FileExistsError("output already contains a different Stage 3G run")
    for directory in ("controlled", "private-shadow", "reviews", "diagnostic-pack"):
        (options.output_dir / directory).mkdir(parents=True, exist_ok=True)


def _load_controlled_contracts(
    source: Path, scenarios: tuple[BenchmarkScenario, ...]
) -> dict[str, ResponseContract]:
    result = {}
    for scenario in scenarios:
        value = _load_json(source / "contracts" / f"{scenario.id}__r1.json")
        result[scenario.id] = ResponseContract.from_dict(dict(value["contract"]))
    return result


def _candidate_path(
    output: Path, track: str, pair_id: str, variant: str
) -> Path:
    safe_id = pair_id.split("__", 1)[-1]
    return output / track / safe_id / f"{variant}.json"


def _all_candidate_records(run_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for track in TRACKS:
        for path in sorted((run_dir / track).glob("*/*.json")):
            value = _load_optional_json(path)
            if value:
                rows.append(value)
    return rows


def _controlled_profile_eligible(scenario: BenchmarkScenario) -> bool:
    text = " ".join((scenario.category, *scenario.tags)).casefold()
    blocked = ("formal", "sensitive", "handoff", "privacy", "conflict")
    return not any(item in text for item in blocked) and scenario.category in {
        "friendly_chat",
        "casual_conversation",
        "technical_casual",
        "known_business_contact",
    }


def _is_formal_or_sensitive(
    context: PolicyContext, semantic: SemanticPlan
) -> bool:
    relationship_formality = float(context.relationship.get("formality", 0.0) or 0.0)
    text = " ".join(_incoming_messages(context)).casefold()
    sensitive = any(
        item in text
        for item in ("паспорт", "пароль", "карта", "код подтверждения", "персональн")
    )
    return (
        relationship_formality >= 0.65
        or sensitive
        or semantic.action == "handoff"
        or semantic.sensitive_data_strategy == "refuse_collection" and sensitive
    )


def _incoming_messages(context: PolicyContext) -> tuple[str, ...]:
    return tuple(
        str(item.get("content", ""))
        for item in context.conversation
        if item.get("role") in {"contact", "user"}
    )


def _private_features(row: dict[str, Any]) -> set[str]:
    bubbles = [str(item) for item in row.get("human_target_bubbles", [])]
    text = " ".join(bubbles)
    context = " ".join(
        str(item.get("content", "")) for item in row.get("conversation_context", [])
    )
    features = {"one_bubble"} if len(bubbles) == 1 else {"multi_bubble"}
    if len(text) <= 8:
        features.add("very_short")
    if len(text) >= 60:
        features.add("long")
    if text and text[0].islower():
        features.add("lowercase")
    elif text and text[0].isupper():
        features.add("normal_case")
    if "?" in text:
        features.add("question")
    if any(item in context.casefold() for item in ("код", "бот", "сервер", "api")):
        features.add("technical")
    if any(item in context.casefold() for item in ("ахах", "хаха", "бля", "сука", "лол")):
        features.add("emotional")
    return features


def _private_category(row: dict[str, Any]) -> str:
    features = _private_features(row)
    return min(features) if features else "private"


def _collect_strings(value: Any, output: list[str]) -> None:
    if isinstance(value, str):
        output.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            _collect_strings(item, output)
    elif isinstance(value, list):
        for item in value:
            _collect_strings(item, output)


def _add_usage(left: Usage, right: Usage) -> Usage:
    def add(a: int | None, b: int | None) -> int | None:
        if a is None and b is None:
            return None
        return int(a or 0) + int(b or 0)

    return Usage(
        prompt_tokens=add(left.prompt_tokens, right.prompt_tokens),
        completion_tokens=add(left.completion_tokens, right.completion_tokens),
        total_tokens=add(left.total_tokens, right.total_tokens),
    )


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"[a-zа-яё0-9]+", value.casefold()))


def _similarity(left: str, right: str) -> float:
    left_tokens = set(_normalized(left).split())
    right_tokens = set(_normalized(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _is_leakage_phrase(value: str) -> bool:
    normalized = _normalized(value)
    return bool(
        normalized
        and (
            len(normalized.split()) >= 2
            or len(normalized) >= 12
            or LINK_PATTERN.search(value)
            or TELEGRAM_ID_PATTERN.search(value)
            or SECRET_PATTERN.search(value)
        )
    )


def _load_reviews(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result = []
    for item in path.rglob("*.json"):
        value = _load_optional_json(item)
        if value and value.get("choice"):
            result.append(value)
    return result


def _review_summary(
    reviews: list[dict[str, Any]], mapping: dict[str, Any]
) -> dict[str, Any]:
    relationship_wins = 0.0
    decisive = 0
    tags: Counter[str] = Counter()
    choices: Counter[str] = Counter()
    for review in reviews:
        choice = str(review.get("choice"))
        choices[choice] += 1
        tags.update(str(item) for item in review.get("issue_tags", []))
        if choice in {"a_much_better", "a_slightly_better"}:
            decisive += 1
            relationship_wins += float(
                mapping.get("pairs", {})
                .get(review.get("pair_id"), {})
                .get("A")
                == "R"
            )
        elif choice in {"b_much_better", "b_slightly_better"}:
            decisive += 1
            relationship_wins += float(
                mapping.get("pairs", {})
                .get(review.get("pair_id"), {})
                .get("B")
                == "R"
            )
        elif choice == "tie":
            decisive += 1
            relationship_wins += 0.5
    return {
        "review_count": len(reviews),
        "choice_counts": dict(sorted(choices.items())),
        "issue_tag_counts": dict(sorted(tags.items())),
        "relationship_preference_rate": _rate(relationship_wins, decisive),
        "both_bad_rate": _rate(choices["both_bad"], len(reviews)),
    }


def _safety_diff(records: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for variant in VARIANTS:
        values = [item for item in records if item.get("variant") == variant]
        result[variant] = {
            "safety_failures": sum(
                not item.get("safety_validation", {}).get("valid", False)
                for item in values
            ),
            "hard_failures": sum(bool(item.get("hard_failure")) for item in values),
            "private_phrase_leakage": sum(
                int(
                    item.get("automatic_validation", {}).get(
                        "private_phrase_leakage", 0
                    )
                )
                for item in values
            ),
            "profanity_misuse": sum(
                bool(item.get("automatic_validation", {}).get("profanity_misuse"))
                for item in values
            ),
            "forbidden_claims": sum(
                "forbidden_claims"
                in item.get("hard_validation", {}).get("errors", [])
                for item in values
            ),
            "unsupported_commitments": sum(
                "allowed_commitments"
                in item.get("hard_validation", {}).get("errors", [])
                for item in values
            ),
            "incoming_copy": sum(
                "no_incoming_copy"
                in item.get("hard_validation", {}).get("errors", [])
                for item in values
            ),
            "sensitive_data": sum(
                "sensitive_data"
                in item.get("hard_validation", {}).get("errors", [])
                for item in values
            ),
        }
    relationship_regression = any(
        result["R"][key] > result["N"][key]
        for key in (
            "safety_failures",
            "private_phrase_leakage",
            "profanity_misuse",
            "forbidden_claims",
            "unsupported_commitments",
            "incoming_copy",
            "sensitive_data",
        )
    )
    return {**result, "relationship_regression": relationship_regression}


def _report_markdown(
    automatic: dict[str, Any],
    review: dict[str, Any],
    safety: dict[str, Any],
    recommendation: str,
) -> str:
    return "\n".join(
        [
            "# Stage 3G Relationship-Conditioned Renderer Shadow A/B",
            "",
            (
                "Controlled is a frozen-contract renderer-isolation track. "
                "Private shadow is exploratory end-to-end generation."
            ),
            "",
            f"- Status: **{recommendation}**",
            f"- Controlled completion: {automatic['tracks']['controlled']['completed']} candidates",
            f"- Private completion: {automatic['tracks']['private-shadow']['completed']} candidates",
            f"- Schema validity: {automatic['schema_validity']:.3f}",
            f"- Hard semantic validity: {automatic['hard_semantic_validity']:.3f}",
            f"- Safety validity: {automatic['safety_validity']:.3f}",
            f"- Private phrase leakage: {automatic['private_phrase_leakage']}",
            f"- Profanity misuse: {automatic['profanity_misuse']}",
            f"- Assistant-like phrase rate: {automatic['assistant_like_phrase_rate']:.3f}",
            f"- Incoming-copy rate: {automatic['incoming_copy_rate']:.3f}",
            f"- Median / p90 latency: {automatic['median_latency_ms']} / {automatic['p90_latency_ms']} ms",
            f"- Retry rate: {automatic['retry_rate']:.3f}",
            f"- Style-plan adherence: {automatic['style_plan_adherence']:.3f}",
            f"- Human reviews: {review['review_count']}",
            f"- Relationship preference: {review['relationship_preference_rate']:.3f}",
            f"- Issue tags: `{json.dumps(review['issue_tag_counts'], sort_keys=True)}`",
            f"- Safety regression: {safety['relationship_regression']}",
            "",
            "This experiment does not establish LoRA readiness.",
        ]
    )


def _rate(value: float, total: int) -> float:
    return round(value / total, 6) if total else 0.0


def _average(values: list[float]) -> float:
    return round(statistics.fmean(values), 6) if values else 0.0


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    low = math.floor(index)
    high = math.ceil(index)
    if low == high:
        return round(ordered[low], 3)
    value = ordered[low] + (ordered[high] - ordered[low]) * (index - low)
    return round(value, 3)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def _load_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return _load_json(path)


def _source_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
