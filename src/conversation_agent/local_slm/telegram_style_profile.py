"""Adaptive descriptive style profiles for local Telegram preview candidates."""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from itertools import pairwise
from typing import Any

_WORD_PATTERN = re.compile(r"[A-Za-z\u0400-\u04ff][A-Za-z\u0400-\u04ff'-]*")
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


def build_style_profiles(
    episodes: list[dict[str, Any]],
    *,
    agent_id: str,
    relationship_id: str,
    generated_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    usable = [
        item
        for item in episodes
        if item.get("human_target", {}).get("messages")
        and item.get("provenance", {}).get("classification") != "ai_generated"
    ]
    features = _collect_features(usable)
    provenance = Counter(
        str(item.get("provenance", {}).get("classification", "unknown"))
        for item in usable
    )
    date_values = [
        timestamp
        for item in usable
        for timestamp in item.get("human_target", {}).get("timestamps", [])
        if timestamp
    ]
    sample_count = len(usable)
    bubble_count = sum(
        len(item.get("human_target", {}).get("messages", [])) for item in usable
    )
    confidence = _confidence(sample_count)
    common = {
        "schema_version": 1,
        "generated_at": generated_at,
        "sample_count": sample_count,
        "bubble_sample_count": bubble_count,
        "confidence": confidence,
        "confidence_per_feature": {
            key: _feature_confidence(value, sample_count)
            for key, value in features.items()
        },
        "date_range": {
            "from": min(date_values, default=None),
            "until": max(date_values, default=None),
        },
        "recency": max(date_values, default=None),
        "provenance_breakdown": dict(sorted(provenance.items())),
        "fallback_status": (
            "insufficient_evidence"
            if sample_count < 5
            else "pilot_evidence_only"
        ),
        "fixed_rules": [],
        "interpretation": "descriptive_distributions_not_prescriptive_rules",
        "features": features,
    }
    agent_profile = {
        **common,
        "profile_type": "agent_style_preview",
        "agent_id": agent_id,
        "scope": "single_contact_pilot_not_global",
    }
    relationship_profile = {
        **common,
        "profile_type": "relationship_style_preview",
        "agent_id": agent_id,
        "relationship_id": relationship_id,
        "contact_alias": "contact_private_001",
        "interaction_patterns": _relationship_patterns(usable),
    }
    return agent_profile, relationship_profile


def _collect_features(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    targets = [item["human_target"] for item in episodes]
    bubbles = [
        str(message)
        for target in targets
        for message in target.get("messages", [])
        if str(message).strip()
    ]
    lengths = [len(item) for item in bubbles]
    words = [match.group(0).casefold() for text in bubbles for match in _WORD_PATTERN.finditer(text)]
    content_words = [item for item in words if len(item) > 1 and item not in _FUNCTION_WORDS]
    bubble_counts = [len(target.get("messages", [])) for target in targets]
    response_delays = [
        delay
        for item in episodes
        if (delay := _response_delay(item)) is not None
    ]
    inter_bubble_delays = [
        delay
        for target in targets
        for delay in _timestamp_deltas(target.get("timestamps", []))
    ]
    emojis = [match.group(0) for text in bubbles for match in _EMOJI_PATTERN.finditer(text)]
    greetings = [
        match.group(0).casefold()
        for text in bubbles
        if (match := _GREETING_PATTERN.search(text.strip()))
    ]
    short_replies = [
        text.strip().casefold()
        for text in bubbles
        if 0 < len(text.strip()) <= 20
    ]
    return {
        "casing": {
            "lowercase_frequency": _mean(_starts_lower(item) for item in bubbles),
            "uppercase_frequency": _mean(_starts_upper(item) for item in bubbles),
            "mixed_or_uncased_frequency": _mean(
                not _starts_lower(item) and not _starts_upper(item) for item in bubbles
            ),
        },
        "punctuation": {
            "final_punctuation_frequency": _mean(
                item.rstrip().endswith((".", "!", "?", "\u2026")) for item in bubbles
            ),
            "question_frequency": _mean("?" in item for item in bubbles),
            "exclamation_frequency": _mean("!" in item for item in bubbles),
        },
        "emoji": {
            "frequency": _mean(bool(_EMOJI_PATTERN.search(item)) for item in bubbles),
            "frequent": _counter_distribution(emojis, 12),
        },
        "message_length_chars": _numeric_distribution(lengths),
        "bubble_count": _numeric_distribution(bubble_counts),
        "inter_bubble_delay_seconds": _numeric_distribution(inter_bubble_delays),
        "response_delay_seconds": _numeric_distribution(response_delays),
        "greeting_forms": _counter_distribution(greetings, 12),
        "common_short_replies": _counter_distribution(short_replies, 20),
        "frequent_lexicon": _counter_distribution(content_words, 30),
        "slang_profanity": {
            "frequency": _mean(bool(_PROFANITY_PATTERN.search(item)) for item in bubbles),
            "observation_count": sum(bool(_PROFANITY_PATTERN.search(item)) for item in bubbles),
        },
        "typo_frequency": _mean(_looks_like_typo(item) for item in words),
        "sentence_completeness": _mean(_looks_complete(item) for item in bubbles),
        "response_length": _numeric_distribution(
            [sum(len(message) for message in target.get("messages", [])) for target in targets]
        ),
        "context_specific_clusters": _context_clusters(episodes),
    }


def _relationship_patterns(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    incoming_bubbles = [
        str(message)
        for item in episodes
        for message in item.get("incoming", {}).get("messages", [])
    ]
    return {
        "episode_count": len(episodes),
        "incoming_question_frequency": _mean("?" in item for item in incoming_bubbles),
        "owner_multi_bubble_frequency": _mean(
            len(item.get("human_target", {}).get("messages", [])) > 1
            for item in episodes
        ),
        "context_turn_distribution": _numeric_distribution(
            [len(item.get("context_turns", [])) for item in episodes]
        ),
        "response_delay_seconds": _numeric_distribution(
            [
                delay
                for item in episodes
                if (delay := _response_delay(item)) is not None
            ]
        ),
    }


def _context_clusters(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    counters: Counter[str] = Counter()
    for episode in episodes:
        incoming = " ".join(episode.get("incoming", {}).get("messages", []))
        if "?" in incoming:
            counters["incoming_question"] += 1
        if len(incoming) <= 25:
            counters["short_incoming"] += 1
        if _EMOJI_PATTERN.search(incoming):
            counters["emoji_context"] += 1
        if _PROFANITY_PATTERN.search(incoming):
            counters["high_emotion_or_profanity_context"] += 1
        if not incoming.strip().endswith("?"):
            counters["non_question_context"] += 1
    return {
        "counts": dict(sorted(counters.items())),
        "distribution": {
            key: round(value / len(episodes), 6) if episodes else 0.0
            for key, value in sorted(counters.items())
        },
    }


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


def _timestamp_deltas(values: Iterable[str]) -> list[float]:
    parsed = [_parse_datetime(item) for item in values]
    return [
        max(0.0, (current - previous).total_seconds())
        for previous, current in pairwise(parsed)
        if previous is not None and current is not None
    ]


def _response_delay(episode: dict[str, Any]) -> float | None:
    incoming = episode.get("incoming", {}).get("timestamps", [])
    outgoing = episode.get("human_target", {}).get("timestamps", [])
    if not incoming or not outgoing:
        return None
    start = _parse_datetime(str(outgoing[0]))
    end = _parse_datetime(str(incoming[-1]))
    if start is None or end is None:
        return None
    return max(0.0, (start - end).total_seconds())


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
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


def _starts_lower(value: str) -> bool:
    first = next((item for item in value if item.isalpha()), "")
    return bool(first and first.islower())


def _starts_upper(value: str) -> bool:
    first = next((item for item in value if item.isalpha()), "")
    return bool(first and first.isupper())


def _looks_like_typo(value: str) -> bool:
    return bool(re.search(r"(.)\1\1", value.casefold())) or any(
        character.isdigit() for character in value
    )


def _looks_complete(value: str) -> bool:
    words = _WORD_PATTERN.findall(value)
    return len(words) >= 3 or value.rstrip().endswith((".", "!", "?", "\u2026"))


def _confidence(sample_count: int) -> float:
    return round(min(1.0, math.log1p(sample_count) / math.log(101)), 6)


def _feature_confidence(value: Any, sample_count: int) -> float:
    evidence_count = sample_count
    if isinstance(value, dict) and isinstance(value.get("count"), int):
        evidence_count = int(value["count"])
    return _confidence(evidence_count)
