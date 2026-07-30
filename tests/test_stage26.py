from __future__ import annotations

import asyncio
import json
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest  # pyright: ignore[reportMissingImports]

from conversation_agent.local_slm.models import GenerationResult
from conversation_agent.local_slm.renderer_registry import (
    GGUF_REVISION,
    LOCAL_RENDERERS,
    SOURCE_REVISION,
    get_renderer_profile,
)
from conversation_agent.local_slm.stage2_dataset import load_frozen_benchmark
from conversation_agent.local_slm.stage25_contract import (
    ResponseContract,
    analyze_incoming_copy,
    validate_renderer_output,
)
from conversation_agent.local_slm.stage25_pipeline import RenderedMessage, Usage
from conversation_agent.local_slm.stage26 import Stage26Options, run_stage26
from conversation_agent.settings import Settings

DATASET = Path("benchmarks/local_slm_stage2_v1/scenarios.jsonl")
EXPECTED_BENCHMARK_FINGERPRINT = (
    "55ed2c40dc8fc5723732a25863ea988f2ecfa7d00471720508eb56c5fc2405f4"
)


def _contract(**overrides: Any) -> ResponseContract:
    value: dict[str, Any] = {
        "action": "reply",
        "goal": "clarify",
        "required_facts": [],
        "forbidden_claims": [],
        "target_bubble_count": 1,
        "max_bubble_count": 1,
        "max_total_characters": 100,
        "max_characters_per_bubble": 100,
        "max_questions": 1,
        "tone": "neutral",
        "formality": 0.5,
        "warmth": 0.5,
        "directness": 0.8,
        "allow_greeting": False,
        "allow_emoji": False,
        "reaction": None,
        "handoff_required": False,
        "confidence": 0.9,
    }
    value.update(overrides)
    return ResponseContract.from_dict(value)


def _result(message: str) -> GenerationResult:
    return GenerationResult(
        action="reply",
        messages=(message,),
        reaction=None,
        handoff_required=False,
        confidence=0.9,
        provider="fixture",
        model="fixture",
        raw_output=message,
    )


def test_registry_keeps_baseline_and_uses_pinned_ruadapt_profiles() -> None:
    assert "qwen3_06b_baseline" in LOCAL_RENDERERS
    assert get_renderer_profile("ruadapt_qwen3_4b_q6").quantization == "Q6_K"
    assert get_renderer_profile("ruadapt_qwen3_4b_q5").quantization == "Q5_K_M"
    assert GGUF_REVISION not in {"main", "master", ""}
    assert SOURCE_REVISION not in {"main", "master", ""}
    generation_mode = next(
        field for field in fields(Settings) if field.name == "generation_mode"
    )
    assert generation_mode.default == "openai_only"


def test_stage2_benchmark_fingerprint_is_unchanged() -> None:
    assert load_frozen_benchmark(DATASET).fingerprint == EXPECTED_BENCHMARK_FINGERPRINT


@pytest.mark.parametrize(
    ("incoming", "output", "rule"),
    [
        (
            "Хочу поговорить с руководителем",
            "Хочу поговорить с руководителем",
            "exact_normalized_copy",
        ),
        (
            "Хочу поговорить с руководителем!",
            "хочу поговорить с руководителем...",
            "exact_normalized_copy",
        ),
        (
            "Хочу поговрить с руководителем",
            "Хочу поговорить с руководителем",
            "near_copy",
        ),
    ],
)
def test_anti_copy_blocks_exact_punctuation_and_near_copy(
    incoming: str,
    output: str,
    rule: str,
) -> None:
    findings = analyze_incoming_copy((output,), (incoming,))
    assert any(item["rule_id"] == rule for item in findings)
    validation = validate_renderer_output(
        _contract(),
        _result(output),
        incoming_messages=(incoming,),
    )
    assert "repeated_incoming_question" in validation.errors
    assert validation.copy_analysis[0]["matched_fragment"]


def test_anti_copy_allows_short_known_fact() -> None:
    incoming = "Работаете с Telegram Mini Apps?"
    output = "Да, работаем с Mini Apps"
    validation = validate_renderer_output(
        _contract(),
        _result(output),
        incoming_messages=(incoming,),
        allowed_facts=("Команда работает с Telegram Mini Apps.",),
    )
    assert "repeated_incoming_question" not in validation.errors


def test_anti_copy_detects_part_of_multi_message_burst() -> None:
    findings = analyze_incoming_copy(
        ("Нужен бот для приёма заявок в Telegram",),
        ("Здравствуйте", "Нужен бот для приёма заявок в Telegram и WhatsApp"),
    )
    assert findings
    assert findings[0]["rule_id"] in {"near_copy", "partial_incoming_copy"}


def test_think_output_is_blocked() -> None:
    validation = validate_renderer_output(
        _contract(),
        _result("<think>Сначала рассужу</think>"),
        incoming_messages=("Что посоветуете?",),
    )
    assert "thinking" in validation.errors


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_stage26_reuses_contracts_and_resume_skips_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Path.cwd()
    dataset_path = (repo / DATASET).resolve()
    benchmark = load_frozen_benchmark(dataset_path)
    source = tmp_path / "stage25"
    output = tmp_path / "stage26"
    _write_json(
        source / "run.json",
        {
            "benchmark_fingerprint": benchmark.fingerprint,
            "policy_version": "gpt_response_contract_v1",
        },
    )
    contract = _contract()
    for scenario in benchmark.scenarios:
        _write_json(
            source / "contracts" / f"{scenario.id}__r1.json",
            {
                "scenario_id": scenario.id,
                "contract": contract.to_dict(),
                "policy_model": "saved-policy",
            },
        )
    profile = get_renderer_profile("ruadapt_qwen3_4b_q6")
    _write_json(
        tmp_path / ".runtime/local_slm/ruadapt-model.json",
        {
            "repository": profile.repository,
            "resolved_revision": profile.revision,
            "filename": profile.filename,
            "quantization": profile.quantization,
            "sha256": "abc123",
            "size_bytes": 123,
        },
    )
    _write_json(
        tmp_path / ".runtime/local_slm/ruadapt-gpu-status.json",
        {"ready": True, "cpu_fallback": False},
    )

    class RecordingRenderer:
        renderer_name = "fixture-renderer"
        model = "fixture-model"

        def __init__(self) -> None:
            self.calls = 0
            self.contexts: list[dict[str, Any]] = []

        async def render(
            self,
            context: Any,
            contract: ResponseContract,
            **_: Any,
        ) -> RenderedMessage:
            self.calls += 1
            self.contexts.append(context.to_prompt_dict())
            return RenderedMessage(
                _result("Понял, уточните детали?"),
                Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            )

    renderer = RecordingRenderer()
    monkeypatch.setattr(
        "conversation_agent.local_slm.stage26._source_commit",
        lambda: "fixture-commit",
    )
    monkeypatch.chdir(tmp_path)
    options = Stage26Options(
        dataset_path=dataset_path,
        renderer="ruadapt_qwen3_4b_q6",
        contracts_from=source,
        baseline_dir=tmp_path / "baseline",
        output_dir=output,
        scenario_limit=1,
        gpu_required=True,
    )
    summary = asyncio.run(
        run_stage26(
            options,
            renderer_override=renderer,
            machine_override={"fixture": True},
        )
    )
    assert renderer.calls == 1
    assert summary["metrics"]["schema_validity_rate"] == 1.0
    assert summary["metrics"]["bubble_count_compliance"] == 1.0
    assert "expected_actions" not in renderer.contexts[0]
    assert "baseline" not in json.dumps(renderer.contexts[0])
    run = json.loads((output / "run.json").read_text(encoding="utf-8"))
    assert run["renderer_profile"]["quantization"] == "Q6_K"
    assert run["model_manifest"]["sha256"] == "abc123"
    assert len(list((output / "contracts").glob("*.json"))) == 1

    resumed = Stage26Options(**{**options.__dict__, "resume": True})
    asyncio.run(
        run_stage26(
            resumed,
            renderer_override=renderer,
            machine_override={"fixture": True},
        )
    )
    assert renderer.calls == 1


def test_gpu_required_rejects_cpu_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = get_renderer_profile("ruadapt_qwen3_4b_q6")
    _write_json(
        tmp_path / ".runtime/local_slm/ruadapt-model.json",
        {
            "repository": profile.repository,
            "resolved_revision": profile.revision,
            "filename": profile.filename,
            "quantization": profile.quantization,
            "sha256": "abc123",
            "size_bytes": 123,
        },
    )
    _write_json(
        tmp_path / ".runtime/local_slm/ruadapt-gpu-status.json",
        {"ready": False, "cpu_fallback": True, "reason": "fixture CPU fallback"},
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match="GPU-required"):
        asyncio.run(
            run_stage26(
                Stage26Options(
                    dataset_path=(Path(__file__).parents[1] / DATASET).resolve(),
                    renderer="ruadapt_qwen3_4b_q6",
                    contracts_from=tmp_path / "unused",
                    baseline_dir=tmp_path / "unused",
                    output_dir=tmp_path / "output",
                    scenario_limit=1,
                    gpu_required=True,
                )
            )
        )
