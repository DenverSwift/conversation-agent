"""Reproducible local benchmark runner."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from conversation_agent.local_slm.context import LocalContextBuilder
from conversation_agent.local_slm.models import DialoguePolicyInput, GenerationRequest
from conversation_agent.local_slm.policy import RuleBasedDialoguePolicy, safe_policy_decision
from conversation_agent.local_slm.provider import FakeLocalGenerationProvider
from conversation_agent.local_slm.router import HybridGenerationRouter
from conversation_agent.local_slm.validator import OutputValidator


@dataclass(frozen=True)
class BenchmarkSummary:
    scenarios: int
    providers: tuple[str, ...]
    valid_outputs: int
    invalid_outputs: int
    no_reply_accuracy: float
    average_output_length: float
    provider_failure_rate: float
    skipped_outputs: int
    output_dir: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenarios": self.scenarios,
            "providers": list(self.providers),
            "valid_outputs": self.valid_outputs,
            "invalid_outputs": self.invalid_outputs,
            "no_reply_accuracy": self.no_reply_accuracy,
            "average_output_length": self.average_output_length,
            "provider_failure_rate": self.provider_failure_rate,
            "skipped_outputs": self.skipped_outputs,
            "output_dir": self.output_dir,
        }


async def run_benchmark(
    *,
    dataset: Path,
    output_dir: Path,
    providers: tuple[str, ...] = ("fake",),
) -> BenchmarkSummary:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        json.loads(line)
        for line in dataset.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    policy = RuleBasedDialoguePolicy()
    context_builder = LocalContextBuilder()
    router = HybridGenerationRouter(
        local_provider=FakeLocalGenerationProvider(),
        validator=OutputValidator(),
        mode="local_only",
    )
    raw_outputs: list[dict[str, Any]] = []
    valid = 0
    no_reply_expected = 0
    no_reply_correct = 0
    total_length = 0
    failures = 0
    skipped = 0
    total_cases = len(rows) * len(providers)
    for provider_name in providers:
        for index, row in enumerate(rows, start=1):
            messages = tuple(str(item) for item in row.get("messages", []))
            expected_action = str(row.get("expected_action", "reply"))
            started = time.perf_counter()
            decision = safe_policy_decision(policy, DialoguePolicyInput(messages=messages))
            if provider_name != "fake":
                skipped += 1
                failures += 1
                raw_outputs.append(
                    {
                        "scenario": row.get("id", index),
                        "provider": provider_name,
                        "expected_action": expected_action,
                        "decision": decision.to_dict(),
                        "skipped": True,
                        "skip_reason": "provider is registered for report shape only in offline POC",
                        "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    }
                )
                continue
            no_reply_expected += int(expected_action == "no_reply")
            context = context_builder.build(
                agent_id="benchmark-agent",
                decision=decision,
                messages=[{"role": "user", "content": item} for item in messages],
            )
            result = await router.generate(GenerationRequest(policy=decision, context=context))
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if result.validation.valid:
                valid += 1
            else:
                failures += 1
            if expected_action == "no_reply" and result.selected.action == "no_reply":
                no_reply_correct += 1
            total_length += sum(len(item) for item in result.selected.messages)
            raw_outputs.append(
                {
                    "scenario": row.get("id", index),
                    "provider": provider_name,
                    "expected_action": expected_action,
                    "decision": decision.to_dict(),
                    "result": result.to_dict(),
                    "elapsed_ms": elapsed_ms,
                }
            )
    (output_dir / "raw_outputs.json").write_text(
        json.dumps(raw_outputs, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary = BenchmarkSummary(
        scenarios=total_cases,
        providers=providers,
        valid_outputs=valid,
        invalid_outputs=total_cases - valid - skipped,
        no_reply_accuracy=(no_reply_correct / no_reply_expected if no_reply_expected else 1.0),
        average_output_length=(total_length / total_cases if total_cases else 0.0),
        provider_failure_rate=(failures / total_cases if total_cases else 0.0),
        skipped_outputs=skipped,
        output_dir=str(output_dir),
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary
