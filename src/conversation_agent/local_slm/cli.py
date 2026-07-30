"""CLI commands for the local Telegram SLM experiment."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from conversation_agent.local_slm.benchmark import run_benchmark
from conversation_agent.local_slm.context import LocalContextBuilder
from conversation_agent.local_slm.dataset import build_sft_dataset
from conversation_agent.local_slm.models import DialoguePolicyInput, GenerationRequest, HybridResult
from conversation_agent.local_slm.policy import RuleBasedDialoguePolicy, safe_policy_decision
from conversation_agent.local_slm.provider import (
    FakeLocalGenerationProvider,
    LocalModelError,
    OpenAICompatibleLocalProvider,
)
from conversation_agent.local_slm.router import HybridGenerationRouter
from conversation_agent.local_slm.runtime_config import LocalLLMConfig
from conversation_agent.local_slm.stage2_dataset import import_private_benchmark
from conversation_agent.local_slm.stage2_report import generate_stage2_report
from conversation_agent.local_slm.stage2_review import run_interactive_review
from conversation_agent.local_slm.stage2_review_ui import run_review_ui
from conversation_agent.local_slm.stage2_runner import (
    Stage2RunOptions,
    run_stage2_benchmark,
)
from conversation_agent.local_slm.stage25_diagnostics import generate_diagnostic_pack
from conversation_agent.local_slm.stage25_report import generate_stage25_report
from conversation_agent.local_slm.stage25_runner import (
    PIPELINES as STAGE25_PIPELINES,
)
from conversation_agent.local_slm.stage25_runner import (
    Stage25RunOptions,
    run_stage25_benchmark,
)
from conversation_agent.local_slm.stage26 import (
    Stage26Options,
    generate_stage26_report,
    run_stage26,
)
from conversation_agent.local_slm.training import training_dry_run
from conversation_agent.local_slm.validator import OutputValidator


def add_local_slm_parsers(subparsers: Any) -> None:
    simulate = subparsers.add_parser("local-simulate", help="Run offline local SLM demo")
    simulate.add_argument("--contact-id", default="test-contact")
    simulate.add_argument("--agent-id", default="informal-manager")
    simulate.add_argument("--message", action="append", required=True)
    simulate.add_argument("--fake", action="store_true", help="Use explicit fake provider")
    simulate.set_defaults(func=_simulate)

    doctor = subparsers.add_parser("local-model-doctor", help="Check real local model endpoint")
    doctor.add_argument("--profile")
    doctor.set_defaults(func=_doctor)

    smoke = subparsers.add_parser("local-model-smoke", help="Run real local model smoke scenarios")
    smoke.add_argument("--output-dir", default=".runtime/local_slm/smoke")
    smoke.set_defaults(func=_smoke)

    dataset = subparsers.add_parser("build-slm-dataset", help="Build local SFT dataset")
    dataset.add_argument("--source", required=True)
    dataset.add_argument("--output", required=True)
    dataset.set_defaults(func=_build_dataset)

    train = subparsers.add_parser("local-train-dry-run", help="Plan LoRA training without GPU")
    train.add_argument("--dataset", required=True)
    train.add_argument("--base-model", default="Qwen2.5-0.5B")
    train.add_argument("--output-dir", default=".runtime/models/adapters/dry-run")
    train.add_argument("--batch-size", type=int, default=4)
    train.set_defaults(func=_train_dry_run)

    benchmark = subparsers.add_parser("benchmark", help="Run fake/local benchmark")
    benchmark_sub = benchmark.add_subparsers(dest="benchmark_command", required=True)
    run = benchmark_sub.add_parser("run")
    run.add_argument("--dataset", required=True)
    run.add_argument("--providers", default="fake")
    run.add_argument("--output", required=True)
    run.set_defaults(func=_benchmark_run)

    stage2 = benchmark_sub.add_parser(
        "stage2-run",
        help="Run the real Stage 2 frozen baseline benchmark",
    )
    stage2.add_argument("--dataset", required=True)
    stage2.add_argument(
        "--mode",
        required=True,
        choices=("system_comparison", "same_context"),
    )
    stage2.add_argument(
        "--providers",
        default="local_qwen,openai_gpt4o_mini",
    )
    stage2.add_argument("--output", required=True)
    stage2.add_argument("--seed", type=int, default=42)
    stage2.add_argument("--scenario-limit", type=int)
    stage2.add_argument("--category")
    stage2.add_argument("--resume", action="store_true")
    stage2.add_argument("--retry-errors", action="store_true")
    stage2.add_argument("--no-openai", action="store_true")
    stage2.add_argument("--no-local", action="store_true")
    stage2.add_argument("--repetitions", type=int, default=1)
    stage2.set_defaults(func=_benchmark_stage2_run)

    private_import = benchmark_sub.add_parser(
        "import-private",
        help="Import an anonymized private benchmark under .runtime",
    )
    private_import.add_argument("--input", required=True)
    private_import.add_argument("--output", required=True)
    private_import.add_argument("--anonymize", action="store_true")
    private_import.add_argument("--purpose", required=True)
    private_import.add_argument("--confirm-save-source", action="store_true")
    private_import.set_defaults(func=_benchmark_import_private)

    review = benchmark_sub.add_parser(
        "stage2-review",
        help="Run blind human A/B review",
    )
    review.add_argument("--run", required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--seed", type=int, default=42)
    review.add_argument("--category")
    review.add_argument("--only-unreviewed", action="store_true")
    review.add_argument("--reveal", action="store_true")
    review.set_defaults(func=_benchmark_stage2_review)

    review_ui = benchmark_sub.add_parser(
        "stage2-review-ui",
        help="Open the blind human A/B review web interface",
    )
    review_ui.add_argument("--run", required=True)
    review_ui.add_argument("--reviewer", required=True)
    review_ui.add_argument("--seed", type=int, default=42)
    review_ui.add_argument("--category")
    review_ui.add_argument("--port", type=int, default=8765)
    review_ui.add_argument("--no-open", action="store_true")
    review_ui.set_defaults(func=_benchmark_stage2_review_ui)

    report = benchmark_sub.add_parser(
        "stage2-report",
        help="Generate Stage 2 reports",
    )
    report.add_argument("--run", required=True)
    report.add_argument("--reviews", required=True)
    report.add_argument("--output", required=True)
    report.set_defaults(func=_benchmark_stage2_report)

    diagnostic = benchmark_sub.add_parser(
        "diagnostic-pack",
        help="Build a compact technical diagnostic sample",
    )
    diagnostic.add_argument("--run", required=True)
    diagnostic.add_argument("--output", required=True)
    diagnostic.add_argument("--max-examples", type=int, default=40)
    diagnostic.add_argument("--seed", type=int, default=42)
    diagnostic.set_defaults(func=_benchmark_diagnostic_pack)

    stage25 = benchmark_sub.add_parser(
        "stage25-run",
        help="Run the Stage 2.5 policy/renderer experiment",
    )
    stage25.add_argument("--dataset", required=True)
    stage25.add_argument("--pipelines", default=",".join(STAGE25_PIPELINES))
    stage25.add_argument("--output", required=True)
    stage25.add_argument("--baseline", default=".runtime/benchmarks/stage2-system-v1")
    stage25.add_argument("--seed", type=int, default=42)
    stage25.add_argument("--scenario-limit", type=int)
    stage25.add_argument("--category")
    stage25.add_argument("--resume", action="store_true")
    stage25.add_argument("--retry-errors", action="store_true")
    stage25.add_argument("--no-openai", action="store_true")
    stage25.add_argument("--no-local", action="store_true")
    gpu_mode = stage25.add_mutually_exclusive_group()
    gpu_mode.add_argument(
        "--gpu-required",
        dest="gpu_required",
        action="store_true",
        default=True,
    )
    gpu_mode.add_argument(
        "--allow-cpu",
        dest="gpu_required",
        action="store_false",
        help="Explicitly allow the local renderer to run without confirmed CUDA offload",
    )
    stage25.set_defaults(func=_benchmark_stage25_run)

    stage25_report = benchmark_sub.add_parser(
        "stage25-report",
        help="Generate the Stage 2.5 comparison report",
    )
    stage25_report.add_argument("--run", required=True)
    stage25_report.add_argument("--baseline", required=True)
    stage25_report.add_argument("--output", required=True)
    stage25_report.set_defaults(func=_benchmark_stage25_report)

    stage26 = benchmark_sub.add_parser(
        "stage26-run",
        help="Run the Ruadapt renderer-only qualification",
    )
    stage26.add_argument("--dataset", required=True)
    stage26.add_argument("--renderer", required=True)
    stage26.add_argument("--contracts-from", required=True)
    stage26.add_argument("--baseline", required=True)
    stage26.add_argument("--output", required=True)
    stage26.add_argument("--seed", type=int, default=42)
    stage26.add_argument("--scenario-limit", type=int)
    stage26.add_argument("--category")
    stage26.add_argument("--resume", action="store_true")
    stage26.add_argument("--retry-errors", action="store_true")
    stage26.add_argument("--gpu-required", action="store_true", default=True)
    stage26.set_defaults(func=_benchmark_stage26_run)

    stage26_report = benchmark_sub.add_parser(
        "stage26-report",
        help="Generate the Ruadapt renderer qualification report",
    )
    stage26_report.add_argument("--run", required=True)
    stage26_report.add_argument("--baseline", required=True)
    stage26_report.add_argument("--output", required=True)
    stage26_report.set_defaults(func=_benchmark_stage26_report)


def run_local_slm_command(args: argparse.Namespace) -> int:
    result = args.func(args)
    if result is not None:
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        if result.get("Ready") == "NO":
            return 1
        if "validation_success_rate" in result and result["validation_success_rate"] < 1.0:
            return 1
    return 0


def _simulate(args: argparse.Namespace) -> dict[str, Any]:
    config = LocalLLMConfig.from_env()
    policy = RuleBasedDialoguePolicy()
    decision = safe_policy_decision(policy, DialoguePolicyInput(messages=tuple(args.message)))
    context_builder = LocalContextBuilder()
    context = context_builder.build(
        agent_id=args.agent_id,
        decision=decision,
        messages=[{"role": "user", "content": item} for item in args.message],
    )
    provider = _make_provider(config, fake=bool(args.fake))
    request = GenerationRequest(policy=decision, context=context)
    validator = OutputValidator()
    if args.fake:
        router = HybridGenerationRouter(
            local_provider=provider,
            validator=validator,
            mode="local_only",
        )
        result = asyncio.run(router.generate(request))
    else:
        selected = asyncio.run(provider.generate(request))
        validation = validator.validate(selected)
        result = HybridResult(
            selected=validation.normalized or selected,
            validation=validation,
            fallback_used=False,
            provider_results={"local": selected},
            route=("local",),
        )
    if not result.validation.valid:
        raise LocalModelError(f"local-simulate validation failed: {result.validation.errors}")
    return {
        "contact_id": args.contact_id,
        "provider": result.selected.provider,
        "backend": result.selected.backend,
        "model": result.selected.model or ("fake" if args.fake else config.model),
        "fake_provider": bool(args.fake),
        "openai_fallback_used": result.fallback_used,
        "accumulated_messages": args.message,
        "dialogue_policy": decision.to_dict(),
        "selected_context": context.render(budget_chars=4000),
        "selected_adapter": None,
        "local_provider": result.selected.provider,
        "raw_action": result.selected.action,
        "generated_bubbles": list(result.selected.messages),
        "validator": result.validation.to_dict(),
        "retry_count": result.selected.retry_count,
        "prompt_tokens": result.selected.prompt_tokens,
        "completion_tokens": result.selected.completion_tokens,
        "ttft_ms": result.selected.ttft_ms,
        "total_generation_ms": result.selected.latency_ms,
        "tokens_per_second": result.selected.tokens_per_second,
        "generation": result.to_dict(),
        "behavior_plan": {
            "typing": result.selected.action == "reply",
            "bubble_count": len(result.selected.messages),
        },
    }


def _make_provider(config: LocalLLMConfig, *, fake: bool) -> FakeLocalGenerationProvider | OpenAICompatibleLocalProvider:
    if fake:
        return FakeLocalGenerationProvider()
    return OpenAICompatibleLocalProvider.from_config(config)


def _doctor(args: argparse.Namespace) -> dict[str, Any]:
    if args.profile == "ruadapt-qwen3-4b":
        return asyncio.run(_doctor_ruadapt())
    return asyncio.run(_doctor_async(LocalLLMConfig.from_env()))


async def _doctor_ruadapt() -> dict[str, Any]:
    from conversation_agent.local_slm.renderer_registry import get_renderer_profile

    profile_name = json.loads(
        Path(".runtime/local_slm/ruadapt-model.json").read_text(
            encoding="utf-8-sig"
        )
    )["profile"]
    profile = get_renderer_profile(profile_name)
    status = json.loads(
        Path(".runtime/local_slm/ruadapt-gpu-status.json").read_text(
            encoding="utf-8-sig"
        )
    )
    config = LocalLLMConfig(
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
    )
    base = await _doctor_async(config)
    messages = " ".join(str(item) for item in base.get("Generated messages", []))
    russian = bool(__import__("re").search(r"[а-яё]", messages.casefold()))
    ready = (
        base.get("Ready") == "YES"
        and status.get("ready") is True
        and status.get("quantization") == profile.quantization
        and status.get("cpu_fallback") is False
        and russian
    )
    return {
        **base,
        "Backend": "llama.cpp CUDA",
        "Model family": "RuadaptQwen3",
        "Repository": profile.repository,
        "Resolved revision": profile.revision,
        "Quantization": profile.quantization,
        "Context": profile.context_tokens,
        "GPU": status.get("gpu_name"),
        "GPU offload": "confirmed" if status.get("ready") else "NO",
        "Offloaded layers": status.get("offloaded_layers"),
        "VRAM usage": status.get("vram_used_mib"),
        "CPU fallback": status.get("cpu_fallback"),
        "Russian completion": "OK" if russian else "NO",
        "Ready": "YES" if ready else "NO",
    }


async def _doctor_async(config: LocalLLMConfig) -> dict[str, Any]:
    provider = OpenAICompatibleLocalProvider.from_config(config)
    output: dict[str, Any] = {
        "Local model server": "NO",
        "Backend": "llama.cpp",
        "Model": config.model,
        "Endpoint": config.base_url,
        "Chat completions": "NO",
        "Structured output": "NO",
        "Non-thinking": "NO",
        "OpenAI fallback": "disabled",
        "Ready": "NO",
        "OpenAI key used": False,
    }
    try:
        health = await provider.health_check()
        output["Local model server"] = "OK" if health else "NO"
        models = await provider.list_models()
        output["Loaded models"] = models
        if config.model not in models and models:
            output["Reason"] = f"model mismatch: expected {config.model}, got {models}"
            return output
        request = _request_for_messages(("привет", "нужен бот для заявок"), "doctor-agent")
        result = await provider.generate(request)
        validation = OutputValidator().validate(result)
        output["Chat completions"] = "OK"
        output["Structured output"] = "OK" if validation.valid else "NO"
        output["Non-thinking"] = "OK" if "reasoning_output" not in validation.errors else "NO"
        output["Parsed action"] = result.action
        output["Generated messages"] = list(result.messages)
        output["Latency ms"] = result.latency_ms
        output["Prompt tokens"] = result.prompt_tokens
        output["Completion tokens"] = result.completion_tokens
        output["Ready"] = "YES" if validation.valid else "NO"
        if not validation.valid:
            output["Reason"] = validation.errors
    except Exception as exc:  # noqa: BLE001
        output["Reason"] = f"{type(exc).__name__}: {exc}"
    return output


def _smoke(args: argparse.Namespace) -> dict[str, Any]:
    config = LocalLLMConfig.from_env()
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir) / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(_smoke_async(config))
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"output_dir": str(output_dir), **report}


async def _smoke_async(config: LocalLLMConfig) -> dict[str, Any]:
    provider = OpenAICompatibleLocalProvider.from_config(config)
    scenarios = [
        ("service_ru", ("привет", "нужен бот для заявок")),
        ("thanks_ack", ("спасибо",)),
        ("handoff", ("скинь договор",)),
        ("informal_reaction", ("😂",)),
        ("formal_service_ru", ("Здравствуйте. Подскажите, вы занимаетесь разработкой Telegram-ботов?",)),
    ]
    validator = OutputValidator()
    rows: list[dict[str, Any]] = []
    valid = 0
    for scenario_id, messages in scenarios:
        request = _request_for_messages(messages, "informal-manager")
        result = await provider.generate(request)
        validation = validator.validate(result)
        valid += int(validation.valid)
        rows.append(
            {
                "id": scenario_id,
                "input": list(messages),
                "raw_output": result.raw_output,
                "normalized_output": validation.normalized.to_dict() if validation.normalized else None,
                "validation": validation.to_dict(),
                "latency_ms": result.latency_ms,
                "ttft_ms": result.ttft_ms,
                "model": result.model,
                "config_fingerprint": _config_fingerprint(config),
            }
        )
    return {
        "model": config.model,
        "backend": "llama.cpp",
        "scenarios": rows,
        "validation_success_rate": valid / len(rows),
    }


def _request_for_messages(messages: tuple[str, ...], agent_id: str) -> GenerationRequest:
    policy = RuleBasedDialoguePolicy()
    decision = safe_policy_decision(policy, DialoguePolicyInput(messages=messages))
    context = LocalContextBuilder().build(
        agent_id=agent_id,
        decision=decision,
        messages=[{"role": "user", "content": item} for item in messages],
    )
    return GenerationRequest(policy=decision, context=context)


def _config_fingerprint(config: LocalLLMConfig) -> str:
    value = json.dumps(config.__dict__, sort_keys=True)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _build_dataset(args: argparse.Namespace) -> dict[str, Any]:
    return build_sft_dataset(source_path=Path(args.source), output_path=Path(args.output)).to_dict()


def _train_dry_run(args: argparse.Namespace) -> dict[str, Any]:
    return training_dry_run(
        dataset_path=Path(args.dataset),
        base_model=args.base_model,
        adapter_output_dir=Path(args.output_dir),
        batch_size=args.batch_size,
    ).to_dict()


def _benchmark_run(args: argparse.Namespace) -> dict[str, Any]:
    providers = tuple(item.strip() for item in str(args.providers).split(",") if item.strip())
    return asyncio.run(
        run_benchmark(
            dataset=Path(args.dataset),
            output_dir=Path(args.output),
            providers=providers,
        )
    ).to_dict()


def _benchmark_stage2_run(args: argparse.Namespace) -> dict[str, Any]:
    providers = [
        item.strip()
        for item in str(args.providers).split(",")
        if item.strip()
    ]
    if args.no_openai:
        providers = [item for item in providers if item != "openai_gpt4o_mini"]
    if args.no_local:
        providers = [item for item in providers if item != "local_qwen"]
    if not providers:
        raise ValueError("at least one real provider must be enabled")
    return asyncio.run(
        run_stage2_benchmark(
            Stage2RunOptions(
                dataset_path=Path(args.dataset),
                output_dir=Path(args.output),
                mode=args.mode,
                providers=tuple(providers),
                seed=args.seed,
                scenario_limit=args.scenario_limit,
                category=args.category,
                resume=args.resume or args.retry_errors,
                retry_errors=args.retry_errors,
                repetitions=args.repetitions,
            )
        )
    )


def _benchmark_import_private(args: argparse.Namespace) -> dict[str, Any]:
    return import_private_benchmark(
        input_path=Path(args.input),
        output_dir=Path(args.output),
        anonymize=bool(args.anonymize),
        purpose=args.purpose,
        confirm_save_source=bool(args.confirm_save_source),
    )


def _benchmark_stage2_review(args: argparse.Namespace) -> dict[str, Any]:
    return run_interactive_review(
        run_dir=Path(args.run),
        reviewer=args.reviewer,
        seed=args.seed,
        category=args.category,
        only_unreviewed=bool(args.only_unreviewed),
        reveal=bool(args.reveal),
    )


def _benchmark_stage2_review_ui(args: argparse.Namespace) -> dict[str, Any]:
    return run_review_ui(
        run_dir=Path(args.run),
        reviewer=args.reviewer,
        seed=args.seed,
        category=args.category,
        port=args.port,
        open_browser=not bool(args.no_open),
    )


def _benchmark_stage2_report(args: argparse.Namespace) -> dict[str, Any]:
    return generate_stage2_report(
        run_dir=Path(args.run),
        reviews_dir=Path(args.reviews),
        output_dir=Path(args.output),
    )


def _benchmark_diagnostic_pack(args: argparse.Namespace) -> dict[str, Any]:
    return generate_diagnostic_pack(
        run_dir=Path(args.run),
        output_dir=Path(args.output),
        max_examples=args.max_examples,
        seed=args.seed,
    )


def _benchmark_stage25_run(args: argparse.Namespace) -> dict[str, Any]:
    pipelines = [
        item.strip()
        for item in str(args.pipelines).split(",")
        if item.strip()
    ]
    if args.no_openai:
        pipelines = [
            item
            for item in pipelines
            if item not in {
                "openai_direct",
                "gpt_policy_openai_renderer",
                "gpt_policy_local_renderer",
            }
        ]
    if args.no_local:
        pipelines = [
            item
            for item in pipelines
            if item not in {"local_direct", "gpt_policy_local_renderer"}
        ]
    if not pipelines:
        raise ValueError("at least one Stage 2.5 pipeline must be enabled")
    return asyncio.run(
        run_stage25_benchmark(
            Stage25RunOptions(
                dataset_path=Path(args.dataset),
                output_dir=Path(args.output),
                pipelines=tuple(pipelines),
                baseline_dir=Path(args.baseline),
                seed=args.seed,
                scenario_limit=args.scenario_limit,
                category=args.category,
                resume=args.resume or args.retry_errors,
                retry_errors=args.retry_errors,
                gpu_required=bool(args.gpu_required),
            )
        )
    )


def _benchmark_stage25_report(args: argparse.Namespace) -> dict[str, Any]:
    return generate_stage25_report(
        run_dir=Path(args.run),
        baseline_dir=Path(args.baseline),
        output_dir=Path(args.output),
    )


def _benchmark_stage26_run(args: argparse.Namespace) -> dict[str, Any]:
    return asyncio.run(
        run_stage26(
            Stage26Options(
                dataset_path=Path(args.dataset),
                renderer=args.renderer,
                contracts_from=Path(args.contracts_from),
                baseline_dir=Path(args.baseline),
                output_dir=Path(args.output),
                seed=args.seed,
                scenario_limit=args.scenario_limit,
                category=args.category,
                resume=args.resume or args.retry_errors,
                retry_errors=args.retry_errors,
                gpu_required=bool(args.gpu_required),
            )
        )
    )


def _benchmark_stage26_report(args: argparse.Namespace) -> dict[str, Any]:
    return generate_stage26_report(
        run_dir=Path(args.run),
        baseline_dir=Path(args.baseline),
        output_dir=Path(args.output),
    )
