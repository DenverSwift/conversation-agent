from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from conversation_agent.local_slm.models import GenerationResult
from conversation_agent.local_slm.stage2_dataset import (
    BenchmarkScenario,
    load_frozen_benchmark,
)
from conversation_agent.local_slm.stage25_contract import (
    CONTRACT_ACTIONS,
    ResponseContract,
    ResponseContractError,
    renderer_response_schema,
    response_contract_schema,
    validate_renderer_output,
)
from conversation_agent.local_slm.stage25_diagnostics import generate_diagnostic_pack
from conversation_agent.local_slm.stage25_pipeline import (
    GPTContractPolicy,
    PolicyContext,
    PolicyPlan,
    RenderedMessage,
    Usage,
    execute_contract_pipeline,
)
from conversation_agent.local_slm.stage25_runner import (
    QUICK_COVERAGE,
    Stage25RunOptions,
    _select_scenarios,
    run_stage25_benchmark,
)

DATASET = Path("benchmarks/local_slm_stage2_v1/scenarios.jsonl")


def _contract(**overrides: Any) -> ResponseContract:
    value: dict[str, Any] = {
        "action": "reply",
        "goal": "коротко уточнить задачу",
        "required_facts": [],
        "forbidden_claims": ["не обещать срок"],
        "target_bubble_count": 1,
        "max_bubble_count": 2,
        "max_total_characters": 120,
        "max_characters_per_bubble": 100,
        "max_questions": 1,
        "tone": "спокойный",
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


def _context() -> PolicyContext:
    return PolicyContext(
        conversation=(
            {"role": "contact", "content": "Сколько будет стоить бот?"},
        ),
        relationship={"type": "new_contact", "formality": 0.5},
        known_facts=(),
        restrictions=("не обещать срок",),
        goal="уточнить задачу",
    )


def _result(
    messages: tuple[str, ...] = ("Какой бот вам нужен?",),
    **overrides: Any,
) -> GenerationResult:
    value: dict[str, Any] = {
        "action": "reply",
        "messages": messages,
        "reaction": None,
        "handoff_required": False,
        "confidence": 0.8,
        "provider": "mock",
        "model": "mock",
        "raw_output": json.dumps({"messages": list(messages)}, ensure_ascii=False),
    }
    value.update(overrides)
    return GenerationResult(**value)


def test_response_contract_schema_has_no_final_message_field() -> None:
    schema = response_contract_schema()
    assert set(schema["properties"]) >= {
        "action",
        "goal",
        "target_bubble_count",
        "max_total_characters",
    }
    assert "messages" not in schema["properties"]
    assert set(schema["properties"]["action"]["enum"]) == set(CONTRACT_ACTIONS)


@pytest.mark.parametrize(
    ("action", "overrides", "error"),
    [
        (
            "no_reply",
            {
                "target_bubble_count": 1,
                "max_bubble_count": 1,
                "max_total_characters": 20,
                "max_characters_per_bubble": 20,
                "max_questions": 0,
            },
            "no_reply_target_bubbles",
        ),
        (
            "reaction",
            {
                "target_bubble_count": 0,
                "max_bubble_count": 0,
                "max_total_characters": 0,
                "max_characters_per_bubble": 0,
                "max_questions": 0,
                "reaction": None,
            },
            "reaction_missing",
        ),
        (
            "handoff",
            {
                "target_bubble_count": 0,
                "max_bubble_count": 1,
                "max_total_characters": 90,
                "max_characters_per_bubble": 90,
                "max_questions": 0,
                "handoff_required": False,
            },
            "handoff_required_false",
        ),
    ],
)
def test_invalid_action_contracts_are_rejected(
    action: str,
    overrides: dict[str, Any],
    error: str,
) -> None:
    with pytest.raises(ResponseContractError) as caught:
        _contract(action=action, **overrides)
    assert error in caught.value.errors


def test_renderer_schema_is_bound_to_contract() -> None:
    contract = _contract(max_bubble_count=2, max_characters_per_bubble=80)
    schema = renderer_response_schema(contract)
    assert schema["properties"]["action"]["const"] == "reply"
    assert schema["properties"]["messages"]["maxItems"] == 2
    assert schema["properties"]["messages"]["items"]["maxLength"] == 80


@pytest.mark.parametrize(
    ("contract", "result", "expected_error"),
    [
        (_contract(max_bubble_count=1), _result(("один", "два")), "bubble_count"),
        (
            _contract(max_total_characters=20),
            _result(("это намеренно слишком длинный ответ",)),
            "total_characters",
        ),
        (
            _contract(max_characters_per_bubble=10),
            _result(("слишком длинно",)),
            "characters_per_bubble",
        ),
        (_contract(max_questions=0), _result(("Что нужно?",)), "question_count"),
        (_contract(allow_greeting=False), _result(("Здравствуйте! Что нужно?",)), "greeting"),
        (_contract(allow_emoji=False), _result(("Сделаем 👍",)), "emoji"),
        (
            _contract(forbidden_claims=["стоит 5000"]),
            _result(("Это стоит 5000 рублей",)),
            "forbidden_claims",
        ),
        (
            _contract(required_facts=["работаем по договору"]),
            _result(("Расскажите подробнее",)),
            "required_facts",
        ),
        (
            _contract(),
            _result(("Сколько будет стоить бот?",)),
            "repeated_incoming_question",
        ),
    ],
)
def test_hard_validator_reports_each_contract_violation(
    contract: ResponseContract,
    result: GenerationResult,
    expected_error: str,
) -> None:
    validation = validate_renderer_output(
        contract,
        result,
        incoming_messages=("Сколько будет стоить бот?",),
    )
    assert validation.valid is False
    assert expected_error in validation.errors


def test_policy_context_excludes_benchmark_expected_actions() -> None:
    scenario = BenchmarkScenario(
        id="x",
        category="simple_question",
        tags=(),
        language="ru",
        agent_profile="informal_manager",
        relationship={"type": "new_contact"},
        conversation=({"role": "contact", "messages": ["Привет"]},),
        known_facts=(),
        goal="respond",
        expected_actions=("no_reply",),
        required_facts=(),
        forbidden_claims=(),
        min_bubbles=0,
        max_bubbles=1,
        max_total_chars=50,
        evaluation_notes="secret evaluation label",
    )
    serialized = json.dumps(
        PolicyContext.from_scenario(scenario).to_prompt_dict(),
        ensure_ascii=False,
    )
    assert "expected_actions" not in serialized
    assert "no_reply" not in serialized
    assert "secret evaluation label" not in serialized


def test_renderer_retry_does_not_repeat_policy() -> None:
    class MockPolicy:
        provider_name = "mock-policy"

        def __init__(self) -> None:
            self.calls = 0

        async def plan(self, context: PolicyContext) -> PolicyPlan:
            self.calls += 1
            return PolicyPlan(
                contract=_contract(),
                latency_ms=1,
                model="policy",
                raw_output="{}",
            )

    class MockRenderer:
        renderer_name = "local_qwen_renderer"
        model = "local"

        def __init__(self) -> None:
            self.calls = 0
            self.contracts: list[ResponseContract] = []

        async def render(
            self,
            context: PolicyContext,
            contract: ResponseContract,
            *,
            previous_output: str = "",
            repair_errors: tuple[str, ...] = (),
        ) -> RenderedMessage:
            self.calls += 1
            self.contracts.append(contract)
            messages = (
                ("Здравствуйте! Какой бот вам нужен?",)
                if self.calls == 1
                else ("Какой бот вам нужен?",)
            )
            return RenderedMessage(result=_result(messages), usage=Usage())

    policy = MockPolicy()
    renderer = MockRenderer()
    result = asyncio.run(
        execute_contract_pipeline(
            policy=policy,
            renderer=renderer,
            context=_context(),
        )
    )
    assert policy.calls == 1
    assert renderer.calls == 2
    assert renderer.contracts == [result.contract, result.contract]
    assert result.renderer_retry_count == 1
    assert result.renderer_validation.valid is True
    assert result.renderer_name == "local_qwen_renderer"


def test_policy_repairs_invalid_contract_without_writing_a_message() -> None:
    invalid = _contract().to_dict()
    invalid.update(
        {
            "action": "reaction",
            "target_bubble_count": 0,
            "max_bubble_count": 0,
            "max_total_characters": 0,
            "max_characters_per_bubble": 0,
            "max_questions": 0,
            "reaction": None,
        }
    )
    valid = _contract().to_dict()

    class FakeReply:
        def __init__(self, value: dict[str, Any], token_count: int) -> None:
            self.text = json.dumps(value, ensure_ascii=False)
            self.model = "mock-policy"
            self.prompt_tokens = token_count
            self.completion_tokens = token_count
            self.total_tokens = token_count * 2

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0
            self.contents: list[str] = []

        async def create_structured_reply(self, **kwargs: Any) -> FakeReply:
            self.contents.append(str(kwargs["messages"][0]["content"]))
            value = invalid if self.calls == 0 else valid
            self.calls += 1
            return FakeReply(value, self.calls)

    policy = GPTContractPolicy(api_key="test")
    client = FakeClient()
    policy._client = client
    plan = asyncio.run(policy.plan(_context()))
    assert policy.calls == 1
    assert client.calls == 2
    assert "reaction_missing" in client.contents[1]
    assert plan.contract.action == "reply"
    assert "messages" not in plan.raw_output
    assert plan.usage.total_tokens == 6


def test_no_reply_short_circuits_renderer() -> None:
    contract = _contract(
        action="no_reply",
        target_bubble_count=0,
        max_bubble_count=0,
        max_total_characters=0,
        max_characters_per_bubble=0,
        max_questions=0,
        forbidden_claims=[],
    )

    class MockPolicy:
        provider_name = "mock-policy"
        calls = 0

        async def plan(self, context: PolicyContext) -> PolicyPlan:
            self.calls += 1
            return PolicyPlan(contract=contract, latency_ms=1, model="policy", raw_output="{}")

    class MockRenderer:
        renderer_name = "local_qwen_renderer"
        model = "local"
        calls = 0

        async def render(
            self,
            context: PolicyContext,
            contract: ResponseContract,
            *,
            previous_output: str = "",
            repair_errors: tuple[str, ...] = (),
        ) -> RenderedMessage:
            self.calls += 1
            raise AssertionError("renderer must not be called")

    renderer = MockRenderer()
    result = asyncio.run(
        execute_contract_pipeline(
            policy=MockPolicy(),
            renderer=renderer,
            context=_context(),
        )
    )
    assert renderer.calls == 0
    assert result.output.action == "no_reply"
    assert result.renderer_validation.valid is True


class RunnerPolicy:
    provider_name = "mock-policy"

    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, context: PolicyContext) -> PolicyPlan:
        self.calls += 1
        contract = _contract(
            forbidden_claims=list(context.restrictions),
            allow_greeting=False,
        )
        return PolicyPlan(
            contract=contract,
            latency_ms=2,
            model="mock-policy",
            raw_output=json.dumps(contract.to_dict(), ensure_ascii=False),
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


class RunnerRenderer:
    model = "mock-renderer"

    def __init__(self, name: str) -> None:
        self.renderer_name = name
        self.calls = 0

    async def render(
        self,
        context: PolicyContext,
        contract: ResponseContract,
        *,
        previous_output: str = "",
        repair_errors: tuple[str, ...] = (),
    ) -> RenderedMessage:
        self.calls += 1
        return RenderedMessage(
            result=_result(("Что именно нужно сделать?",), provider=self.renderer_name),
            usage=Usage(prompt_tokens=20, completion_tokens=6, total_tokens=26),
        )


def test_stage25_resume_reuses_contract_and_completed_results(tmp_path: Path) -> None:
    policy = RunnerPolicy()
    openai_renderer = RunnerRenderer("openai_renderer")
    local_renderer = RunnerRenderer("local_qwen_renderer")
    options = Stage25RunOptions(
        dataset_path=DATASET,
        output_dir=tmp_path / "run",
        pipelines=(
            "gpt_policy_openai_renderer",
            "gpt_policy_local_renderer",
        ),
        scenario_limit=1,
        gpu_required=False,
    )
    asyncio.run(
        run_stage25_benchmark(
            options,
            policy_override=policy,
            renderer_overrides={
                "gpt_policy_openai_renderer": openai_renderer,
                "gpt_policy_local_renderer": local_renderer,
            },
            machine_override={"test": True},
        )
    )
    assert policy.calls == 1
    assert openai_renderer.calls == 1
    assert local_renderer.calls == 1
    asyncio.run(
        run_stage25_benchmark(
            replace(options, resume=True),
            policy_override=policy,
            renderer_overrides={
                "gpt_policy_openai_renderer": openai_renderer,
                "gpt_policy_local_renderer": local_renderer,
            },
            machine_override={"test": True},
        )
    )
    assert policy.calls == 1
    assert openai_renderer.calls == 1
    assert local_renderer.calls == 1
    artifacts = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "run").rglob("*.json")
    )
    assert "OPENAI_API_KEY" not in artifacts
    assert "sk-" not in artifacts


def test_stage25_imports_direct_baseline_without_provider_calls(
    tmp_path: Path,
) -> None:
    benchmark = load_frozen_benchmark(DATASET)
    scenario = next(
        item
        for item in benchmark.scenarios
        if "optional_no_reply" in item.all_categories
    )
    baseline = tmp_path / "baseline"
    baseline.joinpath("results", "openai_gpt4o_mini").mkdir(parents=True)
    baseline.joinpath("run.json").write_text(
        json.dumps({"benchmark_fingerprint": benchmark.fingerprint}),
        encoding="utf-8",
    )
    baseline_record = {
        "scenario_id": scenario.id,
        "provider": "openai_gpt4o_mini",
        "run_fingerprint": "stage2-run",
        "normalized_output": _result().to_dict(),
        "validation": {"valid": True, "errors": []},
        "automatic_evaluation": {"expected_action_match": True},
        "latency_ms": 100,
        "expected_actions": list(scenario.expected_actions),
    }
    baseline.joinpath(
        "results",
        "openai_gpt4o_mini",
        f"{scenario.id}__r1.json",
    ).write_text(json.dumps(baseline_record), encoding="utf-8")
    output = tmp_path / "run"
    asyncio.run(
        run_stage25_benchmark(
            Stage25RunOptions(
                dataset_path=DATASET,
                output_dir=output,
                pipelines=("openai_direct",),
                baseline_dir=baseline,
                scenario_limit=1,
                gpu_required=False,
            ),
            machine_override={"test": True},
        )
    )
    imported = json.loads(
        next(output.joinpath("results", "openai_direct").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert imported["baseline_reference"] is True
    assert imported["baseline_source_run_fingerprint"] == "stage2-run"


def test_gpu_required_run_refuses_unconfirmed_cpu_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = DATASET.resolve()
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match="CUDA offload is not confirmed"):
        asyncio.run(
            run_stage25_benchmark(
                Stage25RunOptions(
                    dataset_path=dataset,
                    output_dir=tmp_path / "run",
                    pipelines=("gpt_policy_local_renderer",),
                    gpu_required=True,
                ),
                policy_override=RunnerPolicy(),
                renderer_overrides={
                    "gpt_policy_local_renderer": RunnerRenderer(
                        "local_qwen_renderer"
                    )
                },
                machine_override={"test": True},
            )
        )


def test_quick_selection_covers_required_scenario_groups() -> None:
    benchmark = load_frozen_benchmark(DATASET)
    selected = _select_scenarios(
        benchmark,
        Stage25RunOptions(
            dataset_path=DATASET,
            output_dir=Path(".runtime/test-stage25"),
            pipelines=("openai_direct",),
            scenario_limit=20,
            gpu_required=False,
        ),
    )
    covered = set().union(*(scenario.all_categories for scenario in selected))
    assert set(QUICK_COVERAGE) <= covered
    assert len(selected) == 20


def _write_diagnostic_pair(
    run_dir: Path,
    *,
    scenario_id: str,
    expected: str,
    qwen: dict[str, Any],
    gpt: dict[str, Any],
) -> None:
    scenario = {
        "id": scenario_id,
        "category": f"category-{scenario_id}",
        "relationship": {"type": "new_contact"},
        "conversation": [{"role": "contact", "messages": ["Тестовое сообщение"]}],
        "known_facts": [],
        "forbidden_claims": ["не выдумывать"],
        "expected_actions": [expected],
    }
    for provider, candidate in (
        ("local_qwen", qwen),
        ("openai_gpt4o_mini", gpt),
    ):
        path = run_dir / "results" / provider / f"{scenario_id}__r1.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "scenario_id": scenario_id,
            "category": scenario["category"],
            "provider": provider,
            "scenario": scenario,
            "expected_actions": [expected],
            "normalized_output": {
                "action": candidate["action"],
                "messages": candidate.get("messages", []),
                "reaction": candidate.get("reaction"),
                "handoff_required": candidate.get("handoff_required", False),
            },
            "validation": {
                "valid": not candidate.get("validation_errors"),
                "errors": candidate.get("validation_errors", []),
            },
            "automatic_evaluation": candidate.get("evaluation", {}),
        }
        if candidate.get("provider_error"):
            record["provider_error"] = candidate["provider_error"]
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


def test_diagnostic_pack_is_deterministic_unique_and_covers_error_groups(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _write_diagnostic_pair(
        run_dir,
        scenario_id="s1",
        expected="no_reply",
        qwen={
            "action": "reply",
            "messages": ["Очень длинный ответ? " * 25],
            "evaluation": {
                "bubble_count_compliance": False,
                "character_count": 400,
                "assistant_phrase_flags": [
                    "unnecessary_question_repetition",
                    "assistant_like_phrase",
                ],
                "unsupported_fact_flags": ["unsupported"],
                "forbidden_claims": ["не выдумывать"],
            },
        },
        gpt={"action": "no_reply", "messages": []},
    )
    _write_diagnostic_pair(
        run_dir,
        scenario_id="s2",
        expected="handoff",
        qwen={
            "action": "reply",
            "messages": ["Отвечу сам"],
            "provider_error": "timeout",
        },
        gpt={"action": "handoff", "messages": [], "handoff_required": True},
    )
    _write_diagnostic_pair(
        run_dir,
        scenario_id="s3",
        expected="reaction",
        qwen={"action": "reply", "messages": ["Ок"]},
        gpt={"action": "reaction", "messages": [], "reaction": "👍"},
    )
    _write_diagnostic_pair(
        run_dir,
        scenario_id="s4",
        expected="reply",
        qwen={"action": "reply", "messages": ["Короткий нормальный ответ"]},
        gpt={
            "action": "reply",
            "messages": ["Слишком длинный ответ " * 20],
            "evaluation": {
                "bubble_count_compliance": False,
                "character_count": 350,
            },
        },
    )
    _write_diagnostic_pair(
        run_dir,
        scenario_id="s5",
        expected="no_reply",
        qwen={"action": "reply", "messages": ["Лишний ответ"]},
        gpt={"action": "reply", "messages": ["Тоже лишний ответ"]},
    )
    _write_diagnostic_pair(
        run_dir,
        scenario_id="s6",
        expected="reply",
        qwen={"action": "reply", "messages": ["Нормально"]},
        gpt={"action": "reply", "messages": ["Тоже нормально"]},
    )
    first = tmp_path / "first"
    second = tmp_path / "second"
    generate_diagnostic_pack(
        run_dir=run_dir,
        output_dir=first,
        max_examples=40,
        seed=42,
    )
    generate_diagnostic_pack(
        run_dir=run_dir,
        output_dir=second,
        max_examples=40,
        seed=42,
    )
    assert (first / "examples.json").read_bytes() == (
        second / "examples.json"
    ).read_bytes()
    examples = json.loads((first / "examples.json").read_text(encoding="utf-8"))
    scenario_ids = [item["scenario_id"] for item in examples]
    assert len(scenario_ids) == len(set(scenario_ids))
    reasons = {reason for item in examples for reason in item["reasons"]}
    assert {
        "wrong_action",
        "missed_no_reply",
        "missed_handoff",
        "wrong_reaction",
        "hallucination_or_unsupported_fact",
        "forbidden_claim",
        "provider_failure",
        "best_qwen",
        "gpt_understands_but_too_long",
        "both_bad",
        "random_control",
    } <= reasons
