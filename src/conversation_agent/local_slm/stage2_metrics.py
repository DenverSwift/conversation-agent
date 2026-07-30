"""Automatic Stage 2 metrics without an LLM-as-a-judge."""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter
from typing import Any

from conversation_agent.local_slm.stage2_dataset import BenchmarkScenario


def evaluate_candidate(
    scenario: BenchmarkScenario,
    *,
    normalized: dict[str, Any] | None,
    validation: dict[str, Any],
    rubric: dict[str, Any],
) -> dict[str, Any]:
    if normalized is None:
        return {
            "expected_action_match": False,
            "bubble_count_compliance": False,
            "empty_reply": False,
            "repeated_bubble": False,
            "forbidden_claims": [],
            "required_fact_coverage": 0.0 if scenario.required_facts else 1.0,
            "unsupported_fact_flags": [],
            "assistant_phrase_flags": [],
        }
    action = str(normalized.get("action", ""))
    messages = [str(item) for item in normalized.get("messages", [])]
    text = "\n".join(messages)
    lowered = text.lower()
    required_hits = [
        fact for fact in scenario.required_facts if fact.lower() in lowered
    ]
    forbidden_hits = [
        claim for claim in scenario.forbidden_claims if claim.lower() in lowered
    ]
    return {
        "expected_action_match": action in scenario.expected_actions,
        "bubble_count_compliance": (
            scenario.min_bubbles <= len(messages) <= scenario.max_bubbles
        ),
        "empty_reply": action == "reply" and not messages,
        "repeated_bubble": len(set(messages)) != len(messages),
        "forbidden_claims": forbidden_hits,
        "required_fact_coverage": (
            len(required_hits) / len(scenario.required_facts)
            if scenario.required_facts
            else 1.0
        ),
        "unsupported_fact_flags": _unsupported_fact_flags(scenario, text),
        "assistant_phrase_flags": assistant_phrase_flags(
            scenario,
            messages=messages,
            rubric=rubric,
        ),
        "normalized_output_valid": bool(validation.get("valid", False)),
        "message_count": len(messages),
        "character_count": len(text),
    }


def assistant_phrase_flags(
    scenario: BenchmarkScenario,
    *,
    messages: list[str],
    rubric: dict[str, Any],
) -> list[str]:
    text = "\n".join(messages)
    lowered = text.lower()
    detector = rubric.get("assistant_phrase_detector", {})
    flags: list[str] = []
    for phrase in detector.get("phrases", []):
        if str(phrase).lower() in lowered:
            flags.append(f"phrase:{phrase}")
    thresholds = detector.get("thresholds", {})
    max_chars = int(thresholds.get("excessive_total_chars", 450))
    if len(text) > min(max_chars, scenario.max_total_chars):
        flags.append("excessive_length")
    if re.search(r"(?m)^\s*(?:#{1,3}\s+|\d+[.)]\s+|[-*]\s+)", text):
        flags.append("unnecessary_heading_or_list")
    if float(scenario.relationship.get("formality", 0.5)) < 0.45 and any(
        marker in lowered
        for marker in detector.get(
            "formal_markers",
            ["уважаемый", "благодарю вас", "будьте добры"],
        )
    ):
        flags.append("unjustified_formal_tone")
    incoming = " ".join(scenario.incoming_messages).lower()
    for message in messages:
        candidate = message.strip().lower().rstrip("?.!")
        if len(candidate) >= 20 and candidate in incoming:
            flags.append("unnecessary_question_repetition")
            break
    return sorted(set(flags))


def aggregate_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [item for item in results if not item.get("provider_error")]
    failures = [item for item in results if item.get("provider_error")]
    evaluated = [item.get("automatic_evaluation", {}) for item in completed]
    expected_no_reply = [
        item for item in completed if "no_reply" in item.get("expected_actions", [])
    ]
    predicted_no_reply = [
        item
        for item in completed
        if (item.get("normalized_output") or {}).get("action") == "no_reply"
    ]
    expected_handoff = [
        item for item in completed if "handoff" in item.get("expected_actions", [])
    ]
    predicted_handoff = [
        item
        for item in completed
        if (item.get("normalized_output") or {}).get("action") == "handoff"
    ]
    expected_reaction = [
        item for item in completed if "reaction" in item.get("expected_actions", [])
    ]
    latencies = [
        int(item["latency_ms"])
        for item in completed
        if isinstance(item.get("latency_ms"), int)
    ]
    completion_tokens = [
        int(item["completion_tokens"])
        for item in completed
        if isinstance(item.get("completion_tokens"), int)
    ]
    tokens_per_second = [
        float(item["tokens_per_second"])
        for item in completed
        if isinstance(item.get("tokens_per_second"), (int, float))
    ]
    total = len(results)
    return {
        "total_scenarios": total,
        "completed_scenarios": len(completed),
        "provider_failures": len(failures),
        "timeout_rate": _rate(
            sum("timeout" in str(item.get("provider_error", "")).lower() for item in failures),
            total,
        ),
        "schema_validity_rate": _rate(
            sum(bool(item.get("validation", {}).get("valid")) for item in completed),
            len(completed),
        ),
        "normalized_output_validity": _rate(
            sum(bool(value.get("normalized_output_valid")) for value in evaluated),
            len(evaluated),
        ),
        "repair_retry_rate": _rate(
            sum(int(item.get("retry_count", 0)) > 0 for item in completed),
            len(completed),
        ),
        "expected_action_match": _rate(
            sum(bool(value.get("expected_action_match")) for value in evaluated),
            len(evaluated),
        ),
        "no_reply_precision": _precision(predicted_no_reply, "no_reply"),
        "no_reply_recall": _recall(expected_no_reply, "no_reply"),
        "handoff_precision": _precision(predicted_handoff, "handoff"),
        "handoff_recall": _recall(expected_handoff, "handoff"),
        "reaction_action_match": _rate(
            sum(
                (item.get("normalized_output") or {}).get("action") == "reaction"
                for item in expected_reaction
            ),
            len(expected_reaction),
        ),
        "bubble_count_compliance": _rate(
            sum(bool(value.get("bubble_count_compliance")) for value in evaluated),
            len(evaluated),
        ),
        "empty_reply_rate": _rate(
            sum(bool(value.get("empty_reply")) for value in evaluated),
            len(evaluated),
        ),
        "repeated_bubble_rate": _rate(
            sum(bool(value.get("repeated_bubble")) for value in evaluated),
            len(evaluated),
        ),
        "forbidden_phrase_flag_rate": _rate(
            sum(bool(value.get("assistant_phrase_flags")) for value in evaluated),
            len(evaluated),
        ),
        "forbidden_claim_rate": _rate(
            sum(bool(value.get("forbidden_claims")) for value in evaluated),
            len(evaluated),
        ),
        "required_fact_coverage": _mean(
            [float(value.get("required_fact_coverage", 0.0)) for value in evaluated]
        ),
        "unsupported_fact_flags": sum(
            len(value.get("unsupported_fact_flags", [])) for value in evaluated
        ),
        "average_message_count": _mean(
            [float(value.get("message_count", 0)) for value in evaluated]
        ),
        "average_character_count": _mean(
            [float(value.get("character_count", 0)) for value in evaluated]
        ),
        "average_completion_tokens": _mean([float(item) for item in completion_tokens]),
        "median_latency_ms": _percentile(latencies, 0.5),
        "p90_latency_ms": _percentile(latencies, 0.9),
        "average_tokens_per_second": _mean(tokens_per_second),
        "openai_token_usage": {
            "prompt_tokens": sum(
                int(item.get("prompt_tokens") or 0) for item in completed
            ),
            "completion_tokens": sum(
                int(item.get("completion_tokens") or 0) for item in completed
            ),
            "total_tokens": sum(
                int(item.get("total_tokens") or 0) for item in completed
            ),
            "cost": None,
            "cost_note": "Provide explicit pricing config to calculate monetary cost.",
        },
        "assistant_phrase_flags": dict(
            Counter(
                flag
                for value in evaluated
                for flag in value.get("assistant_phrase_flags", [])
            )
        ),
    }


def _precision(predicted: list[dict[str, Any]], action: str) -> float:
    correct = sum(action in item.get("expected_actions", []) for item in predicted)
    return _rate(correct, len(predicted))


def _recall(expected: list[dict[str, Any]], action: str) -> float:
    correct = sum(
        (item.get("normalized_output") or {}).get("action") == action for item in expected
    )
    return _rate(correct, len(expected))


def _unsupported_fact_flags(scenario: BenchmarkScenario, text: str) -> list[str]:
    source = " ".join(
        (*scenario.incoming_messages, *scenario.known_facts, *scenario.required_facts)
    )
    flags: list[str] = []
    output_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", text))
    source_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", source))
    for number in sorted(output_numbers - source_numbers):
        flags.append(f"unsupported_number:{number}")
    if "hallucination_risk" in scenario.all_categories:
        for marker in ("руб", "₽", "$", "доллар", "дней", "недел", "гарантир"):
            if marker in text.lower() and marker not in source.lower():
                flags.append(f"unsupported_claim_marker:{marker}")
    return sorted(set(flags))


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 3) if values else None


def _percentile(values: list[int], quantile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1))
    return ordered[index]
