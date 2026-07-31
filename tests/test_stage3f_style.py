from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from conversation_agent.local_slm.authoritative_pilot import (
    resolve_profile_preview,
)
from conversation_agent.local_slm.batch_curation import (
    build_curated_style_profiles,
)
from conversation_agent.local_slm.stage2_dataset import stable_fingerprint
from conversation_agent.local_slm.stage3f_style import (
    profile_audit,
    replay_evaluate,
)
from conversation_agent.local_slm.telegram_style_profile import (
    ConfidenceCalibrationConfig,
    StyleExtractionConfig,
    build_style_profiles,
    classify_casing,
    dataset_rows_to_profile_episodes,
    migrate_style_profile,
    resolve_style_plan,
)
from conversation_agent.settings import Settings


def _episode(
    index: int,
    *,
    target: list[str] | None = None,
    incoming: list[str] | None = None,
    target_timestamps: list[str] | None = None,
    incoming_timestamps: list[str] | None = None,
    relationship_id: str = "relationship-a",
) -> dict[str, Any]:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=index)
    return {
        "example_id": f"example-{index}",
        "relationship_id": relationship_id,
        "contact_alias": relationship_id,
        "human_target": {
            "messages": target or ["Normal answer."],
            "timestamps": target_timestamps or [],
        },
        "incoming": {
            "messages": incoming or ["Question?"],
            "timestamps": incoming_timestamps or [],
            "media_metadata": [],
        },
        "context_turns": [],
        "episode_timestamp": timestamp.isoformat(),
        "provenance": {
            "classification": "human_confirmed",
            "verified": True,
        },
    }


def _profiles(
    episodes: list[dict[str, Any]],
    *,
    calibration: ConfidenceCalibrationConfig | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return build_style_profiles(
        episodes,
        agent_id="agent",
        relationship_id="relationship-a",
        generated_at="2026-01-01T00:00:00+00:00",
        calibration_config=calibration,
    )


def _dataset_row(index: int, *, target: str = "Normal answer.") -> dict[str, Any]:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=index)
    return {
        "adaptive_style_plan": {},
        "agent_id": "agent",
        "approval_status": "approved",
        "conversation_context": [
            {"role": "human", "content": "Earlier answer."},
            {"role": "contact", "content": "Incoming question?"},
        ],
        "example_id": f"example-{index}",
        "human_target_bubbles": [target],
        "pii_flags": [],
        "pii_transformations": [],
        "previous_candidate": [],
        "privacy_status": "approved",
        "provenance": {
            "classification": "human_confirmed",
            "verified": True,
        },
        "quality_flags": [],
        "relationship_context": {
            "contact_alias": "contact-private",
            "relationship_type": "friend",
        },
        "semantic_enrichment_status": "pending",
        "semantic_plan": None,
        "source_type": "imported_human_verified",
        "style_evidence": [],
        "timestamp": timestamp.isoformat(),
    }


def _dataset(tmp_path: Path, count: int = 10) -> Path:
    root = tmp_path / "dataset"
    root.mkdir()
    rows = [
        _dataset_row(
            index,
            target=(
                "NORMAL ANSWER"
                if index % 4 == 0
                else "normal answer" if index % 3 == 0 else "Normal answer."
            ),
        )
        for index in range(count)
    ]
    (root / "examples.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in rows),
        encoding="utf-8",
    )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "dataset_fingerprint": stable_fingerprint(rows),
                "examples": len(rows),
                "source_type": "imported_human_verified",
            }
        ),
        encoding="utf-8",
    )
    return root


def test_all_caps_is_distinct_from_normal_sentence_casing() -> None:
    assert classify_casing("HELLO WORLD") == "all_caps"
    assert classify_casing("Hello world") == "normal_sentence_case"
    assert classify_casing("Hello World") == "mixed_case"


def test_lowercase_is_a_separate_casing_category() -> None:
    assert classify_casing("hello world") == "lowercase"


def test_uncased_punctuation_and_numbers_are_not_uppercase() -> None:
    assert classify_casing("?! 123") == "uncased"


def test_schema_v1_casing_migrates_without_claiming_all_caps() -> None:
    migrated = migrate_style_profile(
        {
            "schema_version": 1,
            "profile_type": "relationship_style_preview",
            "confidence": 0.9,
            "bubble_sample_count": 10,
            "features": {
                "casing": {
                    "lowercase_frequency": 0.2,
                    "uppercase_frequency": 0.7,
                    "mixed_or_uncased_frequency": 0.1,
                },
                "typo_frequency": 0.0,
            },
        }
    )
    distribution = migrated["features"]["casing"]["distribution"]
    assert distribution["normal_sentence_case"] == 0.7
    assert distribution["all_caps"] == 0.0
    assert migrated["migration"]["performed"] is True
    assert migrated["features"]["typo_frequency"] is None


def test_incoming_question_is_extracted_from_contact_context() -> None:
    _, relationship = _profiles(
        [_episode(1, incoming=["Are you available?"], target=["No question"])]
    )
    context = relationship["features"]["context"]
    assert context["incoming_has_question"]["frequency"] == 1.0
    assert context["owner_asks_question"]["frequency"] == 0.0


def test_long_incoming_is_not_classified_as_short() -> None:
    _, relationship = build_style_profiles(
        [_episode(1, incoming=["x" * 50])],
        agent_id="agent",
        relationship_id="relationship-a",
        generated_at="now",
        extraction_config=StyleExtractionConfig(
            short_incoming_characters=10,
            long_incoming_characters=40,
        ),
    )
    context = relationship["features"]["context"]
    assert context["incoming_is_short"]["count"] == 0
    assert context["incoming_is_long"]["count"] == 1
    assert context["thresholds"]["short_incoming_characters"] == 10


def test_target_text_does_not_control_incoming_classification() -> None:
    _, relationship = _profiles(
        [_episode(1, incoming=["brief"], target=["?" * 100])]
    )
    context = relationship["features"]["context"]
    assert context["incoming_has_question"]["count"] == 0
    assert context["incoming_is_short"]["count"] == 1


def test_unsupported_typo_feature_is_null_not_zero() -> None:
    _, relationship = _profiles(
        [_episode(1, target=["typooo informal spelling"])]
    )
    assert relationship["features"]["typo_frequency"] is None
    assert relationship["features"]["typo"]["supported"] is False
    assert relationship["feature_confidences"]["typo"] == 0.0


def test_missing_timestamp_does_not_become_zero_delay() -> None:
    _, relationship = _profiles([_episode(1, target=["one", "two"])])
    timing = relationship["features"]["inter_bubble_delay_seconds"]
    assert timing["availability"] == "unavailable"
    assert timing["count"] == 0
    assert timing["zero_count"] == 0
    assert timing["missing_count"] == 1


def test_real_zero_delay_is_distinct_from_missing_delay() -> None:
    timestamp = "2026-01-01T00:00:00+00:00"
    _, relationship = _profiles(
        [
            _episode(
                1,
                target=["one", "two"],
                target_timestamps=[timestamp, timestamp],
                incoming_timestamps=["2025-12-31T23:59:00+00:00"],
            )
        ]
    )
    timing = relationship["features"]["inter_bubble_delay_seconds"]
    assert timing["availability"] == "available"
    assert timing["count"] == 1
    assert timing["zero_count"] == 1
    assert timing["missing_count"] == 0


def test_invalid_timestamp_order_is_reported_not_clamped() -> None:
    _, relationship = _profiles(
        [
            _episode(
                1,
                target_timestamps=["2026-01-01T00:00:00+00:00"],
                incoming_timestamps=["2026-01-01T00:01:00+00:00"],
            )
        ]
    )
    timing = relationship["features"]["response_delay_seconds"]
    assert timing["count"] == 0
    assert timing["zero_count"] == 0
    assert timing["invalid_order_count"] == 1


def test_profanity_detector_reports_limited_coverage() -> None:
    _, relationship = _profiles([_episode(1, target=["\u0441\u0443\u043a\u0430"])])
    slang = relationship["features"]["slang_profanity"]
    assert slang["detector_coverage"] == "limited_lexicon"
    assert slang["known_profanity_lexicon_version"]
    assert slang["matched_token_count"] == 1
    assert slang["unknown_informal_lexicon_rate"] is None


def test_common_reply_is_evidence_not_a_fixed_rule() -> None:
    _, relationship = _profiles([_episode(1, target=["ok"])])
    common = relationship["features"]["common_short_replies"]
    assert common["usage"] == "observed_retrieval_candidates_only"
    assert common["exact_lexical_reuse_allowed"] is False
    assert relationship["fixed_rules"] == []


def test_single_relationship_limits_global_not_relationship_confidence() -> None:
    agent, relationship = _profiles([_episode(index) for index in range(60)])
    assert agent["single_relationship_bias"] is True
    assert agent["global_agent_confidence"] < relationship[
        "relationship_confidence"
    ]


def test_single_relationship_multiplier_is_configurable() -> None:
    episodes = [_episode(index) for index in range(60)]
    low_agent, _ = _profiles(
        episodes,
        calibration=ConfidenceCalibrationConfig(
            single_relationship_global_multiplier=0.2
        ),
    )
    high_agent, _ = _profiles(
        episodes,
        calibration=ConfidenceCalibrationConfig(
            single_relationship_global_multiplier=0.8
        ),
    )
    assert low_agent["global_agent_confidence"] < high_agent[
        "global_agent_confidence"
    ]


def test_relationship_confidence_remains_separate() -> None:
    agent, relationship = _profiles([_episode(index) for index in range(20)])
    assert agent["confidence"] == agent["global_agent_confidence"]
    assert relationship["confidence"] == relationship[
        "relationship_confidence"
    ]


def test_profanity_remains_relationship_specific() -> None:
    agent, relationship = _profiles([_episode(1, target=["\u0441\u0443\u043a\u0430"])])
    assert agent["feature_scopes"]["slang_profanity"] == "relationship_specific"
    assert agent["feature_confidences"]["slang_profanity"] == 0.0
    assert "matched_token_count" not in agent["features"]["slang_profanity"]
    assert "values" not in agent["features"]["common_short_replies"]
    assert relationship["feature_confidences"]["slang_profanity"] > 0.0


def test_confirmed_row_conversion_uses_contact_turn_not_target() -> None:
    episodes = dataset_rows_to_profile_episodes([_dataset_row(1)])
    assert episodes[0]["incoming"]["messages"] == ["Incoming question?"]
    assert episodes[0]["human_target"]["timestamps"] == []
    assert episodes[0]["episode_timestamp"]


def test_replay_is_episode_level_and_does_not_supply_target(tmp_path: Path) -> None:
    result = replay_evaluate(
        dataset=_dataset(tmp_path),
        output=tmp_path / "replay",
        folds=5,
        seed=42,
    )
    assert result["held_out_target_supplied_to_resolver"] is False
    assert result["episode_level_split"] is True
    folds = json.loads(
        (tmp_path / "replay" / "fold-metrics.json").read_text("utf-8")
    )["folds"]
    assert all(item["episode_overlap"] == 0 for item in folds)
    assert all(
        item["bubbles_split_between_train_and_evaluation"] is False
        for item in folds
    )


def test_replay_is_deterministic_for_seed(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path)
    replay_evaluate(
        dataset=dataset,
        output=tmp_path / "first",
        folds=5,
        seed=7,
    )
    replay_evaluate(
        dataset=dataset,
        output=tmp_path / "second",
        folds=5,
        seed=7,
    )
    assert (tmp_path / "first" / "summary.json").read_bytes() == (
        tmp_path / "second" / "summary.json"
    ).read_bytes()
    assert (tmp_path / "first" / "fold-metrics.json").read_bytes() == (
        tmp_path / "second" / "fold-metrics.json"
    ).read_bytes()


def test_temporal_split_uses_explicit_timestamps(tmp_path: Path) -> None:
    result = replay_evaluate(
        dataset=_dataset(tmp_path),
        output=tmp_path / "replay",
        folds=5,
        seed=42,
    )
    temporal = result["temporal_holdout"]
    assert temporal["availability"] == "available"
    assert temporal["jsonl_order_used_as_time"] is False
    assert temporal["train_episodes"] == 8
    assert temporal["evaluation_episodes"] == 2


def test_v1_profile_remains_resolvable() -> None:
    legacy = {
        "schema_version": 1,
        "profile_type": "relationship_style_preview",
        "confidence": 0.8,
        "sample_count": 10,
        "features": {
            "casing": {
                "lowercase_frequency": 0.1,
                "uppercase_frequency": 0.8,
                "mixed_or_uncased_frequency": 0.1,
            },
            "response_length": {"median": 12},
            "bubble_count": {"median": 1},
            "punctuation": {},
            "emoji": {},
            "slang_profanity": {},
            "sentence_completeness": 0.5,
        },
    }
    plan = resolve_style_plan(
        agent_profile=None,
        relationship_profile=legacy,
        mode="relationship_profile_only",
    )
    assert plan["casing"] == "normal_sentence_case"
    assert plan["fixed_rules"] == []


def test_profile_audit_writes_aggregate_only_reports(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path)
    profiles = tmp_path / "profiles"
    build_curated_style_profiles(dataset=dataset, output=profiles)
    result = profile_audit(
        agent_profile=profiles / "agent-style-profile.json",
        relationship_profile=profiles / "relationship-style-profile.json",
        dataset=dataset,
        output=tmp_path / "audit",
    )
    assert "PROFILE_AUDIT_PASSED" in result["statuses"]
    assert "INSUFFICIENT_RELATIONSHIP_DIVERSITY" in result["statuses"]
    assert "TIMING_DATA_UNAVAILABLE" in result["statuses"]
    summary = (tmp_path / "audit" / "summary.md").read_text("utf-8")
    assert "Normal answer" not in summary
    assert result["privacy_findings"] == 0


def test_resolve_preview_omits_private_lexical_values(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path)
    profiles = tmp_path / "profiles"
    build_curated_style_profiles(dataset=dataset, output=profiles)
    preview = resolve_profile_preview(
        agent_profile=profiles / "agent-style-profile.json",
        relationship_profile=profiles / "relationship-style-profile.json",
        limit=5,
    )
    features = preview["resolved_distributions"]
    assert "values" not in features["common_short_replies"]
    assert "values" not in features["frequent_lexicon"]
    assert features["common_short_replies"]["private_values_omitted"] is True


def test_replay_modes_never_enable_exact_lexical_reuse(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path)
    result = replay_evaluate(
        dataset=dataset,
        output=tmp_path / "replay",
        folds=5,
        seed=42,
    )
    assert set(result["metrics"]) == {
        "neutral_fallback",
        "agent_profile_only",
        "relationship_profile_only",
        "agent_relationship",
        "agent_relationship_conversation",
    }
    assert result["llm_called"] is False
    assert result["training_performed"] is False


def test_replay_does_not_call_openai_or_local_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from conversation_agent.llm.openai_client import OpenAIReplyClient
    from conversation_agent.local_slm.provider import (
        OpenAICompatibleLocalProvider,
    )

    async def unexpected_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("model provider must not be called by offline replay")

    monkeypatch.setattr(OpenAIReplyClient, "create_reply", unexpected_call)
    monkeypatch.setattr(
        OpenAICompatibleLocalProvider,
        "generate",
        unexpected_call,
    )
    result = replay_evaluate(
        dataset=_dataset(tmp_path),
        output=tmp_path / "replay",
        folds=5,
        seed=42,
    )
    assert result["llm_called"] is False


def test_stage3f_keeps_production_default_openai_only() -> None:
    assert Settings.__dataclass_fields__["generation_mode"].default == "openai_only"


@pytest.mark.parametrize("mode", ["agent_profile_only", "relationship_profile_only"])
def test_resolved_plans_keep_fixed_rules_empty(mode: str) -> None:
    agent, relationship = _profiles([_episode(index) for index in range(10)])
    plan = resolve_style_plan(
        agent_profile=agent,
        relationship_profile=relationship,
        mode=mode,
    )
    assert plan["fixed_rules"] == []
    assert plan["exact_lexical_reuse"] is False
