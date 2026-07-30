"""Stage 2 Markdown, JSON and CSV report generation."""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from conversation_agent.local_slm.stage2_dataset import atomic_write_json, atomic_write_text
from conversation_agent.local_slm.stage2_metrics import aggregate_metrics
from conversation_agent.local_slm.stage2_review import build_blind_pairs
from conversation_agent.local_slm.stage2_runner import load_run_results


def generate_stage2_report(
    *,
    run_dir: Path,
    reviews_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    run_meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    results = load_run_results(run_dir)
    by_provider: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in results:
        by_provider[str(item.get("provider", "unknown"))].append(item)
    automatic = {
        provider: aggregate_metrics(items)
        for provider, items in sorted(by_provider.items())
    }
    seed = int(run_meta.get("generation_config", {}).get("seed", 42))
    blind_pairs = build_blind_pairs(run_dir, seed=seed)
    pair_mapping = {
        pair.pair_id: {
            "A": pair.candidate_a_provider,
            "B": pair.candidate_b_provider,
        }
        for pair in blind_pairs
    }
    reviews = _load_reviews(reviews_dir)
    human = _human_summary(reviews, pair_mapping)
    review_complete = bool(blind_pairs) and len(reviews) >= len(blind_pairs)
    stage3_decision = _stage3_decision(
        automatic=automatic,
        human=human,
        review_complete=review_complete,
    )
    summary = {
        "benchmark_name": run_meta.get("benchmark_name"),
        "benchmark_version": run_meta.get("benchmark_version"),
        "benchmark_fingerprint": run_meta.get("benchmark_fingerprint"),
        "source_commit": run_meta.get("source_commit"),
        "comparison_mode": run_meta.get("comparison_mode"),
        "config_fingerprint": run_meta.get("config_fingerprint"),
        "run_fingerprint": run_meta.get("run_fingerprint"),
        "provider_status": run_meta.get("provider_status", {}),
        "automatic_metrics": automatic,
        "human_review": {
            **human,
            "available_pairs": len(blind_pairs),
            "complete": review_complete,
            "status": (
                "complete" if review_complete else "Human evaluation incomplete."
            ),
        },
        "stage3_decision": stage3_decision,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_dir / "summary.json", summary)
    _write_scenario_results(output_dir / "scenario_results.csv", results)
    _write_category_results(output_dir / "category_results.csv", results)
    _write_latency_results(output_dir / "latency_results.csv", results)
    _write_human_preferences(
        output_dir / "human_preferences.csv",
        reviews,
        pair_mapping,
    )
    atomic_write_text(
        output_dir / "failure_examples.md",
        _failure_examples_markdown(results),
    )
    atomic_write_text(
        output_dir / "report.md",
        _report_markdown(summary),
    )
    return {
        "output": str(output_dir),
        "review_complete": review_complete,
        "reviewed_pairs": len(reviews),
        "available_pairs": len(blind_pairs),
        "stage3_decision": stage3_decision,
    }


def _load_reviews(reviews_dir: Path) -> list[dict[str, Any]]:
    if not reviews_dir.is_dir():
        return []
    values: list[dict[str, Any]] = []
    for path in sorted(reviews_dir.rglob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            values.append(value)
    return values


def _human_summary(
    reviews: list[dict[str, Any]],
    mapping: dict[str, dict[str, str]],
) -> dict[str, Any]:
    wins: Counter[str] = Counter()
    dimensions: dict[str, list[float]] = defaultdict(list)
    correct_action: Counter[str] = Counter()
    hallucinations: Counter[str] = Counter()
    edits: Counter[str] = Counter()
    for review in reviews:
        pair_mapping = mapping.get(str(review.get("pair_id")), {})
        winner = str(review.get("winner", ""))
        if winner in {"A", "B"}:
            provider = pair_mapping.get(winner)
            if provider:
                wins[provider] += 1
        elif winner:
            wins[winner] += 1
        for candidate in ("A", "B"):
            provider = pair_mapping.get(candidate)
            ratings = review.get(f"candidate_{candidate}", {})
            if not provider or not isinstance(ratings, dict):
                continue
            for dimension, value in ratings.items():
                if isinstance(value, int) and 1 <= value <= 5:
                    dimensions[f"{provider}.{dimension}"].append(float(value))
            correct_action[f"{provider}.{ratings.get('correct_action')}"] += 1
            hallucinations[f"{provider}.{ratings.get('hallucination')}"] += 1
            edits[f"{provider}.{ratings.get('needs_human_edit')}"] += 1
    return {
        "reviewed_pairs": len(reviews),
        "wins": dict(wins),
        "average_ratings": {
            key: round(statistics.fmean(values), 3)
            for key, values in sorted(dimensions.items())
            if values
        },
        "correct_action": dict(correct_action),
        "hallucinations": dict(hallucinations),
        "human_edit": dict(edits),
    }


def _stage3_decision(
    *,
    automatic: dict[str, dict[str, Any]],
    human: dict[str, Any],
    review_complete: bool,
) -> str:
    local = automatic.get("local_qwen", {})
    if human.get("reviewed_pairs", 0) < 30:
        return "PENDING: fewer than 30 scenarios have human ratings"
    if not review_complete:
        return "PENDING: Human evaluation incomplete"
    completion_rate = (
        local.get("completed_scenarios", 0) / local.get("total_scenarios", 1)
        if local.get("total_scenarios")
        else 0.0
    )
    if (
        local.get("schema_validity_rate", 0.0) >= 0.95
        and completion_rate >= 0.95
        and local.get("unsupported_fact_flags", 1) == 0
    ):
        return (
            "READY_FOR_TRAINING_DATA_STAGE pending final human judgment "
            "that Russian dialogue understanding is adequate"
        )
    return "NOT_READY based on current completion, schema, or factual-discipline metrics"


def _write_scenario_results(path: Path, results: list[dict[str, Any]]) -> None:
    fields = [
        "scenario_id",
        "category",
        "provider",
        "model_id",
        "comparison_mode",
        "action",
        "expected_actions",
        "valid",
        "expected_action_match",
        "latency_ms",
        "prompt_tokens",
        "completion_tokens",
        "retry_count",
        "provider_error",
    ]
    rows = []
    for item in results:
        normalized = item.get("normalized_output") or {}
        evaluation = item.get("automatic_evaluation") or {}
        rows.append(
            {
                "scenario_id": item.get("scenario_id"),
                "category": item.get("category"),
                "provider": item.get("provider"),
                "model_id": item.get("model_id"),
                "comparison_mode": item.get("comparison_mode"),
                "action": normalized.get("action"),
                "expected_actions": "|".join(item.get("expected_actions", [])),
                "valid": item.get("validation", {}).get("valid"),
                "expected_action_match": evaluation.get("expected_action_match"),
                "latency_ms": item.get("latency_ms"),
                "prompt_tokens": item.get("prompt_tokens"),
                "completion_tokens": item.get("completion_tokens"),
                "retry_count": item.get("retry_count"),
                "provider_error": item.get("provider_error"),
            }
        )
    _write_csv(path, fields, rows)


def _write_category_results(path: Path, results: list[dict[str, Any]]) -> None:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in results:
        categories = {str(item.get("category", "")), *item.get("tags", [])}
        for category in categories:
            if category:
                grouped[(category, str(item.get("provider")))].append(item)
    rows = []
    for (category, provider), items in sorted(grouped.items()):
        metrics = aggregate_metrics(items)
        rows.append(
            {
                "category": category,
                "provider": provider,
                "completed": metrics["completed_scenarios"],
                "failures": metrics["provider_failures"],
                "schema_validity_rate": metrics["schema_validity_rate"],
                "expected_action_match": metrics["expected_action_match"],
                "median_latency_ms": metrics["median_latency_ms"],
            }
        )
    _write_csv(
        path,
        [
            "category",
            "provider",
            "completed",
            "failures",
            "schema_validity_rate",
            "expected_action_match",
            "median_latency_ms",
        ],
        rows,
    )


def _write_latency_results(path: Path, results: list[dict[str, Any]]) -> None:
    rows = [
        {
            "scenario_id": item.get("scenario_id"),
            "provider": item.get("provider"),
            "latency_ms": item.get("latency_ms"),
            "completion_tokens": item.get("completion_tokens"),
            "tokens_per_second": item.get("tokens_per_second"),
        }
        for item in results
        if not item.get("provider_error")
    ]
    _write_csv(
        path,
        [
            "scenario_id",
            "provider",
            "latency_ms",
            "completion_tokens",
            "tokens_per_second",
        ],
        rows,
    )


def _write_human_preferences(
    path: Path,
    reviews: list[dict[str, Any]],
    mapping: dict[str, dict[str, str]],
) -> None:
    rows = []
    for review in reviews:
        pair_mapping = mapping.get(str(review.get("pair_id")), {})
        winner = str(review.get("winner", ""))
        rows.append(
            {
                "pair_id": review.get("pair_id"),
                "reviewer": review.get("reviewer"),
                "winner_blind": winner,
                "winner_provider": pair_mapping.get(winner, winner),
                "candidate_A_provider": pair_mapping.get("A"),
                "candidate_B_provider": pair_mapping.get("B"),
                "note": review.get("note"),
            }
        )
    _write_csv(
        path,
        [
            "pair_id",
            "reviewer",
            "winner_blind",
            "winner_provider",
            "candidate_A_provider",
            "candidate_B_provider",
            "note",
        ],
        rows,
    )


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _failure_examples_markdown(results: list[dict[str, Any]]) -> str:
    lines = ["# Failure Examples", ""]
    failures = [
        item
        for item in results
        if item.get("provider_error")
        or not item.get("validation", {}).get("valid", False)
        or item.get("automatic_evaluation", {}).get("forbidden_claims")
        or item.get("automatic_evaluation", {}).get("unsupported_fact_flags")
    ]
    if not failures:
        return "# Failure Examples\n\nNo automatic failure examples recorded.\n"
    for item in failures[:50]:
        lines.extend(
            [
                f"## {item.get('scenario_id')} / {item.get('provider')}",
                "",
                f"- Error: {item.get('provider_error')}",
                f"- Validation: `{json.dumps(item.get('validation'), ensure_ascii=False)}`",
                (
                    "- Automatic flags: `"
                    + json.dumps(
                        item.get("automatic_evaluation"),
                        ensure_ascii=False,
                    )
                    + "`"
                ),
                "",
                "```json",
                json.dumps(item.get("normalized_output"), ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def _report_markdown(summary: dict[str, Any]) -> str:
    human = summary["human_review"]
    automatic = summary["automatic_metrics"]
    review_status = human["status"]
    wins = human.get("wins", {}) if human.get("complete") else {}
    winner_note = (
        json.dumps(wins, ensure_ascii=False, sort_keys=True)
        if human.get("complete")
        else "No winner is declared before human review is complete."
    )
    sections = [
        ("Executive Summary", f"{review_status} {winner_note}"),
        (
            "What Was Compared",
            "Real local Qwen3-0.6B Q8_0 and GPT-4o-mini structured pipelines.",
        ),
        ("Dataset Fingerprint", str(summary.get("benchmark_fingerprint"))),
        ("Source Commit", str(summary.get("source_commit"))),
        (
            "Configurations",
            f"Mode: `{summary.get('comparison_mode')}`; config: `{summary.get('config_fingerprint')}`.",
        ),
        (
            "Automatic Metrics",
            "```json\n"
            + json.dumps(automatic, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n```",
        ),
        ("Human Review Status", review_status),
        ("Local / GPT / Tie Wins", winner_note),
        ("Naturalness", _human_dimension(human, "naturalness")),
        ("Telegram-likeness", _human_dimension(human, "telegram_likeness")),
        ("Correct Action", json.dumps(human.get("correct_action", {}), ensure_ascii=False)),
        ("Hallucinations", json.dumps(human.get("hallucinations", {}), ensure_ascii=False)),
        ("Human Edit Rate", json.dumps(human.get("human_edit", {}), ensure_ascii=False)),
        ("Latency", _latency_summary(automatic)),
        (
            "Category Breakdown",
            "See `category_results.csv` for provider metrics by overlapping category.",
        ),
        ("Qwen Strengths", "Pending completed blind review."),
        ("Qwen Weaknesses", "Pending completed blind review."),
        ("GPT Strengths", "Pending completed blind review."),
        ("GPT Weaknesses", "Pending completed blind review."),
        ("Typical Errors", "See `failure_examples.md`."),
        (
            "Research Limitations",
            (
                "Remote GPT output is not fully deterministic. Automatic detectors create "
                "flags, not quality judgments. Human ratings can vary by reviewer."
            ),
        ),
        ("Stage 3 Decision", str(summary.get("stage3_decision"))),
    ]
    lines = ["# Local Telegram SLM Stage 2 Report", ""]
    for title, body in sections:
        lines.extend([f"## {title}", "", body, ""])
    return "\n".join(lines)


def _human_dimension(human: dict[str, Any], dimension: str) -> str:
    values = {
        key: value
        for key, value in human.get("average_ratings", {}).items()
        if key.endswith(f".{dimension}")
    }
    return json.dumps(values, ensure_ascii=False, sort_keys=True) or "Pending."


def _latency_summary(automatic: dict[str, dict[str, Any]]) -> str:
    value = {
        provider: {
            "median_ms": metrics.get("median_latency_ms"),
            "p90_ms": metrics.get("p90_latency_ms"),
        }
        for provider, metrics in automatic.items()
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
