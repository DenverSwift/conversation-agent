"""Renderer-only Ruadapt Qwen3 4B qualification."""

from __future__ import annotations

import csv
import json
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
from conversation_agent.local_slm.stage2_metrics import evaluate_candidate
from conversation_agent.local_slm.stage2_runner import load_run_results, machine_metadata
from conversation_agent.local_slm.stage25_contract import ResponseContract
from conversation_agent.local_slm.stage25_metrics import aggregate_stage25_metrics
from conversation_agent.local_slm.stage25_pipeline import (
    LocalQwenContractRenderer,
    PolicyContext,
    PolicyPlan,
    Usage,
    execute_renderer_with_plan,
)

PIPELINE = "gpt_policy_ruadapt4b_renderer"
PROMPT_VERSION = "ruadapt_contract_renderer_v1"
QUICK_COVERAGE = (
    "repeated_question",
    "hallucination_risk",
    "missing_information",
    "no_price_invention",
    "no_promise",
    "human_request",
    "conflict",
    "emotional_support",
    "friendly_chat",
    "humor",
    "irony",
    "correction",
    "topic_change",
    "multi_message_burst",
    "incomplete_request",
    "one_short_clarifying_question",
    "new_contact_clarity",
    "known_contact_short_reply",
    "formal_style",
    "informal_style",
    "multiple_bubbles",
    "optional_no_reply",
    "appropriate_reaction",
)


@dataclass(frozen=True)
class Stage26Options:
    dataset_path: Path
    renderer: str
    contracts_from: Path
    baseline_dir: Path
    output_dir: Path
    seed: int = 42
    scenario_limit: int | None = None
    category: str | None = None
    resume: bool = False
    retry_errors: bool = False
    gpu_required: bool = True


async def run_stage26(
    options: Stage26Options,
    *,
    renderer_override: Any | None = None,
    machine_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = get_renderer_profile(options.renderer)
    if profile.name == "qwen3_06b_baseline":
        raise ValueError("Stage 2.6 requires an explicit Ruadapt renderer profile")
    benchmark = load_frozen_benchmark(options.dataset_path)
    scenarios = _select_scenarios(benchmark.scenarios, options)
    model_manifest = _load_json(Path(".runtime/local_slm/ruadapt-model.json"))
    _validate_model_manifest(model_manifest, profile)
    gpu_status = _load_optional_json(
        Path(".runtime/local_slm/ruadapt-gpu-status.json")
    ) or {"ready": False, "reason": "Ruadapt GPU status is missing"}
    if options.gpu_required and not gpu_status.get("ready"):
        raise RuntimeError(f"GPU-required Stage 2.6 run refused: {gpu_status.get('reason')}")
    source_meta = _load_json(options.contracts_from / "run.json")
    if source_meta.get("benchmark_fingerprint") != benchmark.fingerprint:
        raise ValueError("Stage 2.5 contract benchmark fingerprint mismatch")
    plans = _load_contract_plans(options.contracts_from, scenarios)
    snapshot = _snapshot_contracts(options, plans, source_meta)
    config = {
        "benchmark_fingerprint": benchmark.fingerprint,
        "renderer": profile.to_dict(),
        "contract_snapshot_fingerprint": snapshot["fingerprint"],
        "scenario_ids": [scenario.id for scenario in scenarios],
        "seed": options.seed,
        "prompt_version": PROMPT_VERSION,
        "gpu_required": options.gpu_required,
    }
    config_fingerprint = stable_fingerprint(config)
    run_meta = {
        "benchmark_fingerprint": benchmark.fingerprint,
        "source_commit": _source_commit(),
        "config_fingerprint": config_fingerprint,
        "run_fingerprint": stable_fingerprint(
            {"source_commit": _source_commit(), "config": config_fingerprint}
        ),
        "renderer_profile": profile.to_dict(),
        "model_manifest": model_manifest,
        "gpu_status": gpu_status,
        "contract_snapshot_fingerprint": snapshot["fingerprint"],
        "contracts_from": str(options.contracts_from),
        "baseline": str(options.baseline_dir),
        "scenario_count_selected": len(scenarios),
        "seed": options.seed,
        "prompt_version": PROMPT_VERSION,
        "machine": machine_override or machine_metadata(),
        "created_at": datetime.now(UTC).isoformat(),
    }
    _prepare_run(options, run_meta)
    atomic_write_json(options.output_dir / "run.json", run_meta)
    rubric = _load_json(options.dataset_path.parent / "rubric.yaml")
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
            raise RuntimeError(f"Ruadapt endpoint unavailable at {profile.base_url}")
        renderer = LocalQwenContractRenderer(provider, no_think_prefix=False)
    else:
        renderer = renderer_override
    for scenario in scenarios:
        path = options.output_dir / "results" / PIPELINE / f"{scenario.id}__r1.json"
        existing = _load_optional_json(path)
        if (
            existing is not None
            and options.resume
            and not (options.retry_errors and _has_error(existing))
        ):
            continue
        plan = plans[scenario.id]
        record = _base_record(scenario, run_meta)
        try:
            result = await execute_renderer_with_plan(
                plan=plan,
                renderer=renderer,
                context=PolicyContext.from_scenario(scenario),
            )
            output = result.output.to_dict()
            validation = result.renderer_validation.to_dict()
            evaluation = evaluate_candidate(
                scenario,
                normalized=output,
                validation={"valid": result.renderer_validation.valid},
                rubric=rubric,
            )
            text = " ".join(output.get("messages", []))
            record.update(
                {
                    "contract": result.contract.to_dict(),
                    "contract_validation": {"valid": True, "errors": []},
                    "renderer_validation": validation,
                    "validation": {
                        "valid": result.renderer_validation.valid,
                        "errors": list(result.renderer_validation.errors),
                    },
                    "schema_valid": True,
                    "normalized_output": output,
                    "automatic_evaluation": evaluation,
                    "renderer_model": result.renderer_model,
                    "renderer_latency_ms": result.renderer_latency_ms,
                    "total_latency_ms": result.renderer_latency_ms,
                    "latency_ms": result.renderer_latency_ms,
                    "renderer_retry_count": result.renderer_retry_count,
                    "retry_count": result.renderer_retry_count,
                    "renderer_usage": result.renderer_usage.to_dict(),
                    "prompt_tokens": result.renderer_usage.prompt_tokens,
                    "completion_tokens": result.renderer_usage.completion_tokens,
                    "total_tokens": result.renderer_usage.total_tokens,
                    "tokens_per_second": result.output.tokens_per_second,
                    "truncated_output": _truncated(text, result.renderer_usage),
                    "incomplete_sentence": _incomplete_sentence(text),
                    "gpu_vram_used_mib": gpu_status.get("vram_used_mib"),
                    "gpu_vram_delta_mib": gpu_status.get("vram_delta_mib"),
                    "gpu_offloaded_layers": gpu_status.get("offloaded_layers"),
                }
            )
            if not result.renderer_validation.valid:
                record["renderer_error"] = "renderer_validation:" + ",".join(
                    result.renderer_validation.errors
                )
        except Exception as exc:  # noqa: BLE001
            record["provider_error"] = f"{type(exc).__name__}: {exc}"[:2000]
        atomic_write_json(path, record)
        _write_summary(options.output_dir, run_meta)
    return _write_summary(options.output_dir, run_meta)


def generate_stage26_report(
    *,
    run_dir: Path,
    baseline_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    results = load_run_results(run_dir)
    metrics = aggregate_stage26_metrics(results)
    baselines = _baseline_results(baseline_dir)
    baseline_metrics = {
        name: aggregate_stage26_metrics(values)
        for name, values in baselines.items()
    }
    decision = _decision(metrics)
    diagnostic = generate_stage26_diagnostic(
        run_dir=run_dir,
        baseline_dir=baseline_dir,
        output_dir=run_dir / "diagnostic-pack",
        max_examples=30,
    )
    summary = {
        "metrics": metrics,
        "baseline_metrics": baseline_metrics,
        "technical_decision": decision,
        "user_qualitative_review_required": True,
        "production_ready": False,
        "diagnostic_pack": diagnostic,
        "run": _load_json(run_dir / "run.json"),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_dir / "summary.json", summary)
    atomic_write_text(output_dir / "report.md", _report_markdown(summary))
    _write_metrics_csv(output_dir / "renderer_metrics.csv", metrics, baseline_metrics)
    return {
        "output": str(output_dir),
        "technical_decision": decision,
        "diagnostic_pack": diagnostic["output"],
    }


def aggregate_stage26_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = aggregate_stage25_metrics(results)
    validations = [
        item.get("renderer_validation", {})
        for item in results
        if isinstance(item.get("renderer_validation"), dict)
    ]
    analyses = [
        finding
        for validation in validations
        for finding in validation.get("copy_analysis", [])
    ]
    total = len(results)
    schema_valid = sum(
        bool(item.get("schema_valid"))
        or (
            not item.get("provider_error")
            and isinstance(item.get("normalized_output"), dict)
        )
        for item in results
    )
    metrics.update(
        {
            "schema_validity_rate": _rate(schema_valid, total),
            "exact_incoming_copy_rate": _rate(
                sum(
                    any(
                        finding.get("rule_id") == "exact_normalized_copy"
                        for finding in validation.get("copy_analysis", [])
                    )
                    for validation in validations
                ),
                total,
            ),
            "near_copy_rate": _rate(
                sum(
                    any(
                        finding.get("rule_id")
                        in {"near_copy", "partial_incoming_copy"}
                        for finding in validation.get("copy_analysis", [])
                    )
                    for validation in validations
                ),
                total,
            ),
            "copy_rule_counts": dict(
                Counter(str(item.get("rule_id")) for item in analyses)
            ),
            "greeting_violation_rate": _violation_rate(validations, "greeting", total),
            "emoji_violation_rate": _violation_rate(validations, "emoji", total),
            "truncated_output_rate": _rate(
                sum(bool(item.get("truncated_output")) for item in results),
                total,
            ),
            "incomplete_sentence_rate": _rate(
                sum(bool(item.get("incomplete_sentence")) for item in results),
                total,
            ),
            "cuda_errors": sum(
                "cuda" in str(item.get("provider_error", "")).casefold()
                for item in results
            ),
        }
    )
    return metrics


def generate_stage26_diagnostic(
    *,
    run_dir: Path,
    baseline_dir: Path,
    output_dir: Path,
    max_examples: int = 30,
) -> dict[str, Any]:
    current = {
        str(item.get("scenario_id")): item for item in load_run_results(run_dir)
    }
    baselines = _baseline_results(baseline_dir)
    qwen = {str(item.get("scenario_id")): item for item in baselines["qwen06"]}
    gpt = {str(item.get("scenario_id")): item for item in baselines["openai"]}
    ranked = sorted(
        current.values(),
        key=lambda item: (
            -len((item.get("renderer_validation") or {}).get("errors", [])),
            str(item.get("scenario_id")),
        ),
    )[:max_examples]
    rows = [
        _diagnostic_row(item, qwen.get(str(item["scenario_id"])), gpt.get(str(item["scenario_id"])))
        for item in ranked
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        output_dir / "selection-summary.json",
        {
            "selected_scenarios": len(rows),
            "duplicate_scenario_ids": len(rows)
            - len({row["scenario_id"] for row in rows}),
            "scenario_ids": [row["scenario_id"] for row in rows],
        },
    )
    atomic_write_text(output_dir / "README.md", "# Stage 2.6 Diagnostic Pack\n")
    atomic_write_text(output_dir / "comparison.md", _comparison_markdown(rows))
    atomic_write_text(output_dir / "ruadapt-only.md", _single_markdown(rows, "ruadapt"))
    failures = [row for row in rows if row["ruadapt"]["flags"]]
    best = [row for row in rows if not row["ruadapt"]["flags"]]
    atomic_write_text(output_dir / "failures.md", _single_markdown(failures, "ruadapt"))
    atomic_write_text(output_dir / "best-examples.md", _single_markdown(best, "ruadapt"))
    atomic_write_text(
        output_dir / "user-summary-template.md",
        "# Общая картина\n\n## Где Ruadapt лучше Qwen 0.6B\n\n"
        "## Где Ruadapt всё ещё тупит\n\n## Насколько естественный русский\n\n"
        "## Повторяет ли входящие сообщения\n\n## Галлюцинации и обещания\n\n"
        "## Слишком длинные или слишком формальные ответы\n\n"
        "## Стоит ли обучать эту базу\n",
    )
    return {"output": str(output_dir), "selected_examples": len(rows)}


def _snapshot_contracts(
    options: Stage26Options,
    plans: dict[str, PolicyPlan],
    source_meta: dict[str, Any],
) -> dict[str, Any]:
    rows = []
    target = options.output_dir / "contracts"
    for scenario_id, plan in sorted(plans.items()):
        contract = plan.contract.to_dict()
        fingerprint = stable_fingerprint(contract)
        row = {
            "scenario_id": scenario_id,
            "contract": contract,
            "policy_model": plan.model,
            "policy_prompt_version": source_meta.get("policy_version"),
            "source_stage25_run": str(options.contracts_from),
            "contract_fingerprint": fingerprint,
        }
        rows.append(row)
        atomic_write_json(target / f"{scenario_id}__r1.json", row)
    return {"fingerprint": stable_fingerprint(rows), "count": len(rows)}


def _load_contract_plans(
    source: Path,
    scenarios: tuple[BenchmarkScenario, ...],
) -> dict[str, PolicyPlan]:
    values: dict[str, PolicyPlan] = {}
    for scenario in scenarios:
        value = _load_json(source / "contracts" / f"{scenario.id}__r1.json")
        values[scenario.id] = PolicyPlan(
            contract=ResponseContract.from_dict(dict(value["contract"])),
            latency_ms=0,
            model=str(value.get("policy_model", "gpt-4o-mini")),
            raw_output="",
            usage=Usage(),
        )
    return values


def _select_scenarios(
    scenarios: tuple[BenchmarkScenario, ...],
    options: Stage26Options,
) -> tuple[BenchmarkScenario, ...]:
    values = list(scenarios)
    if options.category:
        values = [item for item in values if options.category in item.all_categories]
    if options.scenario_limit is None:
        return tuple(values)
    diagnostic_ids = _stage25_diagnostic_ids(options.contracts_from)
    selected: list[BenchmarkScenario] = []
    for category in QUICK_COVERAGE:
        match = next(
            (
                item
                for item in values
                if item not in selected
                and category in item.all_categories
                and (not diagnostic_ids or item.id in diagnostic_ids)
            ),
            None,
        ) or next(
            (
                item
                for item in values
                if item not in selected and category in item.all_categories
            ),
            None,
        )
        if match:
            selected.append(match)
    selected.extend(
        item
        for item in values
        if item.id in diagnostic_ids and item not in selected
    )
    selected.extend(item for item in values if item not in selected)
    return tuple(selected[: options.scenario_limit])


def _stage25_diagnostic_ids(source: Path) -> set[str]:
    candidates = (
        source / "report" / "diagnostic-pack" / "examples.json",
        source / "diagnostic-pack" / "examples.json",
    )
    for path in candidates:
        if path.is_file():
            value = json.loads(path.read_text(encoding="utf-8"))
            return {
                str(item.get("scenario_id"))
                for item in value
                if isinstance(item, dict)
            }
    return set()


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
        raise ValueError("model manifest must record file hash and size")


def _prepare_run(options: Stage26Options, meta: dict[str, Any]) -> None:
    options.output_dir.mkdir(parents=True, exist_ok=True)
    existing = _load_optional_json(options.output_dir / "run.json")
    if existing is None:
        if options.resume:
            raise ValueError("--resume requested but run.json does not exist")
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
        "contract_snapshot_fingerprint": meta["contract_snapshot_fingerprint"],
        "metrics": aggregate_stage26_metrics(results),
        "result_count": len(results),
    }
    atomic_write_json(output / "summary.json", summary)
    return summary


def _base_record(
    scenario: BenchmarkScenario,
    meta: dict[str, Any],
) -> dict[str, Any]:
    return {
        "scenario_id": scenario.id,
        "category": scenario.category,
        "tags": list(scenario.tags),
        "pipeline": PIPELINE,
        "provider": PIPELINE,
        "expected_actions": list(scenario.expected_actions),
        "scenario": scenario.to_dict(),
        "run_fingerprint": meta["run_fingerprint"],
        "created_at": datetime.now(UTC).isoformat(),
    }


def _baseline_results(path: Path) -> dict[str, list[dict[str, Any]]]:
    results = load_run_results(path)
    return {
        "qwen06": [
            item
            for item in results
            if item.get("pipeline") == "gpt_policy_local_renderer"
        ],
        "openai": [
            item
            for item in results
            if item.get("pipeline") == "gpt_policy_openai_renderer"
        ],
    }


def _decision(metrics: dict[str, Any]) -> str:
    if metrics.get("completion_rate", 0) < 0.95:
        return "INFERENCE_BLOCKED"
    if (
        metrics.get("completion_rate", 0) >= 0.98
        and (metrics.get("renderer_validity") or 0) >= 0.85
        and metrics.get("repeated_question_rate", 1) <= 0.05
        and metrics.get("forbidden_claim_rate", 1) <= 0.02
        and (metrics.get("required_fact_coverage") or 0) >= 0.95
        and (metrics.get("bubble_count_compliance") or 0) >= 0.9
        and (metrics.get("total_character_compliance") or 0) >= 0.95
    ):
        return "READY_FOR_DATASET_PROTOTYPE"
    if (
        (metrics.get("renderer_validity") or 0) < 0.7
        or metrics.get("repeated_question_rate", 1) > 0.15
    ):
        return "TEST_ANOTHER_BASE_MODEL"
    return "RENDERER_ARCHITECTURE_PROBLEM"


def _diagnostic_row(
    current: dict[str, Any],
    qwen: dict[str, Any] | None,
    gpt: dict[str, Any] | None,
) -> dict[str, Any]:
    scenario = current.get("scenario", {})
    return {
        "scenario_id": current.get("scenario_id"),
        "category": current.get("category"),
        "relationship": scenario.get("relationship"),
        "conversation": scenario.get("conversation"),
        "known_facts": scenario.get("known_facts"),
        "contract": current.get("contract"),
        "qwen06": _candidate(qwen),
        "ruadapt": _candidate(current),
        "openai": _candidate(gpt),
        "reasons": (current.get("renderer_validation") or {}).get("errors", [])
        or ["representative_control"],
    }


def _candidate(item: dict[str, Any] | None) -> dict[str, Any]:
    if not item:
        return {"messages": [], "flags": ["missing"], "retry": 0}
    return {
        "messages": (item.get("normalized_output") or {}).get("messages", []),
        "flags": (item.get("renderer_validation") or item.get("validation") or {}).get(
            "errors", []
        ),
        "retry": item.get("renderer_retry_count", item.get("retry_count", 0)),
    }


def _comparison_markdown(rows: list[dict[str, Any]]) -> str:
    lines = ["# Ruadapt Renderer Comparison", ""]
    for row in rows:
        lines.extend(
            [
                f"## {row['scenario_id']} ({row['category']})",
                f"- Relationship: `{json.dumps(row['relationship'], ensure_ascii=False)}`",
                f"- Conversation: `{json.dumps(row['conversation'], ensure_ascii=False)}`",
                f"- Known facts: `{json.dumps(row['known_facts'], ensure_ascii=False)}`",
                f"- ResponseContract: `{json.dumps(row['contract'], ensure_ascii=False)}`",
                f"- Qwen3-0.6B: `{json.dumps(row['qwen06'], ensure_ascii=False)}`",
                f"- RuadaptQwen3-4B: `{json.dumps(row['ruadapt'], ensure_ascii=False)}`",
                f"- GPT renderer: `{json.dumps(row['openai'], ensure_ascii=False)}`",
                f"- Selection reasons: `{json.dumps(row['reasons'], ensure_ascii=False)}`",
                "",
            ]
        )
    return "\n".join(lines)


def _single_markdown(rows: list[dict[str, Any]], key: str) -> str:
    lines = [f"# {key} examples", ""]
    for row in rows:
        lines.extend(
            [
                f"## {row['scenario_id']}",
                f"`{json.dumps(row[key], ensure_ascii=False)}`",
                "",
            ]
        )
    return "\n".join(lines)


def _report_markdown(summary: dict[str, Any]) -> str:
    return (
        "# Stage 2.6 Ruadapt Qwen3 4B Qualification\n\n"
        f"Technical decision: `{summary['technical_decision']}`.\n\n"
        "This is not a production-readiness or human-quality score.\n\n"
        "```json\n"
        + json.dumps(summary["metrics"], ensure_ascii=False, indent=2, sort_keys=True)
        + "\n```\n"
    )


def _write_metrics_csv(
    path: Path,
    current: dict[str, Any],
    baselines: dict[str, dict[str, Any]],
) -> None:
    rows = [
        {"renderer": "ruadapt4b", **current},
        *(
            {"renderer": name, **metrics}
            for name, metrics in sorted(baselines.items())
        ),
    ]
    fields = sorted({key for row in rows for key in row})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (dict, list))
                    else value
                    for key, value in row.items()
                }
            )


def _truncated(text: str, usage: Usage) -> bool:
    return bool(text and usage.completion_tokens and usage.completion_tokens >= 190)


def _incomplete_sentence(text: str) -> bool:
    return bool(text and (text.rstrip().endswith((",", ":", "-", "—"))))


def _violation_rate(
    validations: list[dict[str, Any]],
    key: str,
    total: int,
) -> float:
    return _rate(
        sum(
            (validation.get("contract_compliance") or {}).get(key) is False
            for validation in validations
        ),
        total,
    )


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


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
