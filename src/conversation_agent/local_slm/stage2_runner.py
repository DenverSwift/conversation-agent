"""Resumable real-provider runner for the Stage 2 baseline benchmark."""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from conversation_agent.agent.prompt_builder import build_instructions
from conversation_agent.local_slm.context import LocalContextBuilder
from conversation_agent.local_slm.models import (
    Action,
    DialoguePolicyInput,
    GenerationRequest,
    GenerationResult,
)
from conversation_agent.local_slm.policy import RuleBasedDialoguePolicy, safe_policy_decision
from conversation_agent.local_slm.provider import (
    OpenAICompatibleLocalProvider,
    generation_system_prompt,
)
from conversation_agent.local_slm.runtime_config import LocalLLMConfig
from conversation_agent.local_slm.stage2_dataset import (
    BenchmarkScenario,
    FrozenBenchmark,
    atomic_write_json,
    load_frozen_benchmark,
    stable_fingerprint,
)
from conversation_agent.local_slm.stage2_metrics import aggregate_metrics, evaluate_candidate
from conversation_agent.local_slm.stage2_openai import OpenAIBenchmarkProvider
from conversation_agent.local_slm.validator import OutputValidator
from conversation_agent.settings import load_env_file

COMPARISON_MODES = ("system_comparison", "same_context")
REAL_PROVIDERS = ("local_qwen", "openai_gpt4o_mini")
ALL_ACTIONS: tuple[Action, ...] = ("reply", "no_reply", "reaction", "handoff")
PROMPT_VERSIONS = {
    "system_comparison": {
        "local_qwen": "local_product_v1",
        "openai_gpt4o_mini": "openai_product_v1",
    },
    "same_context": {
        "local_qwen": "same_semantic_context_v1",
        "openai_gpt4o_mini": "same_semantic_context_v1",
    },
}


class Stage2Provider(Protocol):
    provider_name: str
    model: str

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        """Generate one structured benchmark result."""
        ...


@dataclass(frozen=True)
class Stage2RunOptions:
    dataset_path: Path
    output_dir: Path
    mode: str
    providers: tuple[str, ...]
    seed: int = 42
    scenario_limit: int | None = None
    category: str | None = None
    resume: bool = False
    retry_errors: bool = False
    repetitions: int = 1
    max_output_tokens: int = 256
    temperature: float = 0.7
    top_p: float = 0.9
    top_k: int = 20


async def run_stage2_benchmark(
    options: Stage2RunOptions,
    *,
    provider_overrides: Mapping[str, Stage2Provider] | None = None,
    machine_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if options.mode not in COMPARISON_MODES:
        raise ValueError(f"unsupported comparison mode: {options.mode}")
    load_env_file(Path(".env"))
    if options.repetitions < 1:
        raise ValueError("repetitions must be at least 1")
    unknown = set(options.providers) - set(REAL_PROVIDERS)
    if unknown:
        raise ValueError(f"unsupported real providers: {sorted(unknown)}")
    benchmark = load_frozen_benchmark(options.dataset_path)
    rubric = _load_rubric(options.dataset_path.parent / "rubric.yaml")
    scenarios = _select_scenarios(benchmark, options)
    source_commit = _source_commit()
    generation_config = {
        "temperature": options.temperature,
        "top_p": options.top_p,
        "top_k": options.top_k,
        "max_output_tokens": options.max_output_tokens,
        "seed": options.seed,
        "repetitions": options.repetitions,
    }
    config_value = {
        "benchmark_fingerprint": benchmark.fingerprint,
        "mode": options.mode,
        "providers": list(options.providers),
        "scenario_ids": [item.id for item in scenarios],
        "prompt_versions": PROMPT_VERSIONS[options.mode],
        "generation_config": generation_config,
    }
    config_fingerprint = stable_fingerprint(config_value)
    run_fingerprint = stable_fingerprint(
        {
            "source_commit": source_commit,
            "config_fingerprint": config_fingerprint,
        }
    )
    run_meta = {
        "benchmark_name": benchmark.manifest["name"],
        "benchmark_version": benchmark.manifest["version"],
        "benchmark_fingerprint": benchmark.fingerprint,
        "source_commit": source_commit,
        "run_fingerprint": run_fingerprint,
        "config_fingerprint": config_fingerprint,
        "comparison_mode": options.mode,
        "prompt_context_versions": PROMPT_VERSIONS[options.mode],
        "generation_config": generation_config,
        "providers_requested": list(options.providers),
        "scenario_count_selected": len(scenarios),
        "machine": machine_override or machine_metadata(),
        "created_at": datetime.now(UTC).isoformat(),
        "provider_status": {},
        "full_determinism_claimed": False,
        "determinism_note": (
            "Dataset, config, run fingerprints and A/B ordering are deterministic. "
            "Remote GPT API outputs are not claimed to be fully deterministic."
        ),
    }
    _prepare_run_directory(options, run_meta)
    providers, unavailable = await _build_providers(
        options.providers,
        overrides=provider_overrides,
    )
    for provider_name, reason in unavailable.items():
        run_meta["provider_status"][provider_name] = {
            "status": "unavailable",
            "reason": reason,
            "completed": 0,
        }
    atomic_write_json(options.output_dir / "run.json", run_meta)

    for provider_name in options.providers:
        provider = providers.get(provider_name)
        if provider is None:
            continue
        status = {
            "status": "running",
            "model": provider.model,
            "completed": 0,
            "errors": 0,
        }
        run_meta["provider_status"][provider_name] = status
        atomic_write_json(options.output_dir / "run.json", run_meta)
        stop_after_error = False
        for repetition in range(1, options.repetitions + 1):
            for scenario in scenarios:
                result_path = _result_path(
                    options.output_dir,
                    provider_name,
                    scenario.id,
                    repetition,
                )
                existing = _load_optional_json(result_path)
                if existing is not None:
                    if not options.resume:
                        raise ValueError(
                            f"result already exists; use --resume: {result_path}"
                        )
                    if not existing.get("provider_error"):
                        status["completed"] += 1
                        continue
                    if not options.retry_errors:
                        status["errors"] += 1
                        continue
                request, context_record = _build_request(
                    scenario,
                    mode=options.mode,
                    provider_name=provider_name,
                    options=options,
                )
                started_at = datetime.now(UTC).isoformat()
                record = _base_result_record(
                    benchmark=benchmark,
                    scenario=scenario,
                    provider_name=provider_name,
                    provider=provider,
                    options=options,
                    repetition=repetition,
                    source_commit=source_commit,
                    config_fingerprint=config_fingerprint,
                    run_fingerprint=run_fingerprint,
                    context_record=context_record,
                    started_at=started_at,
                )
                try:
                    generated = await provider.generate(request)
                    validation = OutputValidator().validate(generated)
                    normalized = (
                        validation.normalized.to_dict()
                        if validation.normalized is not None
                        else None
                    )
                    record.update(
                        {
                            "raw_output": generated.raw_output,
                            "normalized_output": normalized,
                            "validation": validation.to_dict(),
                            "latency_ms": generated.latency_ms,
                            "prompt_tokens": generated.prompt_tokens,
                            "completion_tokens": generated.completion_tokens,
                            "total_tokens": generated.total_tokens,
                            "retry_count": generated.retry_count,
                            "tokens_per_second": generated.tokens_per_second,
                            "provider_error": None,
                        }
                    )
                    record["automatic_evaluation"] = evaluate_candidate(
                        scenario,
                        normalized=normalized,
                        validation=record["validation"],
                        rubric=rubric,
                    )
                    status["completed"] += 1
                except Exception as exc:  # noqa: BLE001
                    record.update(
                        {
                            "raw_output": None,
                            "normalized_output": None,
                            "validation": {"valid": False, "errors": ["provider_error"]},
                            "provider_error": _safe_error(exc),
                            "automatic_evaluation": evaluate_candidate(
                                scenario,
                                normalized=None,
                                validation={"valid": False},
                                rubric=rubric,
                            ),
                        }
                    )
                    status["errors"] += 1
                    status["status"] = "interrupted"
                    status["reason"] = record["provider_error"]
                    stop_after_error = True
                record["completed_at"] = datetime.now(UTC).isoformat()
                atomic_write_json(result_path, record)
                _write_live_summary(options.output_dir, run_meta)
                atomic_write_json(options.output_dir / "run.json", run_meta)
                if stop_after_error:
                    break
            if stop_after_error:
                break
        if not stop_after_error:
            status["status"] = "completed"
        atomic_write_json(options.output_dir / "run.json", run_meta)

    return _write_live_summary(options.output_dir, run_meta)


def load_run_results(run_dir: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    results_root = run_dir / "results"
    if not results_root.is_dir():
        return results
    for path in sorted(results_root.rglob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            results.append(value)
    return results


def machine_metadata() -> dict[str, Any]:
    return {
        "operating_system": platform.platform(),
        "python": platform.python_version(),
        "cpu": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "unknown"),
        "logical_cpu_count": os.cpu_count(),
        "ram_bytes": _windows_ram_bytes(),
        "gpu": _windows_gpu_names(),
        "llama_cpp_version": _llama_cpp_version(),
    }


def normalized_semantic_context(scenario: BenchmarkScenario) -> str:
    value = {
        "agent_profile": scenario.agent_profile,
        "relationship": scenario.relationship,
        "conversation": list(scenario.flat_conversation),
        "known_facts": list(scenario.known_facts),
        "goal": scenario.goal,
        "restrictions": [f"Do not claim: {item}" for item in scenario.forbidden_claims],
        "allowed_actions": list(ALL_ACTIONS),
        "output_contract": {
            "fields": [
                "action",
                "messages",
                "reaction",
                "handoff_required",
                "confidence",
            ],
            "max_output_tokens": 256,
        },
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _build_request(
    scenario: BenchmarkScenario,
    *,
    mode: str,
    provider_name: str,
    options: Stage2RunOptions,
) -> tuple[GenerationRequest, dict[str, Any]]:
    policy = RuleBasedDialoguePolicy()
    decision = safe_policy_decision(
        policy,
        DialoguePolicyInput(
            messages=scenario.incoming_messages,
            relationship=str(scenario.relationship.get("type", "unknown")),
        ),
    )
    local_context = LocalContextBuilder().build(
        agent_id=scenario.agent_profile,
        decision=decision,
        messages=list(scenario.flat_conversation),
        relationship=str(scenario.relationship.get("type", "unknown")),
        facts=scenario.known_facts,
    )
    allowed_actions: tuple[Action, ...] = ()
    semantic_context: str | None = None
    system_prompt: str | None = None
    context_pipeline = "local_context_builder"
    if mode == "same_context":
        allowed_actions = ALL_ACTIONS
        semantic_context = normalized_semantic_context(scenario)
        system_prompt = generation_system_prompt(
            thinking=False,
            allowed_actions=ALL_ACTIONS,
        )
        context_pipeline = "normalized_semantic_context_v1"
    elif provider_name == "openai_gpt4o_mini":
        allowed_actions = ALL_ACTIONS
        semantic_context = _openai_product_context(scenario)
        system_prompt = (
            build_instructions(Path("README.md"))
            + "\n\n"
            + generation_system_prompt(
                thinking=True,
                allowed_actions=ALL_ACTIONS,
            )
        )
        context_pipeline = "openai_product_prompt_v1"
    request = GenerationRequest(
        policy=decision,
        context=local_context,
        max_output_tokens=options.max_output_tokens,
        temperature=options.temperature,
        top_p=options.top_p,
        semantic_context=semantic_context,
        system_prompt=system_prompt,
        allowed_actions=allowed_actions,
    )
    return request, {
        "pipeline": context_pipeline,
        "prompt_context_version": PROMPT_VERSIONS[mode][provider_name],
        "semantic_context": semantic_context,
        "local_context": (
            local_context.render(budget_chars=16_384)
            if semantic_context is None
            else None
        ),
        "allowed_actions": list(allowed_actions or (decision.action,)),
        "policy_decision": decision.to_dict(),
    }


def _openai_product_context(scenario: BenchmarkScenario) -> str:
    lines = ["Conversation:"]
    for turn in scenario.flat_conversation:
        lines.append(f"{turn['role']}: {turn['content']}")
    if scenario.known_facts:
        lines.append("Known facts:")
        lines.extend(f"- {item}" for item in scenario.known_facts)
    lines.append(f"Goal: {scenario.goal}")
    if scenario.forbidden_claims:
        lines.append("Restrictions:")
        lines.extend(f"- Do not claim: {item}" for item in scenario.forbidden_claims)
    return "\n".join(lines)


async def _build_providers(
    provider_names: tuple[str, ...],
    *,
    overrides: Mapping[str, Stage2Provider] | None,
) -> tuple[dict[str, Stage2Provider], dict[str, str]]:
    if overrides is not None:
        return (
            {name: overrides[name] for name in provider_names if name in overrides},
            {
                name: "provider override was not supplied"
                for name in provider_names
                if name not in overrides
            },
        )
    providers: dict[str, Stage2Provider] = {}
    unavailable: dict[str, str] = {}
    if "local_qwen" in provider_names:
        local = OpenAICompatibleLocalProvider.from_config(LocalLLMConfig.from_env())
        if await local.health_check():
            providers["local_qwen"] = local
        else:
            unavailable["local_qwen"] = (
                f"local server unavailable at {local.base_url}; start llama.cpp and resume"
            )
    if "openai_gpt4o_mini" in provider_names:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            unavailable["openai_gpt4o_mini"] = (
                "OPENAI_API_KEY is missing; add it and rerun with --resume"
            )
        else:
            providers["openai_gpt4o_mini"] = OpenAIBenchmarkProvider(
                api_key=api_key,
                model=os.getenv("STAGE2_OPENAI_MODEL", "gpt-4o-mini"),
                timeout_seconds=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "30")),
                max_output_tokens=256,
            )
    return providers, unavailable


def _select_scenarios(
    benchmark: FrozenBenchmark,
    options: Stage2RunOptions,
) -> tuple[BenchmarkScenario, ...]:
    scenarios = benchmark.scenarios
    if options.category:
        scenarios = tuple(
            item for item in scenarios if options.category in item.all_categories
        )
    if options.scenario_limit is not None:
        if options.scenario_limit < 1:
            raise ValueError("--scenario-limit must be positive")
        scenarios = scenarios[: options.scenario_limit]
    return scenarios


def _prepare_run_directory(options: Stage2RunOptions, run_meta: dict[str, Any]) -> None:
    options.output_dir.mkdir(parents=True, exist_ok=True)
    existing_meta = _load_optional_json(options.output_dir / "run.json")
    if existing_meta is None:
        if options.resume:
            raise ValueError("--resume requested but run.json does not exist")
        return
    if existing_meta.get("benchmark_fingerprint") != run_meta["benchmark_fingerprint"]:
        raise ValueError("run directory contains a different benchmark fingerprint")
    if existing_meta.get("config_fingerprint") != run_meta["config_fingerprint"]:
        raise ValueError("run directory contains a different config fingerprint")
    if not options.resume:
        raise ValueError("run directory already exists; use --resume")
    run_meta["created_at"] = existing_meta.get("created_at", run_meta["created_at"])
    run_meta["provider_status"] = existing_meta.get("provider_status", {})


def _base_result_record(
    *,
    benchmark: FrozenBenchmark,
    scenario: BenchmarkScenario,
    provider_name: str,
    provider: Stage2Provider,
    options: Stage2RunOptions,
    repetition: int,
    source_commit: str,
    config_fingerprint: str,
    run_fingerprint: str,
    context_record: dict[str, Any],
    started_at: str,
) -> dict[str, Any]:
    return {
        "benchmark_name": benchmark.manifest["name"],
        "benchmark_version": benchmark.manifest["version"],
        "benchmark_fingerprint": benchmark.fingerprint,
        "source_commit": source_commit,
        "run_fingerprint": run_fingerprint,
        "config_fingerprint": config_fingerprint,
        "scenario_id": scenario.id,
        "category": scenario.category,
        "tags": list(scenario.tags),
        "expected_actions": list(scenario.expected_actions),
        "provider": provider_name,
        "model_id": provider.model,
        "model_revision": None,
        "backend": getattr(provider, "backend_name", "unknown"),
        "comparison_mode": options.mode,
        "prompt_context_version": PROMPT_VERSIONS[options.mode][provider_name],
        "generation_config": {
            "temperature": options.temperature,
            "top_p": options.top_p,
            "top_k": options.top_k,
            "max_output_tokens": options.max_output_tokens,
            "seed": options.seed,
        },
        "repetition": repetition,
        "started_at": started_at,
        "scenario": scenario.to_dict(),
        "request_context": context_record,
    }


def _result_path(
    output_dir: Path,
    provider: str,
    scenario_id: str,
    repetition: int,
) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", scenario_id)
    return output_dir / "results" / provider / f"{safe_id}__r{repetition}.json"


def _write_live_summary(
    output_dir: Path,
    run_meta: dict[str, Any],
) -> dict[str, Any]:
    results = load_run_results(output_dir)
    by_provider: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        by_provider.setdefault(str(item.get("provider", "unknown")), []).append(item)
    summary = {
        "benchmark_fingerprint": run_meta["benchmark_fingerprint"],
        "run_fingerprint": run_meta["run_fingerprint"],
        "comparison_mode": run_meta["comparison_mode"],
        "provider_status": run_meta["provider_status"],
        "metrics": {
            provider: aggregate_metrics(items)
            for provider, items in sorted(by_provider.items())
        },
        "result_count": len(results),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    atomic_write_json(output_dir / "summary.json", summary)
    return summary


def _load_rubric(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("rubric.yaml must contain an object")
    return value


def _load_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def _source_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _safe_error(exc: Exception) -> str:
    value = f"{type(exc).__name__}: {exc}"
    value = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED_API_KEY]", value)
    value = re.sub(r"Bearer\s+\S+", "Bearer [REDACTED]", value, flags=re.IGNORECASE)
    return value[:2000]


def _windows_ram_bytes() -> int | None:
    if os.name != "nt":
        return None
    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return int(status.total_physical)
    except Exception:  # noqa: BLE001
        return None


def _windows_gpu_names() -> list[str]:
    if os.name != "nt":
        return []
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    except Exception:  # noqa: BLE001
        return []


def _llama_cpp_version() -> str | None:
    path_file = Path(".runtime/local_slm/llama-server.path")
    if not path_file.is_file():
        return None
    executable = Path(path_file.read_text(encoding="utf-8-sig").strip())
    if not executable.is_file():
        return None
    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        value = (completed.stdout + completed.stderr).strip()
        return value[:500] or None
    except Exception:  # noqa: BLE001
        return None
