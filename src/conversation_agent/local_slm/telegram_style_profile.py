"""Deterministic, descriptive style profiles for verified Telegram evidence."""

from __future__ import annotations

import copy
import math
import re
import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from typing import Any, Literal

StyleCasing = Literal[
    "lowercase",
    "normal_sentence_case",
    "all_caps",
    "mixed_case",
    "uncased",
]

PROFILE_SCHEMA_VERSION = 2
PROFANITY_LEXICON_VERSION = "limited-ru-v1"
CASING_CATEGORIES: tuple[StyleCasing, ...] = (
    "lowercase",
    "normal_sentence_case",
    "all_caps",
    "mixed_case",
    "uncased",
)

_WORD_PATTERN = re.compile(r"[A-Za-z\u0400-\u04ff][A-Za-z\u0400-\u04ff'-]*")
_LINK_PATTERN = re.compile(r"(?i)(?:https?://|www\.)\S+")
_EMOJI_PATTERN = re.compile(
    "[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF\u2600-\u27BF]"
)
_GREETING_PATTERN = re.compile(
    r"(?i)^(?:\u043f\u0440\u0438\u0432\u0435\u0442(?:\u0438\u043a)?|"
    r"\u0437\u0434\u0440\u0430\u0432\u0441\u0442\u0432\u0443\u0439(?:\u0442\u0435)?|"
    r"\u0434\u043e\u0431\u0440(?:\u044b\u0439|\u043e\u0435)\s+"
    r"(?:\u0434\u0435\u043d\u044c|\u0443\u0442\u0440\u043e|\u0432\u0435\u0447\u0435\u0440)|"
    r"\u0445\u0430\u0439|\u0445\u0435\u0439)\b"
)
_PROFANITY_PATTERN = re.compile(
    r"(?i)\b(?:\u0431\u043b\u044f\w*|\u0445\u0443\u0439\w*|"
    r"\u043f\u0438\u0437\u0434\w*|\u0435\u0431\w*|\u0441\u0443\u043a\w*)\b"
)
_FUNCTION_WORDS = frozenset(
    {
        "and",
        "but",
        "for",
        "the",
        "this",
        "that",
        "\u0430",
        "\u0432",
        "\u0434\u0430",
        "\u0438",
        "\u043a",
        "\u043d\u0430",
        "\u043d\u0435",
        "\u043d\u043e",
        "\u043f\u043e",
        "\u0441",
        "\u0442\u043e",
        "\u0447\u0442\u043e",
        "\u044d\u0442\u043e",
        "\u044f",
    }
)
_GLOBAL_CANDIDATES = frozenset(
    {
        "casing",
        "punctuation",
        "emoji",
        "message_length_chars",
        "bubble_count",
        "response_length",
        "sentence_completeness",
        "context",
    }
)
_RELATIONSHIP_SPECIFIC = frozenset(
    {
        "greeting_forms",
        "common_short_replies",
        "frequent_lexicon",
        "slang_profanity",
    }
)


@dataclass(frozen=True)
class StyleExtractionConfig:
    short_incoming_characters: int = 25
    long_incoming_characters: int = 280


@dataclass(frozen=True)
class ConfidenceCalibrationConfig:
    sample_reference: int = 50
    bubble_reference: int = 75
    temporal_days_reference: int = 30
    relationship_diversity_reference: int = 3
    contact_diversity_reference: int = 3
    single_relationship_global_multiplier: float = 0.55
    missing_temporal_factor: float = 0.45


def dataset_rows_to_profile_episodes(
    rows: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert confirmed rows without inventing incoming or bubble timestamps."""
    episodes: list[dict[str, Any]] = []
    for row in rows:
        turns = [
            {
                "role": str(item.get("role", "")),
                "content": str(item.get("content", "")),
            }
            for item in row.get("conversation_context", [])
        ]
        boundary = len(turns)
        while boundary and turns[boundary - 1]["role"] == "contact":
            boundary -= 1
        incoming_turns = turns[boundary:]
        timestamp = str(row.get("timestamp", "")).strip()
        target = [str(item) for item in row.get("human_target_bubbles", [])]
        relationship = row.get("relationship_context", {})
        episodes.append(
            {
                "example_id": row.get("example_id"),
                "agent_id": row.get("agent_id", "private-agent"),
                "relationship_id": relationship.get(
                    "contact_alias", "private_contact"
                ),
                "contact_alias": relationship.get(
                    "contact_alias", "private_contact"
                ),
                "human_target": {
                    "messages": target,
                    # A confirmed row has one episode timestamp, not one timestamp
                    # per bubble. Keep it separate to avoid fake zero intervals.
                    "timestamps": [],
                },
                "incoming": {
                    "messages": [
                        item["content"]
                        for item in incoming_turns
                        if item["content"].strip()
                    ],
                    "timestamps": [],
                    "media_metadata": [],
                },
                "context_turns": turns[:boundary],
                "episode_timestamp": timestamp or None,
                "provenance": {
                    "classification": "human_confirmed",
                    "verified": row.get("provenance", {}).get("verified") is True,
                },
            }
        )
    return episodes


def build_style_profiles(
    episodes: list[dict[str, Any]],
    *,
    agent_id: str,
    relationship_id: str,
    generated_at: str,
    extraction_config: StyleExtractionConfig | None = None,
    calibration_config: ConfidenceCalibrationConfig | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    extraction = extraction_config or StyleExtractionConfig()
    calibration = calibration_config or ConfidenceCalibrationConfig()
    usable = [
        item
        for item in episodes
        if item.get("human_target", {}).get("messages")
        and item.get("provenance", {}).get("classification")
        in {"human_confirmed", "human_edited_ai"}
    ]
    features = _collect_features(usable, extraction)
    sample_count = len(usable)
    bubble_count = sum(
        len(item.get("human_target", {}).get("messages", [])) for item in usable
    )
    relationship_ids = {
        str(item.get("relationship_id", relationship_id)) for item in usable
    }
    contact_ids = {
        str(item.get("contact_alias", item.get("relationship_id", relationship_id)))
        for item in usable
    }
    timestamps = [
        value
        for item in usable
        if (value := str(item.get("episode_timestamp") or "").strip())
    ]
    evidence = _evidence_diversity(
        usable,
        sample_count=sample_count,
        bubble_count=bubble_count,
        relationship_count=len(relationship_ids),
        contact_count=len(contact_ids),
        timestamps=timestamps,
    )
    global_confidence, relationship_confidence, reasons = _calibrated_confidences(
        evidence,
        calibration,
    )
    relationship_feature_confidences = _feature_confidences(
        features,
        relationship_confidence,
    )
    agent_feature_confidences = {
        key: (
            round(value * global_confidence / relationship_confidence, 6)
            if relationship_confidence > 0 and key in _GLOBAL_CANDIDATES
            else 0.0
        )
        for key, value in relationship_feature_confidences.items()
    }
    provenance = Counter(
        str(item.get("provenance", {}).get("classification", "unknown"))
        for item in usable
    )
    feature_scopes = {
        key: (
            "global_candidate"
            if key in _GLOBAL_CANDIDATES
            else (
                "relationship_specific"
                if key in _RELATIONSHIP_SPECIFIC
                else (
                    "conversation_local"
                    if key == "context"
                    else "insufficient_cross_relationship_evidence"
                )
            )
        )
        for key in features
    }
    common = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "generated_at": generated_at,
        "sample_count": sample_count,
        "bubble_sample_count": bubble_count,
        "date_range": {
            "from": min(timestamps, default=None),
            "until": max(timestamps, default=None),
        },
        "recency": max(timestamps, default=None),
        "provenance_breakdown": dict(sorted(provenance.items())),
        "fixed_rules": [],
        "interpretation": "descriptive_distributions_not_prescriptive_rules",
        "features": features,
        "feature_scopes": feature_scopes,
        "evidence_diversity": evidence,
        "global_agent_confidence": global_confidence,
        "relationship_confidence": relationship_confidence,
        "single_relationship_bias": evidence["relationship_count"] == 1,
        "calibration_reason": reasons,
        "extraction_config": {
            "short_incoming_characters": extraction.short_incoming_characters,
            "long_incoming_characters": extraction.long_incoming_characters,
        },
        "calibration_config": {
            "sample_reference": calibration.sample_reference,
            "bubble_reference": calibration.bubble_reference,
            "temporal_days_reference": calibration.temporal_days_reference,
            "relationship_diversity_reference": (
                calibration.relationship_diversity_reference
            ),
            "contact_diversity_reference": calibration.contact_diversity_reference,
            "single_relationship_global_multiplier": (
                calibration.single_relationship_global_multiplier
            ),
            "missing_temporal_factor": calibration.missing_temporal_factor,
        },
        "migration": {
            "performed": False,
            "from_schema_version": None,
            "lossy_fields": [],
        },
    }
    agent_features = _agent_scoped_features(features)
    agent_profile = {
        **common,
        "profile_type": "agent_style_preview",
        "agent_id": agent_id,
        "scope": (
            "insufficient_cross_relationship_evidence"
            if evidence["relationship_count"] < 2
            else "global_candidate"
        ),
        "confidence": global_confidence,
        "features": agent_features,
        "feature_confidences": agent_feature_confidences,
        "confidence_per_feature": agent_feature_confidences,
        "unverified_candidates_are_evidence": False,
    }
    relationship_profile = {
        **common,
        "profile_type": "relationship_style_preview",
        "agent_id": agent_id,
        "relationship_id": relationship_id,
        "contact_alias": "contact_private_001",
        "scope": "relationship_specific",
        "confidence": relationship_confidence,
        "feature_confidences": relationship_feature_confidences,
        "confidence_per_feature": relationship_feature_confidences,
        "unverified_candidates_are_evidence": False,
        "interaction_patterns": _relationship_patterns(usable),
    }
    return agent_profile, relationship_profile


def _agent_scoped_features(features: dict[str, Any]) -> dict[str, Any]:
    output = copy.deepcopy(features)
    for key in ("greeting_forms", "common_short_replies", "frequent_lexicon"):
        value = output.get(key)
        if isinstance(value, dict):
            value.pop("values", None)
            value["global_resolution_supported"] = False
            value["reason"] = "insufficient_cross_relationship_evidence"
    slang = output.get("slang_profanity")
    if isinstance(slang, dict):
        output["slang_profanity"] = {
            "detector_coverage": slang.get("detector_coverage"),
            "known_profanity_lexicon_version": slang.get(
                "known_profanity_lexicon_version"
            ),
            "scope": "insufficient_cross_relationship_evidence",
            "global_resolution_supported": False,
            "reason": "relationship-specific evidence from one relationship",
        }
    return output


def migrate_style_profile(value: dict[str, Any]) -> dict[str, Any]:
    """Read schema v1 profiles without treating first-letter case as ALL CAPS."""
    profile = copy.deepcopy(value)
    version = int(profile.get("schema_version", 1))
    if version >= PROFILE_SCHEMA_VERSION:
        profile.setdefault(
            "migration",
            {"performed": False, "from_schema_version": None, "lossy_fields": []},
        )
        return profile
    features = profile.setdefault("features", {})
    legacy = features.get("casing", {})
    lower = float(legacy.get("lowercase_frequency", 0.0))
    normal = float(legacy.get("uppercase_frequency", 0.0))
    ambiguous = float(legacy.get("mixed_or_uncased_frequency", 0.0))
    features["casing"] = {
        "counts": {},
        "distribution": {
            "lowercase": lower,
            "normal_sentence_case": normal,
            "all_caps": 0.0,
            "mixed_case": ambiguous,
            "uncased": 0.0,
        },
        "sample_count": int(profile.get("bubble_sample_count", 0)),
        "migration_note": (
            "v1 uppercase_frequency meant first-letter uppercase; "
            "mixed and uncased evidence cannot be separated"
        ),
    }
    legacy_typo = features.pop("typo_frequency", None)
    features["typo"] = {
        "supported": False,
        "explicit_human_typo_label_rate": None,
        "spelling_anomaly_rate": None,
        "informal_spelling_rate": None,
        "unknown_typo_status": True,
        "legacy_value_ignored": legacy_typo,
        "explanation": "not measured without authoritative labels",
    }
    features["typo_frequency"] = None
    for key in ("response_delay_seconds", "inter_bubble_delay_seconds"):
        timing = features.get(key)
        if isinstance(timing, dict):
            timing.setdefault(
                "availability",
                "available" if int(timing.get("count", 0)) else "unavailable",
            )
            timing.setdefault("missing_count", 0)
            timing.setdefault("zero_count", 0)
            timing.setdefault("invalid_order_count", 0)
    slang = features.get("slang_profanity")
    if isinstance(slang, dict):
        slang.setdefault("detector_coverage", "limited_lexicon")
        slang.setdefault("known_profanity_lexicon_version", "legacy-unspecified")
        slang.setdefault("matched_message_count", slang.get("observation_count", 0))
        slang.setdefault("matched_token_count", None)
        slang.setdefault("unknown_informal_lexicon_rate", None)
    old_confidence = float(profile.get("confidence", 0.0))
    profile["global_agent_confidence"] = min(old_confidence, 0.45)
    profile["relationship_confidence"] = old_confidence
    profile["single_relationship_bias"] = True
    profile["feature_confidences"] = dict(
        profile.get("confidence_per_feature", {})
    )
    profile.setdefault(
        "evidence_diversity",
        {
            "relationship_count": 1,
            "contact_count": 1,
            "temporal_coverage_days": None,
            "missing_feature_rate": None,
            "provenance_quality": None,
            "distribution_stability": None,
        },
    )
    profile.setdefault(
        "feature_scopes",
        {
            key: (
                "global_candidate"
                if key in _GLOBAL_CANDIDATES
                else (
                    "relationship_specific"
                    if key in _RELATIONSHIP_SPECIFIC
                    else "insufficient_cross_relationship_evidence"
                )
            )
            for key in features
        },
    )
    profile["calibration_reason"] = [
        "legacy confidence retained for relationship evidence",
        "global confidence limited because v1 lacks relationship diversity",
    ]
    profile["schema_version"] = PROFILE_SCHEMA_VERSION
    profile["migration"] = {
        "performed": True,
        "from_schema_version": version,
        "lossy_fields": [
            "features.casing.mixed_or_uncased_frequency",
            "features.typo_frequency",
        ],
    }
    profile.setdefault("fixed_rules", [])
    return profile


def classify_casing(value: str) -> StyleCasing:
    letters = [item for item in value if item.isalpha()]
    if not letters:
        return "uncased"
    if all(item.isupper() for item in letters):
        return "all_caps"
    if all(item.islower() for item in letters):
        return "lowercase"
    first_index = next(index for index, item in enumerate(value) if item.isalpha())
    first = value[first_index]
    remaining = [item for item in value[first_index + 1 :] if item.isalpha()]
    if first.isupper() and all(item.islower() for item in remaining):
        return "normal_sentence_case"
    return "mixed_case"


def extract_target_surface(messages: Sequence[str]) -> dict[str, Any]:
    text = " ".join(str(item) for item in messages)
    bubble_casing = [classify_casing(str(item)) for item in messages if str(item)]
    casing = Counter(bubble_casing).most_common(1)[0][0] if bubble_casing else "uncased"
    length = sum(len(str(item)) for item in messages)
    profanity = any(_PROFANITY_PATTERN.search(str(item)) for item in messages)
    return {
        "casing": casing,
        "bubble_count": len(messages),
        "length": length,
        "length_band": _length_band(length),
        "asks_question": "?" in text,
        "final_punctuation": bool(text.rstrip().endswith((".", "!", "?", "\u2026"))),
        "emoji_present": bool(_EMOJI_PATTERN.search(text)),
        "profanity_or_slang_tendency": profanity,
        "short_response": 0 < length <= 20,
        "sentence_complete": _looks_complete(text),
    }


def resolve_style_plan(
    *,
    agent_profile: dict[str, Any] | None,
    relationship_profile: dict[str, Any] | None,
    incoming_messages: Sequence[str] = (),
    conversation_owner_messages: Sequence[str] = (),
    mode: str = "agent_relationship",
) -> dict[str, Any]:
    agent = migrate_style_profile(agent_profile) if agent_profile else None
    relationship = (
        migrate_style_profile(relationship_profile)
        if relationship_profile
        else None
    )
    if mode == "neutral_fallback":
        return _neutral_style_plan()
    use_relationship = mode in {
        "relationship_profile_only",
        "agent_relationship",
        "agent_relationship_conversation",
    }
    use_agent = mode in {
        "agent_profile_only",
        "agent_relationship",
        "agent_relationship_conversation",
    }
    selected = relationship if use_relationship and relationship else agent if use_agent else None
    if selected is None:
        return _neutral_style_plan()
    features = selected.get("features", {})
    confidence = float(selected.get("confidence", 0.0))
    feature_confidences = dict(selected.get("feature_confidences", {}))
    casing_dist = features.get("casing", {}).get("distribution", {})
    casing = max(
        CASING_CATEGORIES,
        key=lambda item: float(casing_dist.get(item, 0.0)),
    )
    response_length = features.get("response_length", {})
    bubbles = features.get("bubble_count", {})
    punctuation = features.get("punctuation", {})
    emoji = features.get("emoji", {})
    sentence = float(features.get("sentence_completeness", 0.0))
    slang = features.get("slang_profanity", {})
    length = round(float(response_length.get("median") or 80))
    bubble_count = max(1, round(float(bubbles.get("median") or 1)))
    snapshot_count = len(conversation_owner_messages)
    missing = [
        key
        for key in ("response_delay_seconds", "inter_bubble_delay_seconds", "typo")
        if (
            features.get(key, {}).get("availability") == "unavailable"
            if isinstance(features.get(key), dict)
            else not features.get(key)
        )
    ]
    return {
        "casing": casing,
        "bubble_count": bubble_count,
        "length": length,
        "length_band": _length_band(length),
        "asks_question": float(punctuation.get("question_frequency", 0.0)) >= 0.5,
        "final_punctuation": (
            float(punctuation.get("final_punctuation_frequency", 0.0)) >= 0.5
        ),
        "emoji_present": float(emoji.get("frequency", 0.0)) >= 0.5,
        "profanity_or_slang_tendency": (
            use_relationship
            and float(slang.get("matched_message_rate", 0.0)) >= 0.5
        ),
        "short_response": 0 < length <= 20,
        "sentence_complete": sentence >= 0.5,
        "fallback": confidence < 0.2,
        "confidence": confidence,
        "feature_confidences": feature_confidences,
        "selected_profile_scope": selected.get("scope"),
        "incoming_evidence_count": len(incoming_messages),
        "conversation_snapshot_evidence_count": snapshot_count,
        "conversation_snapshot_applied": False,
        "conversation_snapshot_reason": (
            "observed but not applied: no validated context-conditioned feature model"
            if mode == "agent_relationship_conversation" and snapshot_count
            else "not selected"
        ),
        "exact_lexical_reuse": False,
        "fixed_rules": [],
        "missing_feature_warnings": missing,
    }


def _collect_features(
    episodes: list[dict[str, Any]],
    config: StyleExtractionConfig,
) -> dict[str, Any]:
    targets = [item["human_target"] for item in episodes]
    bubbles = [
        str(message)
        for target in targets
        for message in target.get("messages", [])
        if str(message).strip()
    ]
    casing_counts = Counter(classify_casing(item) for item in bubbles)
    words = [
        match.group(0).casefold()
        for text in bubbles
        for match in _WORD_PATTERN.finditer(text)
    ]
    content_words = [
        item for item in words if len(item) > 1 and item not in _FUNCTION_WORDS
    ]
    emojis = [
        match.group(0)
        for text in bubbles
        for match in _EMOJI_PATTERN.finditer(text)
    ]
    greetings = [
        match.group(0).casefold()
        for text in bubbles
        if (match := _GREETING_PATTERN.search(text.strip()))
    ]
    short_replies = [
        text.strip().casefold() for text in bubbles if 0 < len(text.strip()) <= 20
    ]
    profanity_matches = [
        match.group(0)
        for text in bubbles
        for match in _PROFANITY_PATTERN.finditer(text)
    ]
    matched_messages = sum(bool(_PROFANITY_PATTERN.search(item)) for item in bubbles)
    response_timing = _response_timing(episodes)
    bubble_timing = _inter_bubble_timing(episodes)
    return {
        "casing": {
            "counts": {
                key: casing_counts.get(key, 0) for key in CASING_CATEGORIES
            },
            "distribution": {
                key: round(casing_counts.get(key, 0) / len(bubbles), 6)
                if bubbles
                else 0.0
                for key in CASING_CATEGORIES
            },
            "sample_count": len(bubbles),
        },
        "punctuation": {
            "final_punctuation_frequency": _mean(
                item.rstrip().endswith((".", "!", "?", "\u2026"))
                for item in bubbles
            ),
            "question_frequency": _mean("?" in item for item in bubbles),
            "exclamation_frequency": _mean("!" in item for item in bubbles),
        },
        "emoji": {
            "frequency": _mean(bool(_EMOJI_PATTERN.search(item)) for item in bubbles),
            "frequent": _counter_distribution(emojis, 12),
        },
        "message_length_chars": _numeric_distribution([len(item) for item in bubbles]),
        "bubble_count": _numeric_distribution(
            [len(target.get("messages", [])) for target in targets]
        ),
        "inter_bubble_delay_seconds": bubble_timing,
        "response_delay_seconds": response_timing,
        "greeting_forms": _counter_distribution(greetings, 12),
        "common_short_replies": {
            **_counter_distribution(short_replies, 20),
            "usage": "observed_retrieval_candidates_only",
            "exact_lexical_reuse_allowed": False,
        },
        "frequent_lexicon": {
            **_counter_distribution(content_words, 30),
            "usage": "relationship_evidence_only",
        },
        "slang_profanity": {
            "detector_coverage": "limited_lexicon",
            "known_profanity_lexicon_version": PROFANITY_LEXICON_VERSION,
            "matched_token_count": len(profanity_matches),
            "matched_message_count": matched_messages,
            "matched_message_rate": round(matched_messages / len(bubbles), 6)
            if bubbles
            else 0.0,
            "unknown_informal_lexicon_rate": None,
            "scope": "relationship_specific",
        },
        "typo": {
            "supported": False,
            "explicit_human_typo_label_rate": None,
            "spelling_anomaly_rate": None,
            "informal_spelling_rate": None,
            "unknown_typo_status": True,
            "explanation": (
                "not measured: no authoritative human typo labels or reliable "
                "deterministic detector"
            ),
        },
        "typo_frequency": None,
        "sentence_completeness": _mean(_looks_complete(item) for item in bubbles),
        "response_length": _numeric_distribution(
            [
                sum(len(message) for message in target.get("messages", []))
                for target in targets
            ]
        ),
        "context": _context_features(episodes, config),
        "context_specific_clusters": _context_clusters(episodes, config),
    }


def _context_features(
    episodes: list[dict[str, Any]],
    config: StyleExtractionConfig,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for episode in episodes:
        incoming_messages = [
            str(item) for item in episode.get("incoming", {}).get("messages", [])
        ]
        target_messages = [
            str(item) for item in episode.get("human_target", {}).get("messages", [])
        ]
        incoming = " ".join(incoming_messages)
        target = " ".join(target_messages)
        char_count = len(incoming)
        has_question = "?" in incoming
        owner_question = "?" in target
        rows.append(
            {
                "incoming_character_count": char_count,
                "incoming_bubble_count": len(incoming_messages),
                "incoming_has_question": has_question,
                "incoming_has_link": bool(_LINK_PATTERN.search(incoming)),
                "incoming_has_media_metadata": bool(
                    episode.get("incoming", {}).get("media_metadata")
                ),
                "incoming_contains_profanity": bool(
                    _PROFANITY_PATTERN.search(incoming)
                ),
                "incoming_is_short": (
                    bool(incoming.strip())
                    and char_count <= config.short_incoming_characters
                ),
                "incoming_is_long": char_count >= config.long_incoming_characters,
                "owner_answers_question": has_question and bool(target.strip()),
                "owner_asks_question": owner_question,
                "interaction_mode": (
                    "contact_question_owner_question"
                    if has_question and owner_question
                    else (
                        "contact_question_owner_statement"
                        if has_question
                        else (
                            "contact_statement_owner_question"
                            if owner_question
                            else "statement_exchange"
                        )
                    )
                ),
            }
        )
    return {
        "sample_count": len(rows),
        "thresholds": {
            "short_incoming_characters": config.short_incoming_characters,
            "long_incoming_characters": config.long_incoming_characters,
        },
        "incoming_character_count": _numeric_distribution(
            [item["incoming_character_count"] for item in rows]
        ),
        "incoming_bubble_count": _numeric_distribution(
            [item["incoming_bubble_count"] for item in rows]
        ),
        **{
            key: {
                "count": sum(bool(item[key]) for item in rows),
                "frequency": _mean(bool(item[key]) for item in rows),
            }
            for key in (
                "incoming_has_question",
                "incoming_has_link",
                "incoming_has_media_metadata",
                "incoming_contains_profanity",
                "incoming_is_short",
                "incoming_is_long",
                "owner_answers_question",
                "owner_asks_question",
            )
        },
        "topic_category": {
            "supported": False,
            "reason": "no deterministic topic taxonomy configured",
        },
        "interaction_mode": _counter_distribution(
            [str(item["interaction_mode"]) for item in rows],
            10,
        ),
    }


def _context_clusters(
    episodes: list[dict[str, Any]],
    config: StyleExtractionConfig,
) -> dict[str, Any]:
    features = _context_features(episodes, config)
    mapping = {
        "incoming_question": "incoming_has_question",
        "short_incoming": "incoming_is_short",
        "long_incoming": "incoming_is_long",
        "incoming_link": "incoming_has_link",
        "incoming_media": "incoming_has_media_metadata",
        "incoming_profanity": "incoming_contains_profanity",
        "owner_question": "owner_asks_question",
    }
    return {
        "counts": {
            output: int(features[source]["count"])
            for output, source in mapping.items()
        },
        "distribution": {
            output: float(features[source]["frequency"])
            for output, source in mapping.items()
        },
        "source": "contact_incoming_and_prior_context_only",
        "thresholds": features["thresholds"],
    }


def _relationship_patterns(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    incoming = [
        " ".join(str(value) for value in item.get("incoming", {}).get("messages", []))
        for item in episodes
    ]
    return {
        "episode_count": len(episodes),
        "incoming_question_frequency": _mean("?" in item for item in incoming),
        "owner_multi_bubble_frequency": _mean(
            len(item.get("human_target", {}).get("messages", [])) > 1
            for item in episodes
        ),
        "context_turn_distribution": _numeric_distribution(
            [len(item.get("context_turns", [])) for item in episodes]
        ),
        "response_delay_seconds": _response_timing(episodes),
    }


def _response_timing(episodes: Sequence[dict[str, Any]]) -> dict[str, Any]:
    values: list[float] = []
    missing = 0
    invalid = 0
    for episode in episodes:
        incoming = episode.get("incoming", {}).get("timestamps", [])
        outgoing = episode.get("human_target", {}).get("timestamps", [])
        if not incoming or not outgoing:
            missing += 1
            continue
        start = _parse_datetime(str(outgoing[0]))
        end = _parse_datetime(str(incoming[-1]))
        if start is None or end is None:
            missing += 1
            continue
        delta = (start - end).total_seconds()
        if delta < 0:
            invalid += 1
            continue
        values.append(delta)
    return _timing_distribution(values, missing=missing, invalid=invalid)


def _inter_bubble_timing(episodes: Sequence[dict[str, Any]]) -> dict[str, Any]:
    values: list[float] = []
    missing = 0
    invalid = 0
    for episode in episodes:
        target = episode.get("human_target", {})
        messages = target.get("messages", [])
        timestamps = target.get("timestamps", [])
        expected = max(0, len(messages) - 1)
        if expected == 0:
            continue
        if len(timestamps) != len(messages):
            missing += expected
            continue
        parsed = [_parse_datetime(str(item)) for item in timestamps]
        if any(item is None for item in parsed):
            missing += expected
            continue
        for previous, current in pairwise(parsed):
            if previous is None or current is None:
                missing += 1
                continue
            delta = (current - previous).total_seconds()
            if delta < 0:
                invalid += 1
            else:
                values.append(delta)
    return _timing_distribution(values, missing=missing, invalid=invalid)


def _timing_distribution(
    values: Sequence[float],
    *,
    missing: int,
    invalid: int,
) -> dict[str, Any]:
    result = _numeric_distribution(values)
    result.update(
        {
            "availability": "available" if values else "unavailable",
            "missing_count": missing,
            "zero_count": sum(value == 0 for value in values),
            "invalid_order_count": invalid,
            "timestamps_deliberately_removed": False,
        }
    )
    return result


def _evidence_diversity(
    episodes: Sequence[dict[str, Any]],
    *,
    sample_count: int,
    bubble_count: int,
    relationship_count: int,
    contact_count: int,
    timestamps: Sequence[str],
) -> dict[str, Any]:
    parsed = [item for value in timestamps if (item := _parse_datetime(value))]
    coverage = (
        max(0.0, (max(parsed) - min(parsed)).total_seconds() / 86400)
        if len(parsed) >= 2
        else None
    )
    provenance_quality = _mean(
        item.get("provenance", {}).get("verified") is True for item in episodes
    )
    return {
        "sample_count": sample_count,
        "effective_message_count": bubble_count,
        "relationship_count": relationship_count,
        "contact_count": contact_count,
        "temporal_coverage_days": round(coverage, 3) if coverage is not None else None,
        "timestamp_coverage": round(len(parsed) / sample_count, 6)
        if sample_count
        else 0.0,
        "missing_feature_rate": round(
            sum(
                (
                    not parsed,
                    not any(
                        item.get("incoming", {}).get("timestamps")
                        for item in episodes
                    ),
                    True,  # typo labels are unavailable
                )
            )
            / 3,
            6,
        ),
        "provenance_quality": provenance_quality,
        "distribution_stability": _distribution_stability(episodes),
    }


def _calibrated_confidences(
    evidence: dict[str, Any],
    config: ConfidenceCalibrationConfig,
) -> tuple[float, float, list[str]]:
    sample = _saturation(int(evidence["sample_count"]), config.sample_reference)
    bubbles = _saturation(
        int(evidence["effective_message_count"]), config.bubble_reference
    )
    temporal_days = evidence.get("temporal_coverage_days")
    temporal = (
        _saturation(float(temporal_days), config.temporal_days_reference)
        if temporal_days is not None
        else config.missing_temporal_factor
    )
    provenance = float(evidence["provenance_quality"])
    stability = float(evidence["distribution_stability"])
    relationship = round(
        0.35 * sample
        + 0.20 * bubbles
        + 0.15 * temporal
        + 0.20 * provenance
        + 0.10 * stability,
        6,
    )
    relationship_diversity = _saturation(
        int(evidence["relationship_count"]),
        config.relationship_diversity_reference,
    )
    contact_diversity = _saturation(
        int(evidence["contact_count"]), config.contact_diversity_reference
    )
    global_base = (
        0.25 * sample
        + 0.15 * bubbles
        + 0.20 * relationship_diversity
        + 0.15 * contact_diversity
        + 0.10 * temporal
        + 0.10 * provenance
        + 0.05 * stability
    )
    reasons = [
        f"sample evidence factor={sample:.3f}",
        f"relationship evidence confidence={relationship:.3f}",
    ]
    if int(evidence["relationship_count"]) == 1:
        global_base *= config.single_relationship_global_multiplier
        reasons.append(
            "global evidence multiplied by configurable "
            f"single-relationship factor={config.single_relationship_global_multiplier:.3f}"
        )
    if temporal_days is None:
        reasons.append("temporal coverage unavailable for timing calibration")
    return round(global_base, 6), relationship, reasons


def _feature_confidences(
    features: dict[str, Any],
    relationship_confidence: float,
) -> dict[str, float]:
    reliability = {
        "casing": 1.0,
        "punctuation": 1.0,
        "emoji": 1.0,
        "message_length_chars": 1.0,
        "bubble_count": 1.0,
        "inter_bubble_delay_seconds": 1.0,
        "response_delay_seconds": 1.0,
        "greeting_forms": 0.9,
        "common_short_replies": 0.7,
        "frequent_lexicon": 0.65,
        "slang_profanity": 0.65,
        "typo": 0.0,
        "typo_frequency": 0.0,
        "sentence_completeness": 0.8,
        "response_length": 1.0,
        "context": 1.0,
        "context_specific_clusters": 1.0,
    }
    output: dict[str, float] = {}
    for key, value in features.items():
        available = 1.0
        if isinstance(value, dict) and value.get("availability") == "unavailable":
            available = 0.0
        if isinstance(value, dict) and value.get("supported") is False:
            available = 0.0
        output[key] = round(
            relationship_confidence * reliability.get(key, 0.7) * available,
            6,
        )
    return output


def _distribution_stability(episodes: Sequence[dict[str, Any]]) -> float:
    if len(episodes) < 4:
        return _saturation(len(episodes), 10)
    midpoint = len(episodes) // 2
    halves = (episodes[:midpoint], episodes[midpoint:])
    dominant: list[str] = []
    for half in halves:
        counts = Counter(
            classify_casing(str(message))
            for item in half
            for message in item.get("human_target", {}).get("messages", [])
        )
        dominant.append(counts.most_common(1)[0][0] if counts else "uncased")
    return 1.0 if dominant[0] == dominant[1] else 0.6


def _numeric_distribution(values: Iterable[int | float]) -> dict[str, Any]:
    numbers = sorted(float(item) for item in values)
    if not numbers:
        return {
            "count": 0,
            "min": None,
            "p25": None,
            "median": None,
            "p75": None,
            "p90": None,
            "max": None,
        }
    return {
        "count": len(numbers),
        "min": round(numbers[0], 3),
        "p25": round(_percentile(numbers, 0.25), 3),
        "median": round(_percentile(numbers, 0.5), 3),
        "p75": round(_percentile(numbers, 0.75), 3),
        "p90": round(_percentile(numbers, 0.9), 3),
        "max": round(numbers[-1], 3),
    }


def _counter_distribution(values: Iterable[str], limit: int) -> dict[str, Any]:
    counter = Counter(item for item in values if item)
    total = sum(counter.values())
    return {
        "count": total,
        "values": [
            {
                "value": key,
                "count": count,
                "frequency": round(count / total, 6) if total else 0.0,
            }
            for key, count in counter.most_common(limit)
        ],
    }


def _neutral_style_plan() -> dict[str, Any]:
    return {
        "casing": "normal_sentence_case",
        "bubble_count": 1,
        "length": 80,
        "length_band": "medium",
        "asks_question": False,
        "final_punctuation": True,
        "emoji_present": False,
        "profanity_or_slang_tendency": False,
        "short_response": False,
        "sentence_complete": True,
        "fallback": True,
        "confidence": 0.0,
        "feature_confidences": {},
        "selected_profile_scope": "neutral_fallback",
        "incoming_evidence_count": 0,
        "exact_lexical_reuse": False,
        "fixed_rules": [],
        "missing_feature_warnings": [],
    }


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _percentile(values: list[float], ratio: float) -> float:
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * ratio
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def _mean(values: Iterable[bool | int | float]) -> float:
    rows = [float(item) for item in values]
    return round(statistics.fmean(rows), 6) if rows else 0.0


def _looks_complete(value: str) -> bool:
    words = _WORD_PATTERN.findall(value)
    return len(words) >= 3 or value.rstrip().endswith((".", "!", "?", "\u2026"))


def _length_band(value: int) -> str:
    if value <= 20:
        return "short"
    if value <= 80:
        return "medium"
    return "long"


def _saturation(value: float, reference: float) -> float:
    if reference <= 0:
        return 1.0
    return min(1.0, max(0.0, float(value) / float(reference)))
