from __future__ import annotations

import asyncio
import json
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest  # pyright: ignore[reportMissingImports]

from conversation_agent.local_slm.models import GenerationResult
from conversation_agent.local_slm.private_style_dataset import (
    InMemoryStyleFeedbackRepository,
    StyleDatasetError,
    StyleFeedbackEvent,
    TrainingExample,
    add_style_examples,
    build_style_dataset,
    init_style_dataset,
    validate_example,
    validate_style_dataset,
)
from conversation_agent.local_slm.renderer_registry import get_renderer_profile
from conversation_agent.local_slm.stage2_dataset import load_frozen_benchmark
from conversation_agent.local_slm.stage3a import (
    EXPECTED_STAGE26_SNAPSHOT,
    Stage3AOptions,
    Stage3ARendered,
    run_stage3a,
)
from conversation_agent.local_slm.stage3a_contract import (
    AdaptiveStyleResolver,
    AgentStyleProfile,
    HardSemanticValidator,
    RelationshipStyleProfile,
    SafetyConstraints,
    SafetyValidator,
    SoftStyleEvaluator,
    StyleEvidence,
    StyleFeatureExtractor,
    empty_style_statistics,
    evidence_from_human_message,
    migrate_v1_to_semantic,
    migrate_v1_to_v2,
    response_contract_v2_schema,
)
from conversation_agent.local_slm.stage25_contract import ResponseContract
from conversation_agent.local_slm.stage25_pipeline import Usage
from conversation_agent.settings import Settings

DATASET = Path("benchmarks/local_slm_stage2_v1/scenarios.jsonl")
BENCHMARK_FINGERPRINT = (
    "55ed2c40dc8fc5723732a25863ea988f2ecfa7d00471720508eb56c5fc2405f4"
)


def _v1(**overrides: Any) -> ResponseContract:
    value: dict[str, Any] = {
        "action": "reply",
        "goal": "respond",
        "required_facts": [],
        "forbidden_claims": [],
        "target_bubble_count": 1,
        "max_bubble_count": 2,
        "max_total_characters": 120,
        "max_characters_per_bubble": 100,
        "max_questions": 1,
        "tone": "neutral",
        "formality": 0.5,
        "warmth": 0.5,
        "directness": 0.7,
        "allow_greeting": True,
        "allow_emoji": True,
        "reaction": None,
        "handoff_required": False,
        "confidence": 0.8,
    }
    value.update(overrides)
    return ResponseContract.from_dict(value)


def _profile(
    *,
    agent_id: str = "agent",
    source_type: str = "human_manual",
    messages: tuple[str, ...] = (),
    count: int = 1,
) -> AgentStyleProfile:
    extractor = StyleFeatureExtractor()
    evidence = tuple(
        evidence_from_human_message(
            evidence_id=f"e-{index}",
            message_id=f"m-{index}",
            source_type=source_type,
            bubbles=messages,
        )
        for index in range(count)
    )
    return AgentStyleProfile(agent_id, extractor.profile(evidence))


def _relationship(
    messages: tuple[str, ...] = (),
    *,
    count: int = 1,
) -> RelationshipStyleProfile:
    profile = _profile(messages=messages, count=count)
    return RelationshipStyleProfile(
        "agent",
        "colleague",
        "contact",
        profile.statistics,
    )


def _contract_for(
    incoming: tuple[str, ...],
    *,
    formality: float,
    agent: AgentStyleProfile | None = None,
    relationship: RelationshipStyleProfile | None = None,
) -> Any:
    extractor = StyleFeatureExtractor()
    return migrate_v1_to_v2(
        _v1(),
        resolver=AdaptiveStyleResolver(),
        agent_profile=agent or AgentStyleProfile("agent", empty_style_statistics()),
        relationship_profile=relationship
        or RelationshipStyleProfile(
            "agent",
            "unknown",
            None,
            empty_style_statistics(),
        ),
        conversation=extractor.conversation_snapshot(
            conversation_id="turn",
            messages=incoming,
        ),
        relationship_context={
            "formality": formality,
            "warmth": 0.5,
            "directness": 0.8,
        },
    )


def _output(text: str, *, action: str = "reply") -> GenerationResult:
    return GenerationResult(
        action=action,  # type: ignore[arg-type]
        messages=(text,) if text else (),
        reaction=None,
        handoff_required=False,
        confidence=0.8,
        provider="fixture",
        raw_output=text,
    )


def test_contract_v2_separates_semantics_style_and_safety() -> None:
    contract = _contract_for(("привет",), formality=0.3)
    value = contract.to_dict()
    assert value["version"] == 2
    assert set(value) == {"version", "semantic", "style", "safety"}
    assert "casing" not in value["semantic"]
    assert "forbidden_claims" not in value["style"]
    assert response_contract_v2_schema()["properties"]["version"]["const"] == 2


def test_old_contract_is_readable_and_migrates_without_rewriting() -> None:
    old = _v1(required_facts=["работаем с Mini Apps"])
    semantic = migrate_v1_to_semantic(old, known_facts=("работаем с Mini Apps",))
    assert semantic.required_information == ("работаем с Mini Apps",)
    assert semantic.allowed_facts == ("работаем с Mini Apps",)
    assert old.to_dict()["target_bubble_count"] == 1


def test_business_004_lowercase_is_per_turn_adaptation() -> None:
    contract = _contract_for(
        ("привет", "бота для заказов делаешь?"),
        formality=0.3,
    )
    assert contract.style.casing_mode == "lowercase"
    assert contract.style.evidence_ids
    assert contract.style.confidence > 0
    assert any("turn" in reason for reason in contract.style.reasons)


def test_formal_context_does_not_inherit_lowercase_rule() -> None:
    contract = _contract_for(
        ("Здравствуйте", "Подскажите, вы разрабатываете ботов?"),
        formality=0.85,
    )
    assert contract.style.casing_mode == "normal"


def test_one_lowercase_contact_message_does_not_override_stable_agent() -> None:
    stable = _profile(messages=("Здравствуйте. Всё подготовлено.",), count=10)
    contract = _contract_for(
        ("привет",),
        formality=0.3,
        agent=stable,
    )
    assert contract.style.casing_mode == "normal"
    assert "stable agent profile" in contract.style.reasons[0]


def test_relationship_and_conversation_affect_each_turn() -> None:
    relationship = _relationship(("ага, готово",), count=6)
    informal = _contract_for(
        ("ну че там",),
        formality=0.25,
        relationship=relationship,
    )
    formal = _contract_for(
        ("Здравствуйте",),
        formality=0.9,
        relationship=relationship,
    )
    assert informal.style.casing_mode == "lowercase"
    assert formal.style.casing_mode == "normal"
    assert informal.style.preferred_character_range != formal.style.preferred_character_range
    assert informal.style.source_weights != {}


def test_missing_evidence_is_marked_as_neutral_fallback() -> None:
    contract = _contract_for((), formality=0.5)
    assert contract.style.source == "neutral_fallback"
    assert contract.style.confidence < 0.2


def test_ai_output_is_not_positive_style_evidence() -> None:
    evidence = StyleEvidence(
        evidence_id="ai",
        source_message_id="draft",
        source_type="model_accepted_unedited",
        timestamp="2026-01-01T00:00:00Z",
        contact_id=None,
        relationship_id=None,
        origin="model",
        confidence=1.0,
        bubbles=("привет, да, делаю",),
    )
    profile = StyleFeatureExtractor().profile((evidence,))
    assert profile.sample_count == 0
    with pytest.raises(ValueError):
        evidence_from_human_message(
            evidence_id="bad",
            message_id="draft",
            source_type="model_accepted_unedited",
            bubbles=("текст",),
        )


def test_safety_overrides_toxic_mirroring() -> None:
    extractor = StyleFeatureExtractor()
    snapshot = extractor.conversation_snapshot(
        conversation_id="toxic",
        messages=("ты тупой идиот",),
        emotional_context="toxic",
    )
    semantic = migrate_v1_to_semantic(_v1())
    style = AdaptiveStyleResolver().resolve(
        semantic=semantic,
        agent_profile=AgentStyleProfile("agent", empty_style_statistics()),
        relationship_profile=RelationshipStyleProfile(
            "agent", "unknown", None, empty_style_statistics()
        ),
        conversation=snapshot,
        relationship_context={"formality": 0.2},
    )
    assert style.mirroring_strength <= 0.2
    assert style.emoji_probability == 0
    assert style.slang_level == 0


def test_hard_failure_is_separate_from_soft_deviation() -> None:
    contract = _contract_for(("привет",), formality=0.2)
    style_result = SoftStyleEvaluator().evaluate(
        contract.style,
        _output("Здравствуйте."),
    )
    assert "casing_fit" in style_result.deviations
    assert style_result.to_dict()["provider_failure"] is False
    unsafe = _output("Гарантирую, что отправлю сегодня")
    assert HardSemanticValidator().validate(contract, unsafe).valid is False
    assert SafetyValidator().validate(contract, unsafe).valid is False
    copied = HardSemanticValidator().validate(
        contract,
        _output("Хочу поговорить с руководителем"),
        incoming_messages=("Хочу поговорить с руководителем",),
    )
    assert "no_incoming_copy" in copied.errors
    required = _contract_for(("Что обсуждали?",), formality=0.5)
    required = type(required)(
        semantic=type(required.semantic)(
            **{
                **required.semantic.__dict__,
                "required_information": ("Ранее уже обсуждали Telegram-бота.",),
            }
        ),
        style=required.style,
        safety=required.safety,
    )
    assert HardSemanticValidator().validate(
        required,
        _output("Да, уже обсуждали бота."),
    ).checks["required_meaning"]
    forbidden = _contract_for(("Кто дизайнер?",), formality=0.6)
    forbidden = type(forbidden)(
        semantic=type(forbidden.semantic)(
            **{
                **forbidden.semantic.__dict__,
                "forbidden_claims": ("имя дизайнера",),
            }
        ),
        style=forbidden.style,
        safety=SafetyConstraints(restrictions=("имя дизайнера",)),
    )
    refusal = _output("Не могу сообщить имя дизайнера.")
    assert HardSemanticValidator().validate(forbidden, refusal).valid
    assert SafetyValidator().validate(forbidden, refusal).valid
    promise_refusal = _output("Не обещаю, что всё будет готово за неделю.")
    assert HardSemanticValidator().validate(contract, promise_refusal).checks[
        "allowed_commitments"
    ]
    assert SafetyValidator().validate(contract, promise_refusal).checks[
        "unapproved_promises"
    ]


def _example(
    *,
    source_type: str,
    target: tuple[str, ...],
    provenance: dict[str, Any] | None = None,
) -> TrainingExample:
    return TrainingExample.from_dict(
        {
            "example_id": f"example-{source_type}",
            "agent_id": "agent",
            "conversation_context": [{"role": "contact", "content": "привет"}],
            "relationship_context": {"type": "colleague"},
            "semantic_plan": {"action": "reply"},
            "adaptive_style_plan": {"source": "adaptive"},
            "human_target_bubbles": list(target),
            "style_evidence": [{"evidence_id": "human-1"}] if target else [],
            "provenance": provenance or {"origin": "human", "message_id": "m1"},
            "timestamp": "2026-01-01T00:00:00Z",
            "privacy_status": "approved",
            "approval_status": "approved",
            "source_type": source_type,
            "quality_flags": [],
            "pii_flags": ["phone_reviewed"],
        }
    )


def test_feedback_events_map_to_safe_training_roles() -> None:
    repository = InMemoryStyleFeedbackRepository()
    edit = StyleFeedbackEvent(
        "1",
        "human_edit",
        "agent",
        ("готово",),
        ai_draft=("Сделано.",),
    )
    fix = StyleFeedbackEvent("2", "human_fix", "agent", ("нет, завтра",))
    accepted = StyleFeedbackEvent(
        "3",
        "ai_accepted_unchanged",
        "agent",
        ("AI draft",),
    )
    assert repository.training_candidate(edit)["positive_human_target"] is True
    assert repository.training_candidate(fix)["priority"] == 2
    assert repository.training_candidate(accepted)["positive_human_target"] is False
    assert repository.training_candidate(accepted)["style_evidence"] is False


def test_dataset_blocks_ai_target_benchmark_and_credentials(
    tmp_path: Path,
) -> None:
    accepted = _example(
        source_type="model_accepted_unedited",
        target=("AI draft",),
    )
    assert "accepted_ai_cannot_be_human_target" in validate_example(accepted)
    benchmark = _example(
        source_type="human_manual",
        target=("готово",),
        provenance={
            "origin": "human",
            "purpose": "benchmark_only",
            "benchmark_fingerprint": BENCHMARK_FINGERPRINT,
        },
    )
    assert "benchmark_training_forbidden" in validate_example(benchmark)
    root = tmp_path / "dataset"
    init_style_dataset(root)
    bad = _example(source_type="human_manual", target=("мой token=super-secret",))
    source = tmp_path / "bad.json"
    source.write_text(
        json.dumps(bad.to_dict(), ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(StyleDatasetError, match="credential_leak"):
        add_style_examples(
            root=root,
            input_path=source,
            source_type="human_manual",
        )
    assert not list((root / "raw").glob("*.json"))


def test_dataset_duplicate_provenance_pii_and_build(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    init_style_dataset(root)
    first = _example(source_type="human_edit", target=("готово",))
    second_value = first.to_dict()
    second_value["example_id"] = "duplicate"
    source = tmp_path / "examples.json"
    source.write_text(
        json.dumps([first.to_dict(), second_value], ensure_ascii=False),
        encoding="utf-8",
    )
    add_style_examples(root=root, input_path=source, source_type="human_edit")
    validation = validate_style_dataset(root)
    assert validation.valid is False
    assert len(validation.duplicates) == 1
    assert validation.pii_flag_counts == {"phone_reviewed": 2}
    (root / "raw" / "duplicate.json").unlink()
    output = root / "curated" / "train.jsonl"
    built = build_style_dataset(root=root, output=output)
    assert built["examples"] == 1
    assert built["benchmark_data_allowed"] is False


def test_production_default_and_frozen_benchmark_are_unchanged() -> None:
    generation_mode = next(
        item for item in fields(Settings) if item.name == "generation_mode"
    )
    assert generation_mode.default == "openai_only"
    assert load_frozen_benchmark(DATASET).fingerprint == BENCHMARK_FINGERPRINT


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_stage3a_imports_saved_contract_without_policy_call_and_resumes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Path.cwd()
    dataset = (repo / DATASET).resolve()
    benchmark = load_frozen_benchmark(dataset)
    business = next(item for item in benchmark.scenarios if item.id == "business-004")
    source = tmp_path / "stage26"
    output = tmp_path / "stage3a"
    _write_json(
        source / "run.json",
        {
            "benchmark_fingerprint": benchmark.fingerprint,
            "contract_snapshot_fingerprint": EXPECTED_STAGE26_SNAPSHOT,
        },
    )
    _write_json(
        source / "contracts/business-004__r1.json",
        {"scenario_id": business.id, "contract": _v1().to_dict()},
    )
    profile = get_renderer_profile("ruadapt_qwen3_4b_q6")
    _write_json(
        tmp_path / ".runtime/local_slm/ruadapt-model.json",
        {
            "repository": profile.repository,
            "resolved_revision": profile.revision,
            "filename": profile.filename,
            "quantization": profile.quantization,
            "sha256": "fixture",
            "size_bytes": 1,
        },
    )
    _write_json(
        tmp_path / ".runtime/local_slm/ruadapt-gpu-status.json",
        {"ready": True, "cpu_fallback": False},
    )

    class Renderer:
        renderer_name = "fixture"
        model = "fixture"

        def __init__(self) -> None:
            self.calls = 0

        async def render(self, **_: Any) -> Stage3ARendered:
            self.calls += 1
            return Stage3ARendered(
                _output("привет, да, делаю"),
                Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            )

    renderer = Renderer()
    monkeypatch.setattr(
        "conversation_agent.local_slm.stage3a._source_commit",
        lambda: "fixture-commit",
    )
    monkeypatch.chdir(tmp_path)
    options = Stage3AOptions(
        dataset_path=dataset,
        contracts_from=source,
        renderer="ruadapt_qwen3_4b_q6",
        output_dir=output,
        scenario_limit=1,
    )
    result = asyncio.run(
        run_stage3a(
            options,
            renderer_override=renderer,
            machine_override={"fixture": True},
        )
    )
    assert renderer.calls == 1
    assert result["result_count"] == 1
    record = json.loads(
        next((output / "results").rglob("*.json")).read_text(encoding="utf-8")
    )
    assert record["scenario_id"] == "business-004"
    assert record["adaptive_style_plan"]["casing"]["mode"] == "lowercase"
    assert record["soft_style_evaluation"]["provider_failure"] is False
    assert json.loads((output / "run.json").read_text())["gpt_policy_calls"] == 0
    resumed = Stage3AOptions(**{**options.__dict__, "resume": True})
    asyncio.run(
        run_stage3a(
            resumed,
            renderer_override=renderer,
            machine_override={"fixture": True},
        )
    )
    assert renderer.calls == 1
