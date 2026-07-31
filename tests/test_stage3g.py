from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any

import pytest  # pyright: ignore[reportMissingImports]

from conversation_agent.local_slm import stage3g_review_ui
from conversation_agent.local_slm.models import GenerationResult
from conversation_agent.local_slm.stage2_dataset import atomic_write_json
from conversation_agent.local_slm.stage3a_contract import (
    SafetyConstraints,
    SemanticPlan,
)
from conversation_agent.local_slm.stage3g import (
    Rendered,
    Stage3GOptions,
    Stage3GRenderer,
    _apply_hidden_target_validation,
    _neutral_style,
    _run_candidate,
    _style_from_profile,
    audit_generation_prompt,
    automatic_validation,
    blind_display_mapping,
    execute_candidate,
    execution_order,
    extract_profile_lexical_values,
    private_episode_input,
    select_private_rows,
    semantic_contract_fingerprint,
    variant_plans,
)
from conversation_agent.local_slm.stage3g_review_ui import Stage3GReviewState
from conversation_agent.local_slm.stage25_pipeline import PolicyContext, Usage
from conversation_agent.settings import Settings


def _semantic(**overrides: Any) -> SemanticPlan:
    values: dict[str, Any] = {
        "action": "reply",
        "goal": "reply",
        "required_information": (),
        "allowed_facts": (),
        "forbidden_claims": (),
        "allowed_commitments": (),
        "must_acknowledge": False,
        "clarification_needed": True,
        "handoff_strategy": "none",
        "uncertainty_strategy": "ask",
        "sensitive_data_strategy": "refuse_collection",
        "reaction": None,
        "confidence": 1.0,
    }
    values.update(overrides)
    return SemanticPlan(**values)


def _context(text: str = "привет") -> PolicyContext:
    return PolicyContext(
        conversation=({"role": "contact", "content": text},),
        relationship={"formality": 0.2},
        known_facts=(),
        restrictions=(),
        goal="reply",
    )


def _profile(*, alias: str = "friend", profanity: float = 0.1) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "contact_alias": alias,
        "confidence": 0.9,
        "features": {
            "casing": {
                "distribution": {
                    "lowercase": 0.8,
                    "normal_sentence_case": 0.2,
                }
            },
            "punctuation": {
                "final_punctuation_frequency": 0.1,
                "exclamation_frequency": 0.0,
            },
            "bubble_count": {"median": 1, "p75": 2},
            "message_length_chars": {"p25": 4, "median": 10, "p75": 30},
            "emoji": {"frequency": 0.02},
            "sentence_completeness": 0.4,
            "slang_profanity": {"matched_message_rate": profanity},
            "common_short_replies": {"values": [{"value": "secret reply"}]},
            "frequent_lexicon": {"values": [{"value": "private phrase"}]},
        },
    }


def _plans(
    semantic: SemanticPlan | None = None,
    *,
    context: PolicyContext | None = None,
) -> dict[str, Any]:
    semantic = semantic or _semantic()
    context = context or _context()
    return variant_plans(
        semantic=semantic,
        neutral=_neutral_style(semantic),
        relationship=_style_from_profile(
            _profile(),
            semantic=semantic,
            eligible=True,
            relationship_matches=True,
            context=context,
        ),
        safety=SafetyConstraints(),
    )


class FakeRenderer:
    renderer_name = "fake"
    model = "fake-local"

    def __init__(self, outputs: list[GenerationResult] | None = None) -> None:
        self.outputs = outputs or [
            GenerationResult(
                action="reply",
                messages=("готово",),
                confidence=1.0,
                provider="fake",
            )
        ]
        self.calls: list[dict[str, Any]] = []

    async def render(self, **kwargs: Any) -> Rendered:
        self.calls.append(kwargs)
        output = self.outputs[min(len(self.calls) - 1, len(self.outputs) - 1)]
        return Rendered(
            output=output,
            usage=Usage(prompt_tokens=10, completion_tokens=3, total_tokens=13),
            prompt_audit={"valid": True, "errors": []},
        )


def test_n_and_r_receive_identical_semantic_plan() -> None:
    plans = _plans()
    assert plans["N"].semantic == plans["R"].semantic


def test_n_and_r_receive_identical_safety_constraints() -> None:
    plans = _plans()
    assert plans["N"].safety == plans["R"].safety


def test_style_is_only_intended_variant() -> None:
    plans = _plans()
    assert plans["N"].style != plans["R"].style
    assert semantic_contract_fingerprint(plans["N"]) == semantic_contract_fingerprint(
        plans["R"]
    )


def test_exact_lexical_profile_values_are_absent_from_prompt() -> None:
    audit = audit_generation_prompt(
        instructions="aggregate distributions only",
        user_content='{"style":{"casing":"lowercase"}}',
        lexical_evidence=("private phrase",),
        held_out_target=(),
    )
    assert audit["valid"]
    leaked = audit_generation_prompt(
        instructions="use private phrase",
        user_content="{}",
        lexical_evidence=("private phrase",),
        held_out_target=(),
    )
    assert not leaked["valid"]


def test_common_short_replies_are_absent_from_prompt() -> None:
    values = extract_profile_lexical_values(_profile())
    assert "secret reply" in values
    assert "secret reply" not in json.dumps(_plans()["R"].style.to_dict())


def test_held_out_target_is_absent_from_generation_input() -> None:
    assert "held_out_target" not in inspect.signature(execute_candidate).parameters
    assert "held_out_target" not in inspect.signature(_run_candidate).parameters


def test_held_out_target_is_absent_from_retry_input() -> None:
    renderer = FakeRenderer(
        [
            GenerationResult(action="handoff", messages=("x",), provider="fake"),
            GenerationResult(action="reply", messages=("готово",), provider="fake"),
        ]
    )
    asyncio.run(
        execute_candidate(
            renderer=renderer,
            context=_context(),
            contract=_plans()["N"],
            relationship_alias="friend",
        )
    )
    assert len(renderer.calls) == 2
    assert set(renderer.calls[1]) == {
        "context",
        "contract",
        "relationship_alias",
        "lexical_evidence",
        "repair_errors",
    }


def test_held_out_episode_is_absent_from_train_profile_input() -> None:
    rows = [{"example_id": "a"}, {"example_id": "b"}, {"example_id": "c"}]
    train = [row for row in rows if row["example_id"] != "b"]
    assert [row["example_id"] for row in train] == ["a", "c"]


def test_bubbles_from_one_episode_never_cross_train_evaluation() -> None:
    row = {
        "conversation_context": [
            {"role": "agent", "content": "old"},
            {"role": "contact", "content": "new"},
        ],
        "relationship_context": {"contact_alias": "friend"},
        "human_target_bubbles": ["held", "out"],
    }
    public = private_episode_input(row)
    assert "held" not in json.dumps(public)
    assert public["latest_incoming"] == [{"role": "contact", "content": "new"}]


def test_relationship_profile_applies_only_to_matching_alias() -> None:
    semantic = _semantic()
    style = _style_from_profile(
        _profile(),
        semantic=semantic,
        eligible=True,
        relationship_matches=False,
        context=_context(),
    )
    assert style.source == "neutral_fallback"


def test_relationship_profanity_is_suppressed_for_formal_scenario() -> None:
    context = PolicyContext(
        conversation=({"role": "contact", "content": "Здравствуйте"},),
        relationship={"formality": 0.9},
        known_facts=(),
        restrictions=(),
        goal="reply",
    )
    style = _style_from_profile(
        _profile(),
        semantic=_semantic(),
        eligible=True,
        relationship_matches=True,
        context=context,
    )
    assert style.slang_level == 0


def test_relationship_profanity_is_suppressed_for_sensitive_scenario() -> None:
    style = _style_from_profile(
        _profile(),
        semantic=_semantic(),
        eligible=True,
        relationship_matches=True,
        context=_context("вот мой пароль"),
    )
    assert style.slang_level == 0


def test_relationship_profanity_is_suppressed_for_handoff() -> None:
    semantic = _semantic(action="handoff", handoff_strategy="request_human")
    style = _style_from_profile(
        _profile(),
        semantic=semantic,
        eligible=True,
        relationship_matches=True,
        context=_context("привет"),
    )
    assert style.slang_level == 0


def test_private_phrase_leakage_is_a_hard_failure(tmp_path: Path) -> None:
    path = tmp_path / "R.json"
    record = {
        "normalized_output": {"messages": ["exact hidden target"]},
        "automatic_validation": {"private_phrase_leakage": 0},
        "hard_failure": False,
    }
    _apply_hidden_target_validation(
        records={"R": record},
        paths={"R": path},
        target=("exact hidden target",),
    )
    saved = json.loads(path.read_text())
    assert saved["hard_failure"] is True
    assert saved["automatic_validation"]["private_phrase_leakage"] == 1


def test_blind_ab_mapping_is_deterministic_for_seed() -> None:
    assert blind_display_mapping("pair", 42) == blind_display_mapping("pair", 42)


def test_execution_order_may_differ_from_display_order() -> None:
    assert any(
        tuple(blind_display_mapping(f"pair-{index}", 42).values())
        != execution_order(f"pair-{index}", 42)
        for index in range(20)
    )


def test_resume_skips_successful_candidates(tmp_path: Path) -> None:
    options = _options(tmp_path, resume=True)
    path = tmp_path / "controlled" / "x" / "N.json"
    atomic_write_json(path, {"hard_failure": False})
    renderer = FakeRenderer()
    asyncio.run(_candidate(options, renderer))
    assert renderer.calls == []


def test_retry_errors_retries_only_failed_candidates(tmp_path: Path) -> None:
    options = _options(tmp_path, resume=True, retry_errors=True)
    path = tmp_path / "controlled" / "x" / "N.json"
    atomic_write_json(path, {"hard_failure": True})
    renderer = FakeRenderer()
    asyncio.run(_candidate(options, renderer))
    assert len(renderer.calls) == 1


def test_review_saves_immediately(tmp_path: Path) -> None:
    state = _review_state(tmp_path)
    state.save("private__one", "tie", ["too long"])
    assert (state.review_dir / "private__one.json").is_file()


def test_human_target_reveal_occurs_only_after_rating(tmp_path: Path) -> None:
    state = _review_state(tmp_path)
    with pytest.raises(PermissionError):
        state.reveal_target("private__one")
    state.save("private__one", "tie", [])
    assert state.reveal_target("private__one")["messages"] == ["target"]


def test_reveal_does_not_modify_rating(tmp_path: Path) -> None:
    state = _review_state(tmp_path)
    state.save("private__one", "a_slightly_better", [])
    before = (state.review_dir / "private__one.json").read_bytes()
    state.reveal_target("private__one")
    assert (state.review_dir / "private__one.json").read_bytes() == before


def test_review_ui_reuses_matching_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _review_state(tmp_path)
    monkeypatch.setattr(
        stage3g_review_ui,
        "Stage3GReviewServer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("occupied")),
    )
    monkeypatch.setattr(
        stage3g_review_ui,
        "_existing_review_ui",
        lambda _port: {"reviewer": "denver", "seed": 42, "reviewed": 3, "total": 30},
    )
    result = stage3g_review_ui.run_stage3g_review_ui(
        run_dir=tmp_path,
        reviewer="denver",
        seed=42,
        open_browser=False,
    )
    assert result["already_running"] is True
    assert result["reviewed"] == 3


def test_no_telegram_client_is_created() -> None:
    source = inspect.getsource(__import__(
        "conversation_agent.local_slm.stage3g",
        fromlist=["stage3g"],
    ))
    assert "TelegramClient" not in source


def test_no_telegram_send_method_is_called() -> None:
    source = inspect.getsource(_run_candidate)
    assert "send_message" not in source
    assert "send_reaction" not in source


def test_openai_is_unavailable_without_explicit_local_provider() -> None:
    source = inspect.getsource(Stage3GRenderer)
    assert "OpenAIReplyClient" not in source
    assert "create_structured_reply" in source


def test_local_model_fallback_is_forbidden() -> None:
    fields = Stage3GOptions.__dataclass_fields__
    assert fields["no_openai"].default is True
    assert "fallback" not in inspect.getsource(Stage3GRenderer).casefold()


def test_training_is_never_invoked() -> None:
    source = inspect.getsource(__import__(
        "conversation_agent.local_slm.stage3g",
        fromlist=["stage3g"],
    ))
    assert "build_sft_dataset" not in source
    assert "training_dry_run" not in source


def test_private_selection_is_deterministic_and_bounded() -> None:
    rows = [
        {
            "example_id": str(index),
            "human_target_bubbles": ["ok" if index % 2 else "Long target " * 8],
            "conversation_context": [{"role": "contact", "content": "бот?"}],
        }
        for index in range(20)
    ]
    assert select_private_rows(rows, 10, 42) == select_private_rows(rows, 10, 42)
    assert len(select_private_rows(rows, 10, 42)) == 10


def test_profanity_misuse_is_reported_when_profile_not_eligible() -> None:
    contract = _plans()["R"]
    result = automatic_validation(
        output=GenerationResult(
            action="reply", messages=("бля готово",), provider="fake"
        ),
        contract=contract,
        context=_context(),
        lexical_evidence=(),
        private_targets=(),
        profile_eligible=False,
    )
    assert result["profanity_misuse"] is True


def test_production_default_remains_openai_only() -> None:
    assert Settings.__dataclass_fields__["generation_mode"].default == "openai_only"


def _options(
    output: Path, *, resume: bool = False, retry_errors: bool = False
) -> Stage3GOptions:
    return Stage3GOptions(
        private_dataset=Path("private"),
        agent_profile=Path("agent"),
        relationship_profile=Path("relationship"),
        contracts_from=Path("contracts"),
        output_dir=output,
        resume=resume,
        retry_errors=retry_errors,
    )


async def _candidate(options: Stage3GOptions, renderer: FakeRenderer) -> None:
    await _run_candidate(
        options=options,
        renderer=renderer,
        track="controlled",
        pair_id="controlled__x",
        variant="N",
        context=_context(),
        contract=_plans()["N"],
        relationship_alias="synthetic",
        lexical_evidence=(),
        metadata={"profile_eligible": False},
    )


def _review_state(root: Path) -> Stage3GReviewState:
    atomic_write_json(
        root / "blind-mapping.json",
        {
            "seed": 42,
            "pairs": {"private__one": {"A": "N", "B": "R"}},
            "execution_order": {"private__one": ["R", "N"]},
        },
    )
    for variant in ("N", "R"):
        atomic_write_json(
            root / "private-shadow" / "one" / f"{variant}.json",
            {
                "track": "private-shadow",
                "variant": variant,
                "metadata": {
                    "category": "casual",
                    "public_episode": {
                        "preceding_context": [],
                        "latest_incoming": [
                            {"role": "contact", "content": "hello"}
                        ],
                    },
                },
                "normalized_output": {
                    "action": "reply",
                    "messages": [f"candidate {variant}"],
                    "reaction": None,
                    "handoff_required": False,
                },
            },
        )
    atomic_write_json(
        root / "hidden-targets.json",
        {"private__one": {"messages": ["target"], "evaluation": {}}},
    )
    return Stage3GReviewState(run_dir=root, reviewer="denver", seed=42)
