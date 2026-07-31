"""Stage 3F profile audit and leakage-safe offline replay."""

from __future__ import annotations

import json
import random
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from conversation_agent.local_slm.stage2_dataset import stable_fingerprint
from conversation_agent.local_slm.telegram_privacy import scan_text
from conversation_agent.local_slm.telegram_style_profile import (
    CASING_CATEGORIES,
    PROFILE_SCHEMA_VERSION,
    build_style_profiles,
    dataset_rows_to_profile_episodes,
    extract_target_surface,
    migrate_style_profile,
    resolve_style_plan,
)

REPLAY_MODES = (
    "neutral_fallback",
    "agent_profile_only",
    "relationship_profile_only",
    "agent_relationship",
    "agent_relationship_conversation",
)


class Stage3FError(ValueError):
    """Raised when a private Stage 3F input violates its local contract."""


def profile_audit(
    *,
    agent_profile: Path,
    relationship_profile: Path,
    dataset: Path,
    output: Path,
) -> dict[str, Any]:
    rows, dataset_fp = _load_verified_rows(dataset)
    episodes = dataset_rows_to_profile_episodes(rows)
    raw_agent = _read_json(agent_profile)
    raw_relationship = _read_json(relationship_profile)
    agent = migrate_style_profile(raw_agent)
    relationship = migrate_style_profile(raw_relationship)
    expected_agent, expected_relationship = build_style_profiles(
        episodes,
        agent_id=str(agent.get("agent_id", "private-agent")),
        relationship_id=str(
            relationship.get("relationship_id", "private_contact")
        ),
        generated_at=str(agent.get("generated_at") or _now()),
    )
    output.mkdir(parents=True, exist_ok=True)
    features = relationship.get("features", {})
    casing = features.get("casing", {})
    context = features.get("context", {})
    response_timing = features.get("response_delay_seconds", {})
    bubble_timing = features.get("inter_bubble_delay_seconds", {})
    slang = features.get("slang_profanity", {})
    typo = features.get("typo", {})
    evidence = relationship.get("evidence_diversity", {})
    privacy_findings = _privacy_findings(rows)
    supported = sorted(
        key
        for key, confidence in relationship.get(
            "feature_confidences", {}
        ).items()
        if float(confidence) > 0
    )
    unsupported = sorted(
        key
        for key, confidence in relationship.get(
            "feature_confidences", {}
        ).items()
        if float(confidence) == 0
    )
    suspicious_zero_values = _suspicious_zero_values(features)
    migration_performed = bool(
        agent.get("migration", {}).get("performed")
        or relationship.get("migration", {}).get("performed")
    )
    casing_audit = {
        "categories": list(CASING_CATEGORIES),
        "counts": casing.get("counts", {}),
        "distribution": casing.get("distribution", {}),
        "sample_count": casing.get("sample_count", 0),
        "legacy_uppercase_field_present": "uppercase_frequency" in casing,
        "status": (
            "supported"
            if not "uppercase_frequency" in casing
            else "legacy_schema_migrated"
        ),
    }
    context_audit = {
        "source": "contact_incoming_and_prior_context_only",
        "sample_count": context.get("sample_count", 0),
        "thresholds": context.get("thresholds", {}),
        "incoming_character_count": context.get("incoming_character_count", {}),
        "incoming_bubble_count": context.get("incoming_bubble_count", {}),
        "incoming_has_question": context.get("incoming_has_question", {}),
        "incoming_is_short": context.get("incoming_is_short", {}),
        "incoming_is_long": context.get("incoming_is_long", {}),
        "owner_asks_question": context.get("owner_asks_question", {}),
        "topic_category": context.get("topic_category", {}),
    }
    timing_audit = {
        "response_delay_seconds": response_timing,
        "inter_bubble_delay_seconds": bubble_timing,
        "episode_timestamp_coverage": evidence.get("timestamp_coverage"),
        "missing_timestamp_not_zero": True,
    }
    lexical_audit = {
        "detector_coverage": slang.get("detector_coverage"),
        "known_profanity_lexicon_version": slang.get(
            "known_profanity_lexicon_version"
        ),
        "matched_token_count": slang.get("matched_token_count"),
        "matched_message_count": slang.get("matched_message_count"),
        "unknown_informal_lexicon_rate": slang.get(
            "unknown_informal_lexicon_rate"
        ),
        "profanity_scope": relationship.get("feature_scopes", {}).get(
            "slang_profanity"
        ),
        "common_short_replies_usage": features.get(
            "common_short_replies", {}
        ).get("usage"),
        "exact_lexical_reuse_allowed": features.get(
            "common_short_replies", {}
        ).get("exact_lexical_reuse_allowed"),
        "private_values_in_report": False,
    }
    confidence_audit = {
        "agent_confidence": agent.get("global_agent_confidence"),
        "relationship_confidence": relationship.get(
            "relationship_confidence"
        ),
        "feature_confidences": relationship.get("feature_confidences", {}),
        "evidence_diversity": evidence,
        "single_relationship_bias": relationship.get(
            "single_relationship_bias"
        ),
        "calibration_reason": relationship.get("calibration_reason", []),
        "typo_feature_status": (
            "unsupported" if typo.get("supported") is False else "supported"
        ),
    }
    profile_diff = {
        "agent": _aggregate_profile_diff(agent, expected_agent),
        "relationship": _aggregate_profile_diff(
            relationship, expected_relationship
        ),
        "migration_performed": migration_performed,
        "raw_schema_versions": {
            "agent": int(raw_agent.get("schema_version", 1)),
            "relationship": int(raw_relationship.get("schema_version", 1)),
        },
        "effective_schema_version": PROFILE_SCHEMA_VERSION,
        "private_values_in_report": False,
    }
    statuses: list[str] = []
    if privacy_findings:
        statuses.append("PRIVACY_BLOCKED")
    elif (
        int(agent.get("schema_version", 0)) == PROFILE_SCHEMA_VERSION
        and not casing_audit["legacy_uppercase_field_present"]
    ):
        statuses.append("PROFILE_AUDIT_PASSED")
    else:
        statuses.append("PROFILE_EXTRACTOR_NEEDS_FIX")
    if int(evidence.get("relationship_count", 0)) < 2:
        statuses.append("INSUFFICIENT_RELATIONSHIP_DIVERSITY")
    if response_timing.get("availability") == "unavailable":
        statuses.append("TIMING_DATA_UNAVAILABLE")
    coverage = {
        "supported_features": supported,
        "unsupported_features": unsupported,
        "suspicious_zero_values": suspicious_zero_values,
        "missing_timestamps": {
            "response_delay": response_timing.get("missing_count", 0),
            "inter_bubble_delay": bubble_timing.get("missing_count", 0),
        },
        "profile_scope": {
            "agent": agent.get("scope"),
            "relationship": relationship.get("scope"),
        },
        "relationship_count": evidence.get("relationship_count", 0),
        "contact_count": evidence.get("contact_count", 0),
        "global_bias_warnings": (
            ["single_relationship_bias"]
            if relationship.get("single_relationship_bias")
            else []
        ),
        "migration_performed": migration_performed,
        "recommended_next_step": (
            "collect verified examples from additional relationships before "
            "treating relationship-local features as global"
        ),
    }
    _write_json(output / "feature-coverage.json", coverage)
    _write_json(output / "casing-audit.json", casing_audit)
    _write_json(output / "context-audit.json", context_audit)
    _write_json(output / "timing-audit.json", timing_audit)
    _write_json(output / "lexical-audit.json", lexical_audit)
    _write_json(output / "confidence-audit.json", confidence_audit)
    _write_json(output / "profile-diff.json", profile_diff)
    summary = {
        "statuses": statuses,
        "dataset_fingerprint": dataset_fp,
        "source_examples": len(rows),
        "owner_message_bubbles": sum(
            len(item.get("human_target_bubbles", [])) for item in rows
        ),
        "privacy_findings": privacy_findings,
        "schema_version": PROFILE_SCHEMA_VERSION,
        **coverage,
        "agent_confidence": agent.get("global_agent_confidence"),
        "relationship_confidence": relationship.get(
            "relationship_confidence"
        ),
        "typo_feature_status": confidence_audit["typo_feature_status"],
        "timing_availability": response_timing.get("availability"),
    }
    _write_summary(output / "summary.md", summary)
    return {**summary, "output": str(output)}


def replay_evaluate(
    *,
    dataset: Path,
    output: Path,
    folds: int,
    seed: int,
) -> dict[str, Any]:
    if folds < 2:
        raise Stage3FError("--folds must be at least 2")
    rows, dataset_fp = _load_verified_rows(dataset)
    episodes = dataset_rows_to_profile_episodes(rows)
    if len(episodes) < folds:
        raise Stage3FError("fold count exceeds episode count")
    assignments = list(range(len(episodes)))
    random.Random(seed).shuffle(assignments)
    fold_indices = [assignments[index::folds] for index in range(folds)]
    mode_observations: dict[str, list[dict[str, float]]] = {
        mode: [] for mode in REPLAY_MODES
    }
    fold_reports: list[dict[str, Any]] = []
    all_indices = set(range(len(episodes)))
    for fold_number, held_out_indices in enumerate(fold_indices, start=1):
        held_out_set = set(held_out_indices)
        train = [
            item for index, item in enumerate(episodes) if index not in held_out_set
        ]
        held_out = [episodes[index] for index in held_out_indices]
        agent, relationship = build_style_profiles(
            train,
            agent_id="private-agent",
            relationship_id="private_contact",
            generated_at="offline-replay",
        )
        fold_mode_metrics: dict[str, Any] = {}
        for mode in REPLAY_MODES:
            observations = _evaluate_episodes(
                held_out,
                agent=agent,
                relationship=relationship,
                mode=mode,
            )
            mode_observations[mode].extend(observations)
            fold_mode_metrics[mode] = _aggregate_metrics(observations)
        fold_reports.append(
            {
                "fold": fold_number,
                "train_episodes": len(train),
                "evaluation_episodes": len(held_out),
                "episode_overlap": len(
                    (all_indices - held_out_set).intersection(held_out_set)
                ),
                "bubbles_split_between_train_and_evaluation": False,
                "held_out_target_supplied_to_resolver": False,
                "metrics": fold_mode_metrics,
            }
        )
    aggregate = {
        mode: _aggregate_metrics(observations)
        for mode, observations in mode_observations.items()
    }
    temporal = _temporal_holdout(episodes)
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "dataset_fingerprint": dataset_fp,
        "source_examples": len(rows),
        "folds": folds,
        "seed": seed,
        "modes": list(REPLAY_MODES),
        "llm_called": False,
        "training_performed": False,
        "held_out_target_supplied_to_resolver": False,
        "episode_level_split": True,
    }
    _write_json(output / "manifest.json", manifest)
    _write_json(output / "fold-metrics.json", {"folds": fold_reports})
    _write_json(output / "summary.json", {"metrics": aggregate, **manifest})
    _write_json(output / "temporal-holdout.json", temporal)
    return {
        **manifest,
        "metrics": aggregate,
        "temporal_holdout": temporal,
        "output": str(output),
    }


def _evaluate_episodes(
    episodes: list[dict[str, Any]],
    *,
    agent: dict[str, Any],
    relationship: dict[str, Any],
    mode: str,
) -> list[dict[str, float]]:
    observations: list[dict[str, float]] = []
    for episode in episodes:
        incoming = [
            str(item) for item in episode.get("incoming", {}).get("messages", [])
        ]
        snapshot = [
            str(item.get("content", ""))
            for item in episode.get("context_turns", [])
            if item.get("role") == "human" and str(item.get("content", "")).strip()
        ][-3:]
        # Resolve before reading the held-out target. This ordering is intentional
        # and covered by tests that pass a resolver spy.
        plan = resolve_style_plan(
            agent_profile=agent,
            relationship_profile=relationship,
            incoming_messages=incoming,
            conversation_owner_messages=(
                snapshot if mode == "agent_relationship_conversation" else ()
            ),
            mode=mode,
        )
        actual = extract_target_surface(
            [
                str(item)
                for item in episode.get("human_target", {}).get("messages", [])
            ]
        )
        confidences = [
            float(value)
            for value in plan.get("feature_confidences", {}).values()
        ]
        observations.append(
            {
                "casing_category_accuracy": float(
                    plan["casing"] == actual["casing"]
                ),
                "bubble_count_absolute_error": abs(
                    float(plan["bubble_count"]) - float(actual["bubble_count"])
                ),
                "bubble_count_exact_match": float(
                    plan["bubble_count"] == actual["bubble_count"]
                ),
                "length_quantile_accuracy": float(
                    plan["length_band"] == actual["length_band"]
                ),
                "length_absolute_error": abs(
                    float(plan["length"]) - float(actual["length"])
                ),
                "question_decision_accuracy": float(
                    plan["asks_question"] == actual["asks_question"]
                ),
                "final_punctuation_accuracy": float(
                    plan["final_punctuation"] == actual["final_punctuation"]
                ),
                "emoji_presence_accuracy": float(
                    plan["emoji_present"] == actual["emoji_present"]
                ),
                "profanity_slang_tendency_accuracy": float(
                    plan["profanity_or_slang_tendency"]
                    == actual["profanity_or_slang_tendency"]
                ),
                "short_response_classification": float(
                    plan["short_response"] == actual["short_response"]
                ),
                "sentence_completeness_band": float(
                    plan["sentence_complete"] == actual["sentence_complete"]
                ),
                "fallback": float(plan["fallback"]),
                "average_feature_confidence": (
                    statistics.fmean(confidences) if confidences else 0.0
                ),
            }
        )
    return observations


def _aggregate_metrics(
    observations: list[dict[str, float]],
) -> dict[str, Any]:
    keys = (
        observations[0].keys()
        if observations
        else (
            "casing_category_accuracy",
            "bubble_count_absolute_error",
            "bubble_count_exact_match",
            "length_quantile_accuracy",
            "length_absolute_error",
            "question_decision_accuracy",
            "final_punctuation_accuracy",
            "emoji_presence_accuracy",
            "profanity_slang_tendency_accuracy",
            "short_response_classification",
            "sentence_completeness_band",
            "fallback",
            "average_feature_confidence",
        )
    )
    return {
        "evaluation_episodes": len(observations),
        **{
            ("fallback_rate" if key == "fallback" else key): round(
                statistics.fmean(item[key] for item in observations), 6
            )
            if observations
            else None
            for key in keys
        },
    }


def _temporal_holdout(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    parsed: list[tuple[datetime, dict[str, Any]]] = []
    for episode in episodes:
        value = str(episode.get("episode_timestamp") or "")
        try:
            parsed.append((datetime.fromisoformat(value), episode))
        except ValueError:
            return {
                "status": "TEMPORAL_HOLDOUT_UNAVAILABLE",
                "reason": "one or more episode timestamps are unavailable",
                "jsonl_order_used_as_time": False,
            }
    if len(parsed) < 5:
        return {
            "status": "TEMPORAL_HOLDOUT_UNAVAILABLE",
            "reason": "fewer than five timestamped episodes",
            "jsonl_order_used_as_time": False,
        }
    parsed.sort(key=lambda item: item[0])
    boundary = max(1, int(len(parsed) * 0.8))
    train = [item[1] for item in parsed[:boundary]]
    evaluation = [item[1] for item in parsed[boundary:]]
    agent, relationship = build_style_profiles(
        train,
        agent_id="private-agent",
        relationship_id="private_contact",
        generated_at="offline-temporal-replay",
    )
    return {
        "availability": "available",
        "train_episodes": len(train),
        "evaluation_episodes": len(evaluation),
        "train_ratio": round(len(train) / len(parsed), 6),
        "jsonl_order_used_as_time": False,
        "metrics": {
            mode: _aggregate_metrics(
                _evaluate_episodes(
                    evaluation,
                    agent=agent,
                    relationship=relationship,
                    mode=mode,
                )
            )
            for mode in REPLAY_MODES
        },
    }


def _load_verified_rows(dataset: Path) -> tuple[list[dict[str, Any]], str]:
    manifest = _read_json(dataset / "manifest.json")
    rows = [
        json.loads(line)
        for line in (dataset / "examples.jsonl")
        .read_text(encoding="utf-8-sig")
        .splitlines()
        if line.strip()
    ]
    if not rows:
        raise Stage3FError("confirmed dataset is empty")
    if any(item.get("source_type") != "imported_human_verified" for item in rows):
        raise Stage3FError("dataset contains non-human examples")
    if any(
        item.get("provenance", {}).get("verified") is not True for item in rows
    ):
        raise Stage3FError("dataset contains unverified examples")
    fingerprint = stable_fingerprint(rows)
    if manifest.get("dataset_fingerprint") != fingerprint:
        raise Stage3FError("dataset fingerprint mismatch")
    return rows, fingerprint


def _privacy_findings(rows: list[dict[str, Any]]) -> int:
    return sum(
        len(scan_text(text))
        for row in rows
        for text in (
            [
                str(item.get("content", ""))
                for item in row.get("conversation_context", [])
            ]
            + [str(item) for item in row.get("human_target_bubbles", [])]
        )
    )


def _suspicious_zero_values(features: dict[str, Any]) -> list[str]:
    suspicious: list[str] = []
    if features.get("typo_frequency") == 0.0:
        suspicious.append("typo_frequency")
    for key in ("response_delay_seconds", "inter_bubble_delay_seconds"):
        value = features.get(key, {})
        if (
            isinstance(value, dict)
            and value.get("count") == 0
            and value.get("availability") != "unavailable"
        ):
            suspicious.append(key)
    return suspicious


def _aggregate_profile_diff(
    actual: dict[str, Any],
    expected: dict[str, Any],
) -> dict[str, Any]:
    actual_features = set(actual.get("features", {}))
    expected_features = set(expected.get("features", {}))
    changed_aggregate_features = sorted(
        key
        for key in actual_features.intersection(expected_features)
        if key not in _RELATIONSHIP_VALUE_FEATURES
        and actual["features"].get(key) != expected["features"].get(key)
    )
    return {
        "schema_version_matches": (
            actual.get("schema_version") == expected.get("schema_version")
        ),
        "missing_feature_names": sorted(expected_features - actual_features),
        "unexpected_feature_names": sorted(actual_features - expected_features),
        "changed_aggregate_feature_names": changed_aggregate_features,
        "fixed_rules_empty": actual.get("fixed_rules") == [],
    }


_RELATIONSHIP_VALUE_FEATURES = {
    "common_short_replies",
    "frequent_lexicon",
    "greeting_forms",
}


def _write_summary(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Stage 3F profile audit",
        "",
        f"- Status: {', '.join(summary['statuses'])}",
        f"- Source examples: {summary['source_examples']}",
        f"- Owner message bubbles: {summary['owner_message_bubbles']}",
        f"- Supported features: {', '.join(summary['supported_features'])}",
        (
            "- Unsupported features: "
            f"{', '.join(summary['unsupported_features'])}"
        ),
        (
            "- Suspicious zero values: "
            f"{', '.join(summary['suspicious_zero_values']) or 'none'}"
        ),
        (
            "- Missing timestamps: "
            f"{json.dumps(summary['missing_timestamps'], sort_keys=True)}"
        ),
        f"- Profile scope: {json.dumps(summary['profile_scope'], sort_keys=True)}",
        f"- Relationship diversity: {summary['relationship_count']}",
        f"- Contact diversity: {summary['contact_count']}",
        (
            "- Global bias warnings: "
            f"{', '.join(summary['global_bias_warnings']) or 'none'}"
        ),
        f"- Migration performed: {summary['migration_performed']}",
        f"- Recommended next step: {summary['recommended_next_step']}",
        "",
        "No raw private messages or lexical examples are included in this report.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise Stage3FError(f"required file is missing: {path.name}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()
