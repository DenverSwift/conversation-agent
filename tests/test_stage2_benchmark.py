from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from conversation_agent.local_slm.dataset import build_sft_dataset
from conversation_agent.local_slm.models import GenerationRequest, GenerationResult
from conversation_agent.local_slm.stage2_dataset import (
    BENCHMARK_PURPOSE,
    BenchmarkTrainingLeakError,
    benchmark_fingerprint,
    coverage_summary,
    import_private_benchmark,
    load_frozen_benchmark,
)
from conversation_agent.local_slm.stage2_report import generate_stage2_report
from conversation_agent.local_slm.stage2_review import (
    RATING_DIMENSIONS,
    build_blind_pairs,
    deterministic_ab_order,
    reveal_mapping,
    save_human_review,
)
from conversation_agent.local_slm.stage2_review_ui import ReviewUIState
from conversation_agent.local_slm.stage2_runner import (
    Stage2RunOptions,
    _build_request,
    run_stage2_benchmark,
)
from conversation_agent.local_slm.training import training_dry_run

DATASET = Path("benchmarks/local_slm_stage2_v1/scenarios.jsonl")
REQUIRED_CATEGORIES = {
    "new_business_contact",
    "known_business_contact",
    "friendly_chat",
    "multi_message_burst",
    "incomplete_request",
    "simple_question",
    "refusal",
    "missing_information",
    "hallucination_provocation",
    "conflict",
    "irritation",
    "emotional_support",
    "humor",
    "irony",
    "short_acknowledgement",
    "optional_no_reply",
    "appropriate_reaction",
    "human_request",
    "potential_handoff",
    "urgent_request",
    "calm_nonurgent_request",
    "formal_style",
    "informal_style",
    "repeated_question",
    "correction",
    "new_message_during_draft",
    "no_promise",
    "no_price_invention",
    "no_deadline_invention",
    "one_short_clarifying_question",
    "multiple_bubbles",
    "known_contact_short_reply",
    "new_contact_clarity",
    "typo",
    "topic_change",
}


class MockProvider:
    backend_name = "mock"

    def __init__(self, name: str, message: str) -> None:
        self.provider_name = name
        self.model = f"{name}-model"
        self.message = message
        self.calls = 0
        self.contexts: list[str | None] = []

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls += 1
        self.contexts.append(request.semantic_context)
        return GenerationResult(
            action="reply",
            messages=(self.message,),
            confidence=0.8,
            provider=self.provider_name,
            backend=self.backend_name,
            model=self.model,
            raw_output=json.dumps(
                {
                    "action": "reply",
                    "messages": [self.message],
                    "reaction": None,
                    "handoff_required": False,
                    "confidence": 0.8,
                },
                ensure_ascii=False,
            ),
            latency_ms=10,
            prompt_tokens=20,
            completion_tokens=5,
            total_tokens=25,
        )


def _options(
    tmp_path: Path,
    *,
    mode: str = "same_context",
    resume: bool = False,
    retry_errors: bool = False,
) -> Stage2RunOptions:
    return Stage2RunOptions(
        dataset_path=DATASET,
        output_dir=tmp_path / "run",
        mode=mode,
        providers=("local_qwen", "openai_gpt4o_mini"),
        scenario_limit=1,
        resume=resume,
        retry_errors=retry_errors,
    )


def _providers() -> dict[str, MockProvider]:
    return {
        "local_qwen": MockProvider("local_qwen", "локальный ответ"),
        "openai_gpt4o_mini": MockProvider("openai_gpt4o_mini", "ответ gpt"),
    }


def test_frozen_dataset_contains_exactly_100_unique_scenarios() -> None:
    benchmark = load_frozen_benchmark(DATASET)
    assert len(benchmark.scenarios) == 100
    assert len({item.id for item in benchmark.scenarios}) == 100


def test_required_categories_and_minimum_coverages_are_met() -> None:
    benchmark = load_frozen_benchmark(DATASET)
    coverage = coverage_summary(benchmark.scenarios)
    assert REQUIRED_CATEGORIES <= set(coverage["categories"])
    assert coverage["action_coverage"]["reply"] >= 40
    assert coverage["action_coverage"]["no_reply"] >= 15
    assert coverage["action_coverage"]["reaction"] >= 10
    assert coverage["action_coverage"]["handoff"] >= 10
    assert coverage["hallucination_risk"] >= 15
    assert coverage["relationship_profiles"] >= 20
    assert coverage["multi_message_bursts"] >= 15
    assert coverage["conflict_or_emotional"] >= 10


def test_manifest_fingerprint_is_deterministic() -> None:
    benchmark = load_frozen_benchmark(DATASET)
    rows = [item.to_dict() for item in benchmark.scenarios]
    assert benchmark_fingerprint(rows) == benchmark.fingerprint
    assert benchmark_fingerprint(rows) == benchmark_fingerprint(rows)


def test_benchmark_manifest_forbids_training() -> None:
    benchmark = load_frozen_benchmark(DATASET)
    assert benchmark.manifest["purpose"] == BENCHMARK_PURPOSE
    assert benchmark.manifest["allowed_for_training"] is False


def test_dataset_builder_rejects_registered_benchmark_fingerprint(tmp_path: Path) -> None:
    copied = tmp_path / "copied.jsonl"
    copied.write_bytes(DATASET.read_bytes())
    with pytest.raises(BenchmarkTrainingLeakError):
        build_sft_dataset(source_path=copied, output_path=tmp_path / "train.jsonl")


def test_training_cli_rejects_benchmark_manifest(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkTrainingLeakError):
        training_dry_run(
            dataset_path=DATASET,
            base_model="qwen",
            adapter_output_dir=tmp_path / "adapter",
        )


def test_system_comparison_uses_different_context_pipelines() -> None:
    benchmark = load_frozen_benchmark(DATASET)
    scenario = benchmark.scenarios[0]
    options = _options(Path(".runtime/test-stage2"), mode="system_comparison")
    local_request, local_record = _build_request(
        scenario,
        mode="system_comparison",
        provider_name="local_qwen",
        options=options,
    )
    openai_request, openai_record = _build_request(
        scenario,
        mode="system_comparison",
        provider_name="openai_gpt4o_mini",
        options=options,
    )
    assert local_record["pipeline"] == "local_context_builder"
    assert openai_record["pipeline"] == "openai_product_prompt_v1"
    assert local_request.semantic_context != openai_request.semantic_context


def test_same_context_uses_equivalent_normalized_semantic_context() -> None:
    scenario = load_frozen_benchmark(DATASET).scenarios[0]
    options = _options(Path(".runtime/test-stage2"))
    local, local_record = _build_request(
        scenario,
        mode="same_context",
        provider_name="local_qwen",
        options=options,
    )
    openai, openai_record = _build_request(
        scenario,
        mode="same_context",
        provider_name="openai_gpt4o_mini",
        options=options,
    )
    assert local.semantic_context == openai.semantic_context
    assert local.system_prompt == openai.system_prompt
    assert local.allowed_actions == openai.allowed_actions
    assert local_record["pipeline"] == openai_record["pipeline"]


def test_resume_does_not_repeat_completed_provider_calls(tmp_path: Path) -> None:
    providers = _providers()
    asyncio.run(
        run_stage2_benchmark(
            _options(tmp_path),
            provider_overrides=providers,
            machine_override={"test": True},
        )
    )
    assert providers["local_qwen"].calls == 1
    assert providers["openai_gpt4o_mini"].calls == 1
    asyncio.run(
        run_stage2_benchmark(
            _options(tmp_path, resume=True),
            provider_overrides=providers,
            machine_override={"test": True},
        )
    )
    assert providers["local_qwen"].calls == 1
    assert providers["openai_gpt4o_mini"].calls == 1


def test_run_fingerprint_is_reproducible_across_output_directories(
    tmp_path: Path,
) -> None:
    first_options = _options(tmp_path / "first")
    second_options = _options(tmp_path / "second")
    first = asyncio.run(
        run_stage2_benchmark(
            first_options,
            provider_overrides=_providers(),
            machine_override={"test": True},
        )
    )
    second = asyncio.run(
        run_stage2_benchmark(
            second_options,
            provider_overrides=_providers(),
            machine_override={"test": True},
        )
    )
    assert first["run_fingerprint"] == second["run_fingerprint"]


def test_result_is_written_atomically_and_contains_no_api_key(tmp_path: Path) -> None:
    providers = _providers()
    asyncio.run(
        run_stage2_benchmark(
            _options(tmp_path),
            provider_overrides=providers,
            machine_override={"test": True},
        )
    )
    result_files = list((tmp_path / "run" / "results").rglob("*.json"))
    assert len(result_files) == 2
    assert not list((tmp_path / "run").rglob("*.tmp"))
    artifact_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "run").rglob("*.json")
    )
    assert "OPENAI_API_KEY" not in artifact_text
    assert "sk-" not in artifact_text


def test_invalid_output_is_saved_as_validation_failure(tmp_path: Path) -> None:
    class InvalidProvider(MockProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResult:
            self.calls += 1
            return GenerationResult(
                action="reply",
                messages=(),
                provider=self.provider_name,
                model=self.model,
                raw_output="{}",
            )

    providers: dict[str, MockProvider] = {
        "local_qwen": InvalidProvider("local_qwen", ""),
        "openai_gpt4o_mini": MockProvider("openai_gpt4o_mini", "ok"),
    }
    asyncio.run(
        run_stage2_benchmark(
            _options(tmp_path),
            provider_overrides=providers,
            machine_override={"test": True},
        )
    )
    local_result = json.loads(
        next((tmp_path / "run" / "results" / "local_qwen").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert local_result["validation"]["valid"] is False
    assert "empty_reply" in local_result["validation"]["errors"]


def test_fake_provider_cannot_be_selected_for_real_stage2_run(tmp_path: Path) -> None:
    options = Stage2RunOptions(
        dataset_path=DATASET,
        output_dir=tmp_path,
        mode="same_context",
        providers=("fake",),
    )
    with pytest.raises(ValueError, match="unsupported real providers"):
        asyncio.run(run_stage2_benchmark(options, provider_overrides={}))


def test_blind_payload_hides_provider_latency_and_tokens(tmp_path: Path) -> None:
    asyncio.run(
        run_stage2_benchmark(
            _options(tmp_path),
            provider_overrides=_providers(),
            machine_override={"test": True},
        )
    )
    pair = build_blind_pairs(tmp_path / "run", seed=42)[0]
    serialized = json.dumps(pair.payload)
    assert "provider" not in serialized
    assert "latency" not in serialized
    assert "token" not in serialized
    assert pair.candidate_a_provider not in serialized
    assert pair.candidate_b_provider not in serialized


def test_ab_randomization_is_reproducible() -> None:
    first = deterministic_ab_order(scenario_id="business-001", repetition=1, seed=42)
    second = deterministic_ab_order(scenario_id="business-001", repetition=1, seed=42)
    assert first == second


def test_reveal_does_not_modify_saved_review(tmp_path: Path) -> None:
    asyncio.run(
        run_stage2_benchmark(
            _options(tmp_path),
            provider_overrides=_providers(),
            machine_override={"test": True},
        )
    )
    run_dir = tmp_path / "run"
    pair = build_blind_pairs(run_dir, seed=42)[0]
    review_path = save_human_review(
        run_dir=run_dir,
        reviewer="tester",
        pair=pair,
        ratings=_ratings(),
    )
    before = review_path.read_bytes()
    mapping = reveal_mapping(run_dir, seed=42)
    assert pair.pair_id in mapping
    assert review_path.read_bytes() == before


def test_human_review_is_saved_after_each_scenario(tmp_path: Path) -> None:
    asyncio.run(
        run_stage2_benchmark(
            _options(tmp_path),
            provider_overrides=_providers(),
            machine_override={"test": True},
        )
    )
    run_dir = tmp_path / "run"
    pair = build_blind_pairs(run_dir, seed=42)[0]
    path = save_human_review(
        run_dir=run_dir,
        reviewer="tester",
        pair=pair,
        ratings=_ratings(),
    )
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8"))["blind"] is True


def test_review_ui_saves_three_point_button_ratings(tmp_path: Path) -> None:
    asyncio.run(
        run_stage2_benchmark(
            _options(tmp_path),
            provider_overrides=_providers(),
            machine_override={"test": True},
        )
    )
    state = ReviewUIState(
        run_dir=tmp_path / "run",
        reviewer="ui-reviewer",
        seed=42,
    )
    candidate: dict[str, Any] = {
        dimension: 3 for dimension in RATING_DIMENSIONS
    }
    candidate.update(
        {
            "correct_action": "yes",
            "hallucination": "no",
            "bot_like": "no",
            "needs_human_edit": "minor",
        }
    )
    pair_id = state.pairs[0].pair_id
    result = state.save(
        pair_id,
        {
            "winner": "tie_good",
            "candidate_A": dict(candidate),
            "candidate_B": dict(candidate),
        },
    )
    snapshot = state.snapshot()
    assert result["saved"] is True
    assert result["reviewed"] == 1
    assert snapshot["items"][0]["reviewed"] is True
    assert snapshot["items"][0]["ratings"]["candidate_A"]["naturalness"] == 3
    serialized = json.dumps(snapshot)
    assert "local_qwen" not in serialized
    assert "openai_gpt4o_mini" not in serialized


def test_report_handles_incomplete_human_review(tmp_path: Path) -> None:
    asyncio.run(
        run_stage2_benchmark(
            _options(tmp_path),
            provider_overrides=_providers(),
            machine_override={"test": True},
        )
    )
    run_dir = tmp_path / "run"
    result = generate_stage2_report(
        run_dir=run_dir,
        reviews_dir=run_dir / "reviews",
        output_dir=run_dir / "report",
    )
    assert result["review_complete"] is False
    report = (run_dir / "report" / "report.md").read_text(encoding="utf-8")
    assert "Human evaluation incomplete" in report
    assert "No winner is declared" in report


def test_assistant_phrase_flags_are_calculated(tmp_path: Path) -> None:
    providers = {
        "local_qwen": MockProvider(
            "local_qwen",
            "Благодарю вас за обращение",
        ),
        "openai_gpt4o_mini": MockProvider("openai_gpt4o_mini", "Коротко"),
    }
    asyncio.run(
        run_stage2_benchmark(
            _options(tmp_path),
            provider_overrides=providers,
            machine_override={"test": True},
        )
    )
    local_result = json.loads(
        next((tmp_path / "run" / "results" / "local_qwen").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert local_result["automatic_evaluation"]["assistant_phrase_flags"]


def test_exact_target_response_is_not_required() -> None:
    rows = [
        json.loads(line)
        for line in DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert all("target" not in row and "target_response" not in row for row in rows)


def test_private_import_stays_under_runtime_and_is_training_forbidden(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "private.jsonl"
    source.write_text(
        json.dumps(
            {
                "id": "private-1",
                "conversation": [{"role": "contact", "messages": ["mail a@b.com"]}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / ".runtime" / "benchmarks" / "private-v1"
    result = import_private_benchmark(
        input_path=source,
        output_dir=output,
        anonymize=True,
        purpose="benchmark_only",
    )
    assert Path(result["output"]).is_relative_to((tmp_path / ".runtime").resolve())
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["allowed_for_training"] is False
    assert "[EMAIL]" in (output / "scenarios.jsonl").read_text(encoding="utf-8")


def test_private_import_rejects_output_outside_runtime(tmp_path: Path) -> None:
    source = tmp_path / "private.jsonl"
    source.write_text('{"id":"x"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="under .runtime"):
        import_private_benchmark(
            input_path=source,
            output_dir=tmp_path / "tracked",
            anonymize=True,
            purpose="benchmark_only",
        )


def _ratings() -> dict[str, Any]:
    candidate: dict[str, Any] = {dimension: 4 for dimension in RATING_DIMENSIONS}
    candidate.update(
        {
            "correct_action": "yes",
            "hallucination": "no",
            "bot_like": "no",
            "needs_human_edit": "minor",
        }
    )
    return {
        "winner": "tie_good",
        "candidate_A": dict(candidate),
        "candidate_B": dict(candidate),
        "note": "",
    }
