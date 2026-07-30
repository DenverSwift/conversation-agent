"""Automatic contract and pipeline metrics for Stage 2.5."""

from __future__ import annotations

import math
import statistics
from collections import Counter
from typing import Any

from conversation_agent.local_slm.stage2_metrics import aggregate_metrics


def aggregate_stage25_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    base = aggregate_metrics(results)
    completed = [item for item in results if not _has_error(item)]
    contract_records = [
        item for item in results if item.get("contract_validation") is not None
    ]
    renderer_records = [
        item for item in results if item.get("renderer_validation") is not None
    ]
    compliance = [
        item.get("renderer_validation", {}).get("contract_compliance", {})
        for item in renderer_records
        if isinstance(item.get("renderer_validation"), dict)
    ]
    policy_latencies = [
        int(item["policy_latency_ms"])
        for item in completed
        if isinstance(item.get("policy_latency_ms"), int)
    ]
    renderer_latencies = [
        int(item["renderer_latency_ms"])
        for item in completed
        if isinstance(item.get("renderer_latency_ms"), int)
    ]
    total_latencies = [
        int(item["total_latency_ms"])
        for item in completed
        if isinstance(item.get("total_latency_ms"), int)
    ]
    evaluations = [item.get("automatic_evaluation", {}) for item in completed]
    base.update(
        {
            "completion_rate": _rate(len(completed), len(results)),
            "contract_validity": _rate(
                sum(
                    bool(item.get("contract_validation", {}).get("valid"))
                    for item in contract_records
                ),
                len(contract_records),
            )
            if contract_records
            else None,
            "renderer_validity": _rate(
                sum(
                    bool(item.get("renderer_validation", {}).get("valid"))
                    for item in renderer_records
                ),
                len(renderer_records),
            )
            if renderer_records
            else base.get("schema_validity_rate"),
            "total_character_compliance": _compliance_rate(
                compliance,
                "total_characters",
            ),
            "per_bubble_character_compliance": _compliance_rate(
                compliance,
                "characters_per_bubble",
            ),
            "question_count_compliance": _compliance_rate(
                compliance,
                "question_count",
            ),
            "repeated_question_rate": _rate(
                sum(
                    "unnecessary_question_repetition"
                    in value.get("assistant_phrase_flags", [])
                    for value in evaluations
                ),
                len(evaluations),
            ),
            "assistant_phrase_flag_rate": base.get("forbidden_phrase_flag_rate"),
            "human_edit_proxy_rate": _rate(
                sum(_human_edit_proxy(value) for value in evaluations),
                len(evaluations),
            ),
            "retry_rate": _rate(
                sum(int(item.get("renderer_retry_count", 0)) > 0 for item in completed),
                len(completed),
            ),
            "median_policy_latency_ms": _percentile(policy_latencies, 0.5),
            "p90_policy_latency_ms": _percentile(policy_latencies, 0.9),
            "median_renderer_latency_ms": _percentile(renderer_latencies, 0.5),
            "p90_renderer_latency_ms": _percentile(renderer_latencies, 0.9),
            "median_total_latency_ms": _percentile(total_latencies, 0.5),
            "p90_total_latency_ms": _percentile(total_latencies, 0.9),
            "policy_token_usage": _usage(completed, "policy_usage"),
            "renderer_token_usage": _usage(completed, "renderer_usage"),
            "gpu_vram_used_mib": _maximum_numeric(
                completed,
                "gpu_vram_used_mib",
            ),
            "gpu_vram_delta_mib": _maximum_numeric(
                completed,
                "gpu_vram_delta_mib",
            ),
            "gpu_offloaded_layers": _maximum_numeric(
                completed,
                "gpu_offloaded_layers",
            ),
            "contract_failure_types": dict(
                Counter(
                    error
                    for item in results
                    for error in (item.get("contract_validation") or {}).get(
                        "errors",
                        [],
                    )
                )
            ),
            "renderer_failure_types": dict(
                Counter(
                    error
                    for item in results
                    for error in (item.get("renderer_validation") or {}).get(
                        "errors",
                        [],
                    )
                )
            ),
        }
    )
    return base


def _has_error(item: dict[str, Any]) -> bool:
    return bool(
        item.get("provider_error")
        or item.get("contract_error")
    )


def _compliance_rate(values: list[dict[str, Any]], key: str) -> float | None:
    present = [value for value in values if key in value]
    if not present:
        return None
    return _rate(sum(bool(value.get(key)) for value in present), len(present))


def _human_edit_proxy(value: dict[str, Any]) -> bool:
    return bool(
        value.get("assistant_phrase_flags")
        or value.get("unsupported_fact_flags")
        or value.get("forbidden_claims")
        or value.get("empty_reply")
        or value.get("repeated_bubble")
    )


def _usage(values: list[dict[str, Any]], key: str) -> dict[str, int]:
    return {
        token_key: sum(
            int(item.get(key, {}).get(token_key) or 0) for item in values
        )
        for token_key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }


def _maximum_numeric(
    values: list[dict[str, Any]],
    key: str,
) -> float | int | None:
    present = [
        value[key]
        for value in values
        if isinstance(value.get(key), (int, float))
        and not isinstance(value.get(key), bool)
    ]
    return max(present) if present else None


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _percentile(values: list[int], quantile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1))
    return ordered[index]


def mean_or_none(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 3) if values else None
