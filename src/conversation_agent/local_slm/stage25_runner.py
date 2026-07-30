"""Resumable Stage 2.5 response-contract benchmark runner."""

from __future__ import annotations

import json
import os
import subprocess
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from conversation_agent.local_slm.provider import OpenAICompatibleLocalProvider
from conversation_agent.local_slm.runtime_config import LocalLLMConfig
from conversation_agent.local_slm.stage2_dataset import (
    BenchmarkScenario,
    FrozenBenchmark,
    atomic_write_json,
    load_frozen_benchmark,
    stable_fingerprint,
)
from conversation_agent.local_slm.stage2_metrics import evaluate_candidate
from conversation_agent.local_slm.stage2_runner import load_run_results, machine_metadata
from conversation_agent.local_slm.stage25_metrics import aggregate_stage25_metrics
from conversation_agent.local_slm.stage25_pipeline import (
    ContractPolicy,
    ContractRenderer,
    GPTContractPolicy,
    LocalQwenContractRenderer,
    OpenAIContractRenderer,
    PolicyContext,
    PolicyPlan,
    Usage,
    execute_renderer_with_plan,
)
from conversation_agent.settings import load_env_file

PIPELINES = (
    "openai_direct",
    "local_direct",
    "gpt_policy_openai_renderer",
    "gpt_policy_local_renderer",
)
NEW_PIPELINES = (
    "gpt_policy_openai_renderer",
    "gpt_policy_local_renderer",
)
QUICK_COVERAGE = (
    "optional_no_reply",
    "potential_handoff",
    "appropriate_reaction",
    "incomplete_request",
    "hallucination_risk",
    "conflict",
    "friendly_chat",
    "formal_style",
    "multi_message_burst",
    "short_acknowledgement",
)


@dataclass(frozen=True)
class Stage25RunOptions:
    dataset_path: Path
    output_dir: Path
    pipelines: tuple[str, ...]
    baseline_dir: Path = Path(".runtime/benchmarks/stage2-system-v1")
    seed: int = 42
    scenario_limit: int | None = None
    category: str | None = None
    resume: bool = False
    retry_errors: bool = False
    gpu_required: bool = True


async def run_stage25_benchmark(
    options: Stage25RunOptions,
    *,
    policy_override: ContractPolicy | None = None,
    renderer_overrides: Mapping[str, ContractRenderer] | None = None,
    machine_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    load_env_file(Path(".env"))
    unknown = set(options.pipelines) - set(PIPELINES)
    if unknown:
        raise ValueError(f"unsupported Stage 2.5 pipelines: {sorted(unknown)}")
    benchmark = load_frozen_benchmark(options.dataset_path)
    scenarios = _select_scenarios(benchmark, options)
    source_commit = _source_commit()
    gpu_status = _load_gpu_status()
    if (
        options.gpu_required
        and "gpt_policy_local_renderer" in options.pipelines
        and not gpu_status.get("ready")
    ):
        raise RuntimeError(
            "GPU-required run refused: CUDA offload is not confirmed; "
            "run scripts\\local_slm\\check_gpu_offload.ps1"
        )
    config = {
        "benchmark_fingerprint": benchmark.fingerprint,
        "pipelines": list(options.pipelines),
        "scenario_ids": [scenario.id for scenario in scenarios],
        "seed": options.seed,
        "policy_version": "gpt_response_contract_v1",
        "renderer_version": "contract_renderer_v1",
        "gpu_required": options.gpu_required,
        "baseline_dir": str(options.baseline_dir.resolve()),
    }
    config_fingerprint = stable_fingerprint(config)
    run_fingerprint = stable_fingerprint(
        {
            "source_commit": source_commit,
            "config_fingerprint": config_fingerprint,
        }
    )
    run_meta: dict[str, Any] = {
        "benchmark_name": benchmark.manifest["name"],
        "benchmark_version": benchmark.manifest["version"],
        "benchmark_fingerprint": benchmark.fingerprint,
        "source_commit": source_commit,
        "run_fingerprint": run_fingerprint,
        "config_fingerprint": config_fingerprint,
        "pipelines_requested": list(options.pipelines),
        "scenario_count_selected": len(scenarios),
        "seed": options.seed,
        "policy_version": "gpt_response_contract_v1",
        "renderer_version": "contract_renderer_v1",
        "machine": machine_override or machine_metadata(),
        "gpu_status": gpu_status,
        "baseline_dir": str(options.baseline_dir),
        "created_at": datetime.now(UTC).isoformat(),
        "pipeline_status": {},
        "full_determinism_claimed": False,
    }
    _prepare_run_directory(options, run_meta)
    rubric = _load_rubric(options.dataset_path.parent / "rubric.yaml")
    policy, renderers, unavailable = await _build_components(
        options,
        policy_override=policy_override,
        renderer_overrides=renderer_overrides,
    )
    baseline = _load_baseline(options.baseline_dir, benchmark)
    for pipeline in options.pipelines:
        status = run_meta["pipeline_status"].setdefault(
            pipeline,
            {"completed": 0, "errors": 0, "status": "running"},
        )
        if pipeline in unavailable:
            status.update({"status": "unavailable", "reason": unavailable[pipeline]})
    atomic_write_json(options.output_dir / "run.json", run_meta)

    for scenario in scenarios:
        for repetition in (1,):
            plan: PolicyPlan | None = None
            plan_error: str | None = None
            if any(pipeline in options.pipelines for pipeline in NEW_PIPELINES):
                try:
                    plan = await _get_or_create_plan(
                        options,
                        scenario,
                        policy,
                    )
                except Exception as exc:  # noqa: BLE001
                    plan_error = _safe_error(exc)
            for pipeline in options.pipelines:
                result_path = _result_path(
                    options.output_dir,
                    pipeline,
                    scenario.id,
                    repetition,
                )
                existing = _load_optional_json(result_path)
                if existing is not None and options.resume:
                    has_error = _record_has_error(existing)
                    if not (options.retry_errors and has_error):
                        continue
                if pipeline in unavailable:
                    continue
                if pipeline in {"openai_direct", "local_direct"}:
                    record = _import_baseline_record(
                        scenario,
                        pipeline=pipeline,
                        baseline=baseline,
                        run_fingerprint=run_fingerprint,
                    )
                elif plan_error or plan is None:
                    record = _base_record(
                        scenario,
                        pipeline=pipeline,
                        run_fingerprint=run_fingerprint,
                    )
                    record.update(
                        {
                            "contract_validation": {
                                "valid": False,
                                "errors": [plan_error or "policy_plan_missing"],
                            },
                            "contract_error": plan_error or "policy_plan_missing",
                        }
                    )
                else:
                    record = await _run_new_pipeline(
                        scenario,
                        pipeline=pipeline,
                        plan=plan,
                        renderer=renderers[pipeline],
                        rubric=rubric,
                        run_fingerprint=run_fingerprint,
                        gpu_status=(
                            gpu_status
                            if pipeline == "gpt_policy_local_renderer"
                            else {}
                        ),
                    )
                atomic_write_json(result_path, record)
                _write_summary(options.output_dir, run_meta)
    _refresh_status(run_meta, options.output_dir)
    atomic_write_json(options.output_dir / "run.json", run_meta)
    return _write_summary(options.output_dir, run_meta)


async def _get_or_create_plan(
    options: Stage25RunOptions,
    scenario: BenchmarkScenario,
    policy: ContractPolicy | None,
) -> PolicyPlan:
    path = options.output_dir / "contracts" / f"{scenario.id}__r1.json"
    existing = _load_optional_json(path)
    if existing is not None and options.resume and not (
        options.retry_errors and existing.get("contract_error")
    ):
        return _plan_from_record(existing)
    if policy is None:
        raise RuntimeError("GPT policy is unavailable")
    context = PolicyContext.from_scenario(scenario)
    plan = await policy.plan(context)
    value = {
        "scenario_id": scenario.id,
        "contract": plan.contract.to_dict(),
        "contract_validation": {"valid": True, "errors": []},
        "policy_latency_ms": plan.latency_ms,
        "policy_model": plan.model,
        "policy_raw_output": plan.raw_output,
        "policy_usage": plan.usage.to_dict(),
        "created_at": datetime.now(UTC).isoformat(),
    }
    atomic_write_json(path, value)
    return plan


async def _run_new_pipeline(
    scenario: BenchmarkScenario,
    *,
    pipeline: str,
    plan: PolicyPlan,
    renderer: ContractRenderer,
    rubric: dict[str, Any],
    run_fingerprint: str,
    gpu_status: dict[str, Any],
) -> dict[str, Any]:
    record = _base_record(
        scenario,
        pipeline=pipeline,
        run_fingerprint=run_fingerprint,
    )
    try:
        result = await execute_renderer_with_plan(
            plan=plan,
            renderer=renderer,
            context=PolicyContext.from_scenario(scenario),
        )
    except Exception as exc:  # noqa: BLE001
        record.update(
            {
                "contract": plan.contract.to_dict(),
                "contract_validation": {"valid": True, "errors": []},
                "provider_error": _safe_error(exc),
                "error_type": "provider_error",
            }
        )
        return record
    normalized = result.output.to_dict()
    renderer_validation = result.renderer_validation.to_dict()
    stage2_validation = {
        "valid": result.renderer_validation.valid,
        "errors": list(result.renderer_validation.errors),
    }
    evaluation = evaluate_candidate(
        scenario,
        normalized=normalized,
        validation=stage2_validation,
        rubric=rubric,
    )
    record.update(
        {
            "contract": result.contract.to_dict(),
            "contract_validation": {"valid": True, "errors": []},
            "renderer_validation": renderer_validation,
            "normalized_output": normalized,
            "validation": stage2_validation,
            "automatic_evaluation": evaluation,
            "policy_model": result.policy_model,
            "renderer_model": result.renderer_model,
            "policy_latency_ms": result.policy_latency_ms,
            "renderer_latency_ms": result.renderer_latency_ms,
            "total_latency_ms": result.total_latency_ms,
            "latency_ms": result.total_latency_ms,
            "renderer_retry_count": result.renderer_retry_count,
            "retry_count": result.renderer_retry_count,
            "policy_usage": result.policy_usage.to_dict(),
            "renderer_usage": result.renderer_usage.to_dict(),
            "prompt_tokens": result.renderer_usage.prompt_tokens,
            "completion_tokens": result.renderer_usage.completion_tokens,
            "total_tokens": result.renderer_usage.total_tokens,
            "tokens_per_second": result.output.tokens_per_second,
            "gpu_vram_used_mib": gpu_status.get("vram_used_mib"),
            "gpu_vram_delta_mib": gpu_status.get("vram_delta_mib"),
            "gpu_offloaded_layers": gpu_status.get("offloaded_layers"),
        }
    )
    if not result.renderer_validation.valid:
        record["renderer_error"] = (
            "renderer_validation:" + ",".join(result.renderer_validation.errors)
        )
        record["error_type"] = "renderer_validation"
    return record


def _import_baseline_record(
    scenario: BenchmarkScenario,
    *,
    pipeline: str,
    baseline: dict[tuple[str, str], dict[str, Any]],
    run_fingerprint: str,
) -> dict[str, Any]:
    provider = "openai_gpt4o_mini" if pipeline == "openai_direct" else "local_qwen"
    source = baseline.get((scenario.id, provider))
    if source is None:
        record = _base_record(
            scenario,
            pipeline=pipeline,
            run_fingerprint=run_fingerprint,
        )
        record["provider_error"] = "baseline_result_missing"
        record["error_type"] = "baseline_import"
        return record
    record = dict(source)
    record.update(
        {
            "pipeline": pipeline,
            "provider": pipeline,
            "run_fingerprint": run_fingerprint,
            "baseline_reference": True,
            "baseline_source_run_fingerprint": source.get("run_fingerprint"),
            "policy_latency_ms": 0,
            "renderer_latency_ms": source.get("latency_ms"),
            "total_latency_ms": source.get("latency_ms"),
            "contract_validation": None,
            "renderer_validation": source.get("validation"),
        }
    )
    return record


def _base_record(
    scenario: BenchmarkScenario,
    *,
    pipeline: str,
    run_fingerprint: str,
) -> dict[str, Any]:
    return {
        "scenario_id": scenario.id,
        "category": scenario.category,
        "tags": list(scenario.tags),
        "pipeline": pipeline,
        "provider": pipeline,
        "run_fingerprint": run_fingerprint,
        "expected_actions": list(scenario.expected_actions),
        "scenario": scenario.to_dict(),
        "created_at": datetime.now(UTC).isoformat(),
    }


async def _build_components(
    options: Stage25RunOptions,
    *,
    policy_override: ContractPolicy | None,
    renderer_overrides: Mapping[str, ContractRenderer] | None,
) -> tuple[
    ContractPolicy | None,
    dict[str, ContractRenderer],
    dict[str, str],
]:
    if policy_override is not None:
        renderers = dict(renderer_overrides or {})
        unavailable = {
            pipeline: "renderer override missing"
            for pipeline in NEW_PIPELINES
            if pipeline in options.pipelines and pipeline not in renderers
        }
        return policy_override, renderers, unavailable
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    needs_policy = any(pipeline in options.pipelines for pipeline in NEW_PIPELINES)
    unavailable: dict[str, str] = {}
    policy: ContractPolicy | None = None
    if needs_policy and not api_key:
        for pipeline in NEW_PIPELINES:
            if pipeline in options.pipelines:
                unavailable[pipeline] = "OPENAI_API_KEY is missing"
    elif needs_policy:
        policy = GPTContractPolicy(
            api_key=api_key,
            model=os.getenv("STAGE25_POLICY_MODEL", "gpt-4o-mini"),
            timeout_seconds=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "30")),
        )
    renderers: dict[str, ContractRenderer] = {}
    if "gpt_policy_openai_renderer" in options.pipelines and api_key:
        renderers["gpt_policy_openai_renderer"] = OpenAIContractRenderer(
            api_key=api_key,
            model=os.getenv("STAGE25_RENDERER_MODEL", "gpt-4o-mini"),
            timeout_seconds=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "30")),
        )
    if "gpt_policy_local_renderer" in options.pipelines:
        local = OpenAICompatibleLocalProvider.from_config(LocalLLMConfig.from_env())
        if await local.health_check():
            renderers["gpt_policy_local_renderer"] = LocalQwenContractRenderer(local)
        else:
            unavailable["gpt_policy_local_renderer"] = (
                f"local server unavailable at {local.base_url}"
            )
    return policy, renderers, unavailable


def _load_baseline(
    baseline_dir: Path,
    benchmark: FrozenBenchmark,
) -> dict[tuple[str, str], dict[str, Any]]:
    if not baseline_dir.is_dir():
        return {}
    meta = _load_optional_json(baseline_dir / "run.json") or {}
    if meta.get("benchmark_fingerprint") != benchmark.fingerprint:
        raise ValueError("baseline benchmark fingerprint mismatch")
    return {
        (str(item.get("scenario_id")), str(item.get("provider"))): item
        for item in load_run_results(baseline_dir)
    }


def _select_scenarios(
    benchmark: FrozenBenchmark,
    options: Stage25RunOptions,
) -> tuple[BenchmarkScenario, ...]:
    values = list(benchmark.scenarios)
    if options.category:
        values = [
            scenario
            for scenario in values
            if options.category in scenario.all_categories
        ]
    if options.scenario_limit is None:
        return tuple(values)
    if options.scenario_limit < 1:
        raise ValueError("--scenario-limit must be positive")
    if options.category or options.scenario_limit >= len(values):
        return tuple(values[: options.scenario_limit])
    selected: list[BenchmarkScenario] = []
    for category in QUICK_COVERAGE:
        match = next(
            (
                scenario
                for scenario in values
                if scenario not in selected and category in scenario.all_categories
            ),
            None,
        )
        if match is not None:
            selected.append(match)
    selected.extend(
        scenario for scenario in values if scenario not in selected
    )
    return tuple(selected[: options.scenario_limit])


def _prepare_run_directory(
    options: Stage25RunOptions,
    run_meta: dict[str, Any],
) -> None:
    options.output_dir.mkdir(parents=True, exist_ok=True)
    existing = _load_optional_json(options.output_dir / "run.json")
    if existing is None:
        if options.resume:
            raise ValueError("--resume requested but run.json does not exist")
        return
    if existing.get("benchmark_fingerprint") != run_meta["benchmark_fingerprint"]:
        raise ValueError("run directory contains a different benchmark fingerprint")
    if existing.get("config_fingerprint") != run_meta["config_fingerprint"]:
        raise ValueError("run directory contains a different config fingerprint")
    if not options.resume:
        raise ValueError("run directory already exists; use --resume")
    run_meta["created_at"] = existing.get("created_at", run_meta["created_at"])
    run_meta["pipeline_status"] = existing.get("pipeline_status", {})


def _write_summary(output_dir: Path, run_meta: dict[str, Any]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in load_run_results(output_dir):
        pipeline = str(item.get("pipeline", "unknown"))
        grouped[pipeline].append(item)
    summary = {
        "benchmark_fingerprint": run_meta["benchmark_fingerprint"],
        "run_fingerprint": run_meta["run_fingerprint"],
        "pipeline_status": run_meta["pipeline_status"],
        "metrics": {
            pipeline: aggregate_stage25_metrics(items)
            for pipeline, items in sorted(grouped.items())
        },
        "result_count": sum(len(items) for items in grouped.values()),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    atomic_write_json(output_dir / "summary.json", summary)
    return summary


def _refresh_status(run_meta: dict[str, Any], output_dir: Path) -> None:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in load_run_results(output_dir):
        grouped[str(item.get("pipeline", "unknown"))].append(item)
    for pipeline in run_meta["pipelines_requested"]:
        status = run_meta["pipeline_status"].setdefault(pipeline, {})
        if status.get("status") == "unavailable":
            continue
        values = grouped.get(pipeline, [])
        status.update(
            {
                "completed": sum(not _record_has_error(item) for item in values),
                "errors": sum(_record_has_error(item) for item in values),
                "status": "completed",
            }
        )


def _plan_from_record(value: dict[str, Any]) -> PolicyPlan:
    from conversation_agent.local_slm.stage25_contract import ResponseContract

    return PolicyPlan(
        contract=ResponseContract.from_dict(dict(value["contract"])),
        latency_ms=int(value.get("policy_latency_ms", 0)),
        model=str(value.get("policy_model", "unknown")),
        raw_output=str(value.get("policy_raw_output", "")),
        usage=Usage(**dict(value.get("policy_usage", {}))),
    )


def _result_path(
    output_dir: Path,
    pipeline: str,
    scenario_id: str,
    repetition: int,
) -> Path:
    return output_dir / "results" / pipeline / f"{scenario_id}__r{repetition}.json"


def _record_has_error(value: dict[str, Any]) -> bool:
    return bool(
        value.get("provider_error")
        or value.get("contract_error")
        or value.get("renderer_error")
    )


def _load_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    return value if isinstance(value, dict) else None


def _load_rubric(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _load_gpu_status() -> dict[str, Any]:
    path = Path(".runtime/local_slm/gpu-status.json")
    return _load_optional_json(path) or {
        "ready": False,
        "reason": "gpu-status.json is missing",
    }


def _source_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            encoding="utf-8",
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _safe_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:2000]
