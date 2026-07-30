"""Stage 3A adaptive-style benchmark using saved semantic contracts."""

from __future__ import annotations

import json
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
from conversation_agent.local_slm.stage2_runner import load_run_results, machine_metadata
from conversation_agent.local_slm.stage3a_contract import (
    AdaptiveStyleResolver,
    AgentStyleProfile,
    HardSemanticValidator,
    RelationshipStyleProfile,
    ResponseContractV2,
    SafetyValidator,
    SoftStyleEvaluator,
    StyleFeatureExtractor,
    empty_style_statistics,
    migrate_v1_to_v2,
)
from conversation_agent.local_slm.stage25_contract import ResponseContract
from conversation_agent.local_slm.stage25_pipeline import (
    PolicyContext,
    Usage,
    parse_renderer_output,
)

PIPELINE = "gpt_semantic_policy_adaptive_style_ruadapt_renderer"
PROMPT_VERSION = "adaptive_style_renderer_v1"
EXPECTED_STAGE26_SNAPSHOT = (
    "1be968429b511f02c08289533b694d984d436305e0fb0e2664df7221f0362ddb"
)


@dataclass(frozen=True)
class Stage3AOptions:
    dataset_path: Path
    contracts_from: Path
    renderer: str
    output_dir: Path
    seed: int = 42
    scenario_limit: int | None = None
    category: str | None = None
    resume: bool = False
    retry_errors: bool = False
    gpu_required: bool = True


@dataclass(frozen=True)
class Stage3ARendered:
    output: GenerationResult
    usage: Usage


class Stage3ARenderer:
    renderer_name = "ruadapt_adaptive_style_renderer"

    def __init__(self, provider: OpenAICompatibleLocalProvider) -> None:
        self.provider = provider
        self.model = provider.model
        self.calls = 0

    async def render(
        self,
        *,
        context: PolicyContext,
        contract: ResponseContractV2,
        previous_output: str = "",
        repair_errors: tuple[str, ...] = (),
    ) -> Stage3ARendered:
        self.calls += 1
        payload: dict[str, Any] = {
            "conversation": list(context.conversation),
            "relationship": context.relationship,
            "allowed_facts": list(context.known_facts),
            "response_contract_v2": contract.to_dict(),
        }
        if repair_errors:
            payload["repair"] = {
                "previous_output": previous_output,
                "hard_violations": list(repair_errors),
                "instruction": "Repair only semantic or safety violations.",
            }
        reply = await self.provider.create_structured_reply(
            instructions=_renderer_instructions(contract),
            user_content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            schema=_renderer_schema(contract),
            max_output_tokens=192,
        )
        return Stage3ARendered(
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
        )


async def run_stage3a(
    options: Stage3AOptions,
    *,
    renderer_override: Any | None = None,
    machine_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    benchmark = load_frozen_benchmark(options.dataset_path)
    scenarios = _select_scenarios(benchmark.scenarios, options)
    profile = get_renderer_profile(options.renderer)
    model_manifest = _load_json(Path(".runtime/local_slm/ruadapt-model.json"))
    _validate_model_manifest(model_manifest, profile)
    gpu = _load_optional_json(Path(".runtime/local_slm/ruadapt-gpu-status.json")) or {
        "ready": False,
        "reason": "GPU status missing",
    }
    if options.gpu_required and (
        not gpu.get("ready") or gpu.get("cpu_fallback") is not False
    ):
        raise RuntimeError("GPU-required Stage 3A run refused")
    source_run = _load_json(options.contracts_from / "run.json")
    source_snapshot = str(source_run.get("contract_snapshot_fingerprint", ""))
    if source_snapshot != EXPECTED_STAGE26_SNAPSHOT:
        raise ValueError("Stage 2.6 contract snapshot fingerprint mismatch")
    if source_run.get("benchmark_fingerprint") != benchmark.fingerprint:
        raise ValueError("Stage 2.6 benchmark fingerprint mismatch")
    source_contracts = _load_v1_contracts(options.contracts_from, scenarios)
    contracts = {
        scenario.id: _adaptive_contract(scenario, source_contracts[scenario.id])
        for scenario in scenarios
    }
    snapshot = _write_contract_snapshot(options.output_dir, contracts)
    config = {
        "benchmark_fingerprint": benchmark.fingerprint,
        "source_snapshot": source_snapshot,
        "adaptive_snapshot": snapshot,
        "renderer": profile.to_dict(),
        "scenario_ids": [item.id for item in scenarios],
        "seed": options.seed,
        "prompt_version": PROMPT_VERSION,
    }
    config_fingerprint = stable_fingerprint(config)
    commit = _source_commit()
    run_meta = {
        "benchmark_fingerprint": benchmark.fingerprint,
        "source_stage26_snapshot": source_snapshot,
        "adaptive_contract_snapshot_fingerprint": snapshot,
        "source_commit": commit,
        "config_fingerprint": config_fingerprint,
        "run_fingerprint": stable_fingerprint(
            {"source_commit": commit, "config": config_fingerprint}
        ),
        "renderer_profile": profile.to_dict(),
        "model_manifest": model_manifest,
        "gpu_status": gpu,
        "scenario_count_selected": len(scenarios),
        "seed": options.seed,
        "prompt_version": PROMPT_VERSION,
        "machine": machine_override or machine_metadata(),
        "gpt_policy_calls": 0,
        "created_at": datetime.now(UTC).isoformat(),
    }
    _prepare_run(options, run_meta)
    atomic_write_json(options.output_dir / "run.json", run_meta)
    if renderer_override is None:
        config_obj = LocalLLMConfig(
            base_url=profile.base_url,
            model=profile.model_alias,
            max_output_tokens=profile.max_output_tokens,
            context_tokens=profile.context_tokens,
            temperature=profile.temperature,
            top_p=profile.top_p,
            presence_penalty=0.0,
            repetition_penalty=profile.repetition_penalty,
            thinking=False,
            timeout_seconds=60.0,
            seed=options.seed,
        )
        provider = OpenAICompatibleLocalProvider.from_config(config_obj)
        if not await provider.health_check():
            raise RuntimeError("Ruadapt endpoint unavailable")
        renderer: Any = Stage3ARenderer(provider)
    else:
        renderer = renderer_override
    for scenario in scenarios:
        path = options.output_dir / "results" / PIPELINE / f"{scenario.id}__r1.json"
        existing = _load_optional_json(path)
        if (
            existing
            and options.resume
            and not (options.retry_errors and _has_error(existing))
        ):
            continue
        context = PolicyContext.from_scenario(scenario)
        contract = contracts[scenario.id]
        record = {
            "scenario_id": scenario.id,
            "category": scenario.category,
            "tags": list(scenario.tags),
            "pipeline": PIPELINE,
            "scenario": scenario.to_dict(),
            "semantic_plan": contract.semantic.to_dict(),
            "adaptive_style_plan": contract.style.to_dict(),
            "safety_constraints": contract.safety.to_dict(),
            "style_evidence": list(contract.style.evidence_ids),
            "run_fingerprint": run_meta["run_fingerprint"],
            "created_at": datetime.now(UTC).isoformat(),
        }
        try:
            execution = await _execute(
                renderer=renderer,
                context=context,
                contract=contract,
            )
            record.update(
                {
                    "normalized_output": execution["output"].to_dict(),
                    "hard_validation": execution["hard"].to_dict(),
                    "safety_validation": execution["safety"].to_dict(),
                    "soft_style_evaluation": execution["soft"].to_dict(),
                    "renderer_retry_count": execution["retry_count"],
                    "renderer_latency_ms": execution["latency_ms"],
                    "latency_ms": execution["latency_ms"],
                    "renderer_usage": execution["usage"].to_dict(),
                    "prompt_tokens": execution["usage"].prompt_tokens,
                    "completion_tokens": execution["usage"].completion_tokens,
                    "total_tokens": execution["usage"].total_tokens,
                    "tokens_per_second": execution["output"].tokens_per_second,
                    "gpu_vram_used_mib": gpu.get("vram_used_mib"),
                    "gpu_offloaded_layers": gpu.get("offloaded_layers"),
                }
            )
            if not execution["hard"].valid or not execution["safety"].valid:
                record["renderer_error"] = "hard_or_safety_validation"
        except Exception as exc:  # noqa: BLE001
            record["provider_error"] = f"{type(exc).__name__}: {exc}"[:2000]
        atomic_write_json(path, record)
        _write_summary(options.output_dir, run_meta)
    return _write_summary(options.output_dir, run_meta)


async def _execute(
    *,
    renderer: Any,
    context: PolicyContext,
    contract: ResponseContractV2,
) -> dict[str, Any]:
    semantic = contract.semantic
    hard_validator = HardSemanticValidator()
    safety_validator = SafetyValidator()
    soft_evaluator = SoftStyleEvaluator()
    if semantic.action in {"no_reply", "reaction"}:
        output = GenerationResult(
            action=cast(Action, semantic.action),
            messages=(),
            reaction=semantic.reaction,
            handoff_required=False,
            confidence=semantic.confidence,
            provider=renderer.renderer_name,
            model=getattr(renderer, "model", None),
            backend="adaptive_contract_short_circuit",
        )
        return {
            "output": output,
            "hard": hard_validator.validate(
                contract,
                output,
                incoming_messages=_incoming_messages(context),
            ),
            "safety": safety_validator.validate(contract, output),
            "soft": soft_evaluator.evaluate(contract.style, output),
            "retry_count": 0,
            "latency_ms": 0,
            "usage": Usage(),
        }
    started = time.perf_counter()
    previous = ""
    errors: tuple[str, ...] = ()
    rendered: Stage3ARendered | None = None
    hard = None
    safety = None
    attempts = 0
    usage = Usage()
    for attempt in range(2):
        attempts = attempt + 1
        current: Stage3ARendered = await renderer.render(
            context=context,
            contract=contract,
            previous_output=previous,
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
        errors = hard.errors + safety.errors
        previous = current.output.raw_output
    if rendered is None or hard is None or safety is None:
        raise RuntimeError("adaptive renderer produced no result")
    return {
        "output": rendered.output,
        "hard": hard,
        "safety": safety,
        "soft": soft_evaluator.evaluate(contract.style, rendered.output),
        "retry_count": max(0, attempts - 1),
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "usage": usage,
    }


def aggregate_stage3a_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    completed = [item for item in results if not item.get("provider_error")]
    hard = [item.get("hard_validation", {}) for item in completed]
    safety = [item.get("safety_validation", {}) for item in completed]
    soft = [item.get("soft_style_evaluation", {}) for item in completed]
    style_plans = [item.get("adaptive_style_plan", {}) for item in completed]
    metric_names = {
        name
        for value in soft
        for name in (value.get("metrics") or {})
    }
    latencies = [
        int(item["renderer_latency_ms"])
        for item in completed
        if isinstance(item.get("renderer_latency_ms"), int)
    ]
    speeds = [
        float(item["tokens_per_second"])
        for item in completed
        if isinstance(item.get("tokens_per_second"), (int, float))
    ]
    metrics = {
        "total_scenarios": total,
        "completion_rate": _rate(len(completed), total),
        "hard_semantic_validity": _rate(
            sum(bool(item.get("valid")) for item in hard), total
        ),
        "safety_validity": _rate(
            sum(bool(item.get("valid")) for item in safety), total
        ),
        "soft_style_fit": _average(
            [float(item.get("fit", 0.0)) for item in soft]
        ),
        "average_style_confidence": _average(
            [float(plan.get("confidence", 0.0)) for plan in style_plans]
        ),
        **{
            name: _average(
                [
                    float((item.get("metrics") or {}).get(name, 0.0))
                    for item in soft
                ]
            )
            for name in sorted(metric_names)
        },
        "agent_profile_confidence": 0.0,
        "relationship_profile_confidence": 0.0,
        "conversation_snapshot_confidence": _average(
            [
                float(plan.get("source_weights", {}).get("conversation", 0.0))
                for plan in style_plans
            ]
        ),
        "fallback_rate": _rate(
            sum(plan.get("source") == "neutral_fallback" for plan in style_plans),
            total,
        ),
        "average_evidence_count": _average(
            [float(len(plan.get("evidence_ids", []))) for plan in style_plans]
        ),
        "renderer_retry_rate": _rate(
            sum(int(item.get("renderer_retry_count", 0)) > 0 for item in completed),
            len(completed),
        ),
        "forbidden_claim_violations": _check_failures(hard, "forbidden_claims"),
        "allowed_commitment_violations": _check_failures(
            hard, "allowed_commitments"
        ),
        "handoff_violations": _check_failures(hard, "handoff"),
        "sensitive_data_violations": _check_failures(safety, "personal_data"),
        "unsupported_fact_flags": _check_failures(hard, "required_meaning"),
        "median_latency_ms": _percentile(latencies, 0.5),
        "p90_latency_ms": _percentile(latencies, 0.9),
        "average_tokens_per_second": _average(speeds),
        "gpu_vram_used_mib": max(
            (
                int(item["gpu_vram_used_mib"])
                for item in completed
                if isinstance(item.get("gpu_vram_used_mib"), int)
            ),
            default=None,
        ),
        "cuda_errors": sum(
            "cuda" in str(item.get("provider_error", "")).casefold()
            for item in results
        ),
        "hard_failure_types": dict(
            Counter(
                error
                for item in hard
                for error in item.get("errors", [])
            )
        ),
        "style_deviation_types": dict(
            Counter(
                error
                for item in soft
                for error in item.get("deviations", [])
            )
        ),
    }
    return metrics


def generate_stage3a_report(
    *,
    run_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    results = load_run_results(run_dir)
    metrics = aggregate_stage3a_metrics(results)
    decision = _decision(metrics)
    diagnostic = generate_stage3a_diagnostic(
        run_dir=run_dir,
        output_dir=run_dir / "diagnostic-pack",
    )
    summary = {
        "metrics": metrics,
        "technical_status": decision,
        "diagnostic_pack": diagnostic,
        "human_quality_claimed": False,
        "run": _load_json(run_dir / "run.json"),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_dir / "summary.json", summary)
    atomic_write_text(
        output_dir / "report.md",
        "# Stage 3A Adaptive Style\n\n"
        f"Technical status: `{decision}`.\n\n"
        "Soft style metrics are diagnostics, not a human-likeness score.\n\n"
        "```json\n"
        + json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n```\n",
    )
    return {
        "output": str(output_dir),
        "technical_status": decision,
        "diagnostic_pack": diagnostic["output"],
    }


def generate_stage3a_diagnostic(
    *,
    run_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    results = load_run_results(run_dir)
    selected = sorted(
        results,
        key=lambda item: (
            item.get("scenario_id") != "business-004",
            -len((item.get("soft_style_evaluation") or {}).get("deviations", [])),
            str(item.get("scenario_id")),
        ),
    )[:30]
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(output_dir / "README.md", "# Stage 3A Diagnostic Pack\n")
    atomic_write_text(
        output_dir / "adaptive-style-comparison.md",
        _diagnostic_markdown(selected),
    )
    mappings = {
        "lowercase-uppercase-examples.md": lambda item: True,
        "bubble-adaptation.md": lambda item: True,
        "length-adaptation.md": lambda item: True,
        "relationship-adaptation.md": lambda item: True,
        "style-fallbacks.md": lambda item: (
            item.get("adaptive_style_plan", {}).get("source") == "neutral_fallback"
        ),
        "hard-failures.md": lambda item: not item.get("hard_validation", {}).get(
            "valid", False
        ),
    }
    for filename, predicate in mappings.items():
        atomic_write_text(
            output_dir / filename,
            _diagnostic_markdown([item for item in selected if predicate(item)]),
        )
    atomic_write_text(
        output_dir / "dataset-prototype-status.md",
        "# Dataset Prototype Status\n\n"
        "Only confirmed human manual/edit/fix/imported text is eligible. "
        "Benchmark and accepted unchanged AI output are blocked.\n",
    )
    atomic_write_text(
        output_dir / "user-summary-template.md",
        "# Общая картина\n\n## Где стиль адаптируется правильно\n\n"
        "## Где стиль выглядит неестественно\n\n## Регистр и пунктуация\n\n"
        "## Длина и bubbles\n\n## Отношения и текущий ритм\n\n"
        "## Стоит ли начинать сбор человеческих примеров\n",
    )
    atomic_write_json(
        output_dir / "selection-summary.json",
        {
            "scenario_ids": [item.get("scenario_id") for item in selected],
            "business_004_included": any(
                item.get("scenario_id") == "business-004" for item in selected
            ),
        },
    )
    return {"output": str(output_dir), "selected_examples": len(selected)}


def _adaptive_contract(
    scenario: BenchmarkScenario,
    v1: ResponseContract,
) -> ResponseContractV2:
    extractor = StyleFeatureExtractor()
    incoming = scenario.incoming_messages
    emotional = (
        "toxic"
        if any(
            token in " ".join(incoming).casefold()
            for token in ("идиот", "тупой", "говно", "ненавиж")
        )
        else "neutral"
    )
    snapshot = extractor.conversation_snapshot(
        conversation_id=scenario.id,
        messages=incoming,
        emotional_context=emotional,
        topic=scenario.category,
    )
    empty = empty_style_statistics()
    return migrate_v1_to_v2(
        v1,
        resolver=AdaptiveStyleResolver(),
        agent_profile=AgentStyleProfile(scenario.agent_profile, empty),
        relationship_profile=RelationshipStyleProfile(
            scenario.agent_profile,
            str(scenario.relationship.get("type", "unknown")),
            None,
            empty,
        ),
        conversation=snapshot,
        relationship_context=scenario.relationship,
        known_facts=scenario.known_facts,
    )


def _load_v1_contracts(
    source: Path,
    scenarios: tuple[BenchmarkScenario, ...],
) -> dict[str, ResponseContract]:
    contracts = {}
    for scenario in scenarios:
        value = _load_json(source / "contracts" / f"{scenario.id}__r1.json")
        contracts[scenario.id] = ResponseContract.from_dict(dict(value["contract"]))
    return contracts


def _write_contract_snapshot(
    output: Path,
    contracts: dict[str, ResponseContractV2],
) -> str:
    rows = []
    for scenario_id, contract in sorted(contracts.items()):
        row = {
            "scenario_id": scenario_id,
            "response_contract_v2": contract.to_dict(),
            "contract_fingerprint": stable_fingerprint(contract.to_dict()),
            "source_stage26_snapshot": EXPECTED_STAGE26_SNAPSHOT,
        }
        rows.append(row)
        atomic_write_json(output / "contracts" / f"{scenario_id}__r1.json", row)
    return stable_fingerprint(rows)


def _select_scenarios(
    scenarios: tuple[BenchmarkScenario, ...],
    options: Stage3AOptions,
) -> tuple[BenchmarkScenario, ...]:
    values = [
        item
        for item in scenarios
        if not options.category or options.category in item.all_categories
    ]
    if options.scenario_limit is None:
        return tuple(values)
    preferred_ids = ["business-004", *_stage26_diagnostic_ids(options.contracts_from)]
    selected = []
    for scenario_id in preferred_ids:
        match = next(
            (
                item
                for item in values
                if item.id == scenario_id and item not in selected
            ),
            None,
        )
        if match:
            selected.append(match)
    selected.extend(item for item in values if item not in selected)
    return tuple(selected[: options.scenario_limit])


def _stage26_diagnostic_ids(source: Path) -> list[str]:
    path = source / "diagnostic-pack" / "selection-summary.json"
    if not path.is_file():
        return []
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    return [str(item) for item in value.get("scenario_ids", [])]


def _renderer_instructions(contract: ResponseContractV2) -> str:
    return (
        "Write only the final Russian Telegram response as strict JSON. SemanticPlan "
        "and SafetyConstraints are mandatory. AdaptiveStylePlan is a per-turn preference, "
        "not permission to invent facts or mirror aggression. Do not reconsider action. "
        "Do not explain reasoning, mention AI, repeat incoming text, add promises, prices, "
        "or unknown facts. If sensitive_data_strategy is refuse_collection, explicitly say "
        "not to send sensitive data; never ask the contact to send passport, card, password, "
        "or verification data anywhere. Handoff may only acknowledge and request a human; "
        "never claim that a transfer already happened. Return natural message bubbles only."
        "\nResponseContractV2:\n"
        + json.dumps(contract.to_dict(), ensure_ascii=False, separators=(",", ":"))
    )


def _renderer_schema(contract: ResponseContractV2) -> dict[str, Any]:
    action = contract.semantic.action
    min_messages = 1 if action in {"reply", "handoff"} else 0
    max_messages = (
        max(1, contract.style.preferred_bubble_range[1])
        if action in {"reply", "handoff"}
        else 0
    )
    critical_length = max(
        contract.style.preferred_character_range[1] * 4,
        contract.style.preferred_character_range[0] + 120,
    )
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
                "items": {"type": "string", "maxLength": critical_length},
                "minItems": min_messages,
                "maxItems": max_messages,
            },
            "reaction": (
                {"type": "string", "const": contract.semantic.reaction}
                if action == "reaction" and contract.semantic.reaction
                else {"type": "null"}
            ),
            "handoff_required": {
                "type": "boolean",
                "const": action == "handoff",
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    }


def _diagnostic_markdown(rows: list[dict[str, Any]]) -> str:
    lines = ["# Adaptive Style Comparison", ""]
    for item in rows:
        scenario = item.get("scenario", {})
        style = item.get("adaptive_style_plan", {})
        lines.extend(
            [
                f"## {item.get('scenario_id')}",
                f"- Conversation: `{json.dumps(scenario.get('conversation'), ensure_ascii=False)}`",
                f"- Relationship: `{json.dumps(scenario.get('relationship'), ensure_ascii=False)}`",
                f"- SemanticPlan: `{json.dumps(item.get('semantic_plan'), ensure_ascii=False)}`",
                f"- Style evidence: `{json.dumps(item.get('style_evidence'), ensure_ascii=False)}`",
                f"- AdaptiveStylePlan: `{json.dumps(style, ensure_ascii=False)}`",
                f"- Output: `{json.dumps((item.get('normalized_output') or {}).get('messages', []), ensure_ascii=False)}`",
                f"- Hard flags: `{json.dumps((item.get('hard_validation') or {}).get('errors', []), ensure_ascii=False)}`",
                f"- Soft deviations: `{json.dumps((item.get('soft_style_evaluation') or {}).get('deviations', []), ensure_ascii=False)}`",
                f"- Confidence: `{style.get('confidence')}`",
                f"- Reasons: `{json.dumps(style.get('reasons', []), ensure_ascii=False)}`",
                "",
            ]
        )
    return "\n".join(lines)


def _decision(metrics: dict[str, Any]) -> str:
    if metrics.get("completion_rate", 0) < 0.95:
        return "INFERENCE_BLOCKED"
    if (
        metrics.get("safety_validity", 0) >= 0.95
        and metrics.get("cuda_errors", 1) == 0
    ):
        return "READY_TO_COLLECT_HUMAN_EXAMPLES"
    return "STYLE_ARCHITECTURE_NEEDS_REVISION"


def _prepare_run(options: Stage3AOptions, meta: dict[str, Any]) -> None:
    options.output_dir.mkdir(parents=True, exist_ok=True)
    existing = _load_optional_json(options.output_dir / "run.json")
    if not existing:
        if options.resume:
            raise ValueError("--resume requested but run.json is missing")
        return
    if not options.resume:
        raise ValueError("run directory exists; use --resume")
    if existing.get("config_fingerprint") != meta["config_fingerprint"]:
        raise ValueError("run config fingerprint mismatch")
    meta["created_at"] = existing.get("created_at", meta["created_at"])


def _write_summary(output: Path, meta: dict[str, Any]) -> dict[str, Any]:
    results = load_run_results(output)
    summary = {
        "benchmark_fingerprint": meta["benchmark_fingerprint"],
        "run_fingerprint": meta["run_fingerprint"],
        "adaptive_contract_snapshot_fingerprint": meta[
            "adaptive_contract_snapshot_fingerprint"
        ],
        "metrics": aggregate_stage3a_metrics(results),
        "result_count": len(results),
    }
    atomic_write_json(output / "summary.json", summary)
    return summary


def _validate_model_manifest(manifest: dict[str, Any], profile: Any) -> None:
    for key, expected in (
        ("repository", profile.repository),
        ("resolved_revision", profile.revision),
        ("filename", profile.filename),
        ("quantization", profile.quantization),
    ):
        if manifest.get(key) != expected:
            raise ValueError(f"model manifest {key} mismatch")
    if not manifest.get("sha256") or not manifest.get("size_bytes"):
        raise ValueError("model manifest must contain hash and size")


def _add_usage(left: Usage, right: Usage) -> Usage:
    def add(first: int | None, second: int | None) -> int | None:
        return None if first is None and second is None else int(first or 0) + int(second or 0)

    return Usage(
        prompt_tokens=add(left.prompt_tokens, right.prompt_tokens),
        completion_tokens=add(left.completion_tokens, right.completion_tokens),
        total_tokens=add(left.total_tokens, right.total_tokens),
    )


def _incoming_messages(context: PolicyContext) -> tuple[str, ...]:
    return tuple(
        item.get("content", "")
        for item in context.conversation
        if item.get("role") in {"contact", "user"}
    )


def _check_failures(values: list[dict[str, Any]], key: str) -> int:
    return sum((item.get("checks") or {}).get(key) is False for item in values)


def _average(values: list[float]) -> float:
    return round(statistics.fmean(values), 6) if values else 0.0


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def _has_error(value: dict[str, Any]) -> bool:
    return bool(value.get("provider_error") or value.get("renderer_error"))


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def _load_optional_json(path: Path) -> dict[str, Any] | None:
    return _load_json(path) if path.is_file() else None


def _source_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        text=True,
        encoding="utf-8",
    ).strip()
