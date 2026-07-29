from __future__ import annotations

import asyncio
import json
from pathlib import Path

from conversation_agent.local_slm.benchmark import run_benchmark
from conversation_agent.local_slm.context import LocalContextBuilder
from conversation_agent.local_slm.dataset import build_sft_dataset
from conversation_agent.local_slm.models import (
    DialoguePolicyInput,
    GenerationRequest,
    GenerationResult,
)
from conversation_agent.local_slm.policy import RuleBasedDialoguePolicy
from conversation_agent.local_slm.provider import FakeLocalGenerationProvider
from conversation_agent.local_slm.registry import AdapterSpec, AgentAdapterRegistry
from conversation_agent.local_slm.router import HybridGenerationRouter
from conversation_agent.local_slm.training import training_dry_run
from conversation_agent.local_slm.validator import OutputValidator


def test_rule_based_dialogue_policy_actions() -> None:
    policy = RuleBasedDialoguePolicy()

    assert policy.decide(DialoguePolicyInput(messages=("ок",))).action == "no_reply"
    assert policy.decide(DialoguePolicyInput(messages=("нужен бот",))).action == "reply"
    assert policy.decide(DialoguePolicyInput(messages=("🔥",))).action == "reaction"
    assert policy.decide(DialoguePolicyInput(messages=("скинь договор",))).action == "handoff"


def test_context_builder_respects_budget_and_uses_tail() -> None:
    decision = RuleBasedDialoguePolicy().decide(DialoguePolicyInput(messages=("нужен бот",)))
    messages = [{"role": "user", "content": f"message {index} " + "x" * 100} for index in range(20)]

    context = LocalContextBuilder(budget_chars=500).build(
        agent_id="agent",
        decision=decision,
        messages=messages,
        facts=["private fact"] * 10,
    )
    rendered = context.render(budget_chars=500)

    assert len(rendered) <= 500
    assert "message 19" in rendered


def test_output_validator_rejects_invalid_or_unsafe_output() -> None:
    validator = OutputValidator(max_bubble_count=2, max_message_length=20)
    result = GenerationResult(
        action="reply",
        messages=("same", "same", "https://bad"),
        confidence=1.2,
    )

    validation = validator.validate(result)

    assert not validation.valid
    assert "duplicate_messages" in validation.errors
    assert "forbidden_link" in validation.errors


def test_hybrid_router_local_only_handoffs_invalid_output() -> None:
    class BadProvider:
        provider_name = "bad"

        async def health_check(self) -> bool:
            return True

        async def generate(self, request: GenerationRequest) -> GenerationResult:
            return GenerationResult(action="reply", messages=(), confidence=0.1, provider="bad")

    decision = RuleBasedDialoguePolicy().decide(DialoguePolicyInput(messages=("нужен бот",)))
    context = LocalContextBuilder().build(
        agent_id="agent",
        decision=decision,
        messages=[{"role": "user", "content": "нужен бот"}],
    )
    router = HybridGenerationRouter(
        local_provider=BadProvider(),
        validator=OutputValidator(),
        mode="local_only",
    )

    result = asyncio.run(router.generate(GenerationRequest(policy=decision, context=context)))

    assert result.selected.action == "handoff"


def test_fake_provider_allows_offline_generation() -> None:
    decision = RuleBasedDialoguePolicy().decide(DialoguePolicyInput(messages=("нужен бот",)))
    context = LocalContextBuilder().build(
        agent_id="agent",
        decision=decision,
        messages=[{"role": "user", "content": "нужен бот"}],
    )

    result = asyncio.run(FakeLocalGenerationProvider().generate(GenerationRequest(decision, context)))

    assert result.action == "reply"
    assert result.messages


def test_dataset_builder_excludes_ai_and_deduplicates(tmp_path: Path) -> None:
    source = tmp_path / "cleaned.jsonl"
    rows = [
        {
            "example_id": "a",
            "dialog_id": 1,
            "context": [{"role": "user", "text": "hi"}],
            "target_reply": "hello",
            "is_human_authored": True,
        },
        {
            "example_id": "duplicate",
            "dialog_id": 1,
            "context": [{"role": "user", "text": "hi"}],
            "target_reply": "hello",
            "is_human_authored": True,
        },
        {
            "example_id": "ai",
            "dialog_id": 2,
            "context": [{"role": "user", "text": "hi"}],
            "target_reply": "ai",
            "is_human_authored": False,
        },
    ]
    source.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in rows) + "\n",
        encoding="utf-8",
    )

    summary = build_sft_dataset(source_path=source, output_path=tmp_path / "dataset.jsonl")

    assert summary.examples == 1
    assert summary.duplicates_removed == 1
    assert summary.ai_generated_excluded == 1


def test_training_dry_run_does_not_load_model(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        json.dumps({"split": "train"}) + "\n" + json.dumps({"split": "test"}) + "\n",
        encoding="utf-8",
    )

    summary = training_dry_run(
        dataset_path=dataset,
        base_model="Qwen2.5-0.5B",
        adapter_output_dir=tmp_path / "adapter",
        batch_size=2,
    )

    assert summary.estimated_batches == 1
    assert summary.gpu_required_for_real_run


def test_adapter_registry_selects_active_agent_adapter(tmp_path: Path) -> None:
    registry = AgentAdapterRegistry(tmp_path / "adapters.json")
    registry.save(
        [
            AdapterSpec(
                agent_id="agent",
                base_model="qwen",
                adapter_id="telegram-core",
                adapter_version="1",
                active=True,
                dataset_fingerprint="abc",
                output_path=".runtime/models/a",
            )
        ]
    )

    selected = registry.select("agent")

    assert selected is not None
    assert selected.adapter_id == "telegram-core"


def test_benchmark_writes_metrics_without_real_providers(tmp_path: Path) -> None:
    summary = asyncio.run(
        run_benchmark(
            dataset=Path("tests/fixtures/dialogue_benchmark.jsonl"),
            output_dir=tmp_path / "bench",
        )
    )

    assert summary.scenarios == 3
    assert (tmp_path / "bench" / "summary.json").is_file()
    assert (tmp_path / "bench" / "raw_outputs.json").is_file()
