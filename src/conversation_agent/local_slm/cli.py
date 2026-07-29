"""CLI commands for the local Telegram SLM experiment."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from conversation_agent.local_slm.benchmark import run_benchmark
from conversation_agent.local_slm.context import LocalContextBuilder
from conversation_agent.local_slm.dataset import build_sft_dataset
from conversation_agent.local_slm.models import DialoguePolicyInput, GenerationRequest
from conversation_agent.local_slm.policy import RuleBasedDialoguePolicy, safe_policy_decision
from conversation_agent.local_slm.provider import FakeLocalGenerationProvider
from conversation_agent.local_slm.router import HybridGenerationRouter
from conversation_agent.local_slm.training import training_dry_run
from conversation_agent.local_slm.validator import OutputValidator


def add_local_slm_parsers(subparsers: Any) -> None:
    simulate = subparsers.add_parser("local-simulate", help="Run offline local SLM demo")
    simulate.add_argument("--contact-id", default="test-contact")
    simulate.add_argument("--agent-id", default="informal-manager")
    simulate.add_argument("--message", action="append", required=True)
    simulate.set_defaults(func=_simulate)

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


def run_local_slm_command(args: argparse.Namespace) -> int:
    result = args.func(args)
    if result is not None:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _simulate(args: argparse.Namespace) -> dict[str, Any]:
    policy = RuleBasedDialoguePolicy()
    decision = safe_policy_decision(policy, DialoguePolicyInput(messages=tuple(args.message)))
    context_builder = LocalContextBuilder()
    context = context_builder.build(
        agent_id=args.agent_id,
        decision=decision,
        messages=[{"role": "user", "content": item} for item in args.message],
    )
    router = HybridGenerationRouter(
        local_provider=FakeLocalGenerationProvider(),
        validator=OutputValidator(),
        mode="local_only",
    )
    result = asyncio.run(router.generate(GenerationRequest(policy=decision, context=context)))
    return {
        "contact_id": args.contact_id,
        "accumulated_messages": args.message,
        "dialogue_policy": decision.to_dict(),
        "selected_context": context.render(budget_chars=4000),
        "selected_adapter": None,
        "local_provider": "fake-local",
        "generation": result.to_dict(),
        "behavior_plan": {
            "typing": result.selected.action == "reply",
            "bubble_count": len(result.selected.messages),
        },
    }


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
