"""Stage 2.5 contract-pipeline comparison report."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from conversation_agent.local_slm.stage2_dataset import atomic_write_json, atomic_write_text
from conversation_agent.local_slm.stage2_runner import load_run_results
from conversation_agent.local_slm.stage25_diagnostics import generate_diagnostic_pack
from conversation_agent.local_slm.stage25_metrics import aggregate_stage25_metrics


def generate_stage25_report(
    *,
    run_dir: Path,
    baseline_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    run_meta = _load_json(run_dir / "run.json")
    results = load_run_results(run_dir)
    pipelines = sorted({str(item.get("pipeline", "unknown")) for item in results})
    metrics = {
        pipeline: aggregate_stage25_metrics(
            [item for item in results if item.get("pipeline") == pipeline]
        )
        for pipeline in pipelines
    }
    baseline_summary = _load_json(baseline_dir / "summary.json")
    diagnostic = generate_diagnostic_pack(
        run_dir=run_dir,
        output_dir=output_dir / "diagnostic-pack",
        max_examples=40,
        seed=int(run_meta.get("seed", 42)),
    )
    recommendation = _technical_recommendation(metrics)
    summary = {
        "benchmark_fingerprint": run_meta.get("benchmark_fingerprint"),
        "source_commit": run_meta.get("source_commit"),
        "run_fingerprint": run_meta.get("run_fingerprint"),
        "pipeline_status": run_meta.get("pipeline_status", {}),
        "gpu_status": run_meta.get("gpu_status", {}),
        "pipeline_metrics": metrics,
        "baseline_metrics": baseline_summary.get("metrics", {}),
        "diagnostic_pack": diagnostic,
        "technical_recommendation": recommendation,
        "user_qualitative_review_required": True,
        "production_ready": False,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_dir / "summary.json", summary)
    _write_pipeline_metrics(output_dir / "pipeline_metrics.csv", metrics)
    _write_failures(
        output_dir / "contract_failures.csv",
        results,
        error_key="contract_error",
    )
    _write_failures(
        output_dir / "renderer_failures.csv",
        results,
        error_key="renderer_error",
    )
    _write_metric_subset(
        output_dir / "action_metrics.csv",
        metrics,
        (
            "expected_action_match",
            "no_reply_precision",
            "no_reply_recall",
            "handoff_precision",
            "handoff_recall",
            "reaction_action_match",
        ),
    )
    _write_metric_subset(
        output_dir / "length_metrics.csv",
        metrics,
        (
            "bubble_count_compliance",
            "total_character_compliance",
            "per_bubble_character_compliance",
            "question_count_compliance",
            "average_character_count",
            "average_message_count",
        ),
    )
    _write_metric_subset(
        output_dir / "latency_metrics.csv",
        metrics,
        (
            "median_policy_latency_ms",
            "p90_policy_latency_ms",
            "median_renderer_latency_ms",
            "p90_renderer_latency_ms",
            "median_total_latency_ms",
            "p90_total_latency_ms",
            "average_tokens_per_second",
        ),
    )
    comparison_source = output_dir / "diagnostic-pack" / "comparison.md"
    atomic_write_text(
        output_dir / "comparison_examples.md",
        comparison_source.read_text(encoding="utf-8"),
    )
    atomic_write_text(output_dir / "report.md", _report_markdown(summary))
    return {
        "output": str(output_dir),
        "pipelines": pipelines,
        "technical_recommendation": recommendation,
        "diagnostic_pack": diagnostic["output"],
        "user_qualitative_review_required": True,
    }


def _technical_recommendation(metrics: dict[str, dict[str, Any]]) -> str:
    local = metrics.get("gpt_policy_local_renderer", {})
    openai = metrics.get("gpt_policy_openai_renderer", {})
    policy_action = max(
        float(local.get("expected_action_match") or 0.0),
        float(openai.get("expected_action_match") or 0.0),
    )
    contract_validity = max(
        float(local.get("contract_validity") or 0.0),
        float(openai.get("contract_validity") or 0.0),
    )
    if contract_validity < 0.9 or policy_action < 0.8:
        return "ARCHITECTURE_NOT_WORKING"
    local_validity = float(local.get("renderer_validity") or 0.0)
    local_edit_proxy = float(local.get("human_edit_proxy_rate") or 1.0)
    openai_validity = float(openai.get("renderer_validity") or 0.0)
    openai_length = float(openai.get("total_character_compliance") or 0.0)
    if local_validity >= 0.9 and local_edit_proxy <= 0.35:
        return "READY_FOR_TRAINING_DATA_STAGE"
    if openai_validity >= 0.9 and openai_length >= 0.9 and local_validity < 0.8:
        return "OPENAI_RENDERER_ONLY_FOR_NOW"
    return "TEST_LARGER_LOCAL_MODEL"


def _write_pipeline_metrics(
    path: Path,
    metrics: dict[str, dict[str, Any]],
) -> None:
    rows = [
        {
            "pipeline": pipeline,
            **{
                key: json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else value
                for key, value in values.items()
            },
        }
        for pipeline, values in metrics.items()
    ]
    fields = sorted({key for row in rows for key in row}) if rows else ["pipeline"]
    _write_csv(path, fields, rows)


def _write_failures(
    path: Path,
    results: list[dict[str, Any]],
    *,
    error_key: str,
) -> None:
    rows = [
        {
            "scenario_id": item.get("scenario_id"),
            "pipeline": item.get("pipeline"),
            "category": item.get("category"),
            "error": item.get(error_key),
        }
        for item in results
        if item.get(error_key)
    ]
    _write_csv(path, ["scenario_id", "pipeline", "category", "error"], rows)


def _write_metric_subset(
    path: Path,
    metrics: dict[str, dict[str, Any]],
    names: tuple[str, ...],
) -> None:
    rows = [
        {"pipeline": pipeline, **{name: values.get(name) for name in names}}
        for pipeline, values in metrics.items()
    ]
    _write_csv(path, ["pipeline", *names], rows)


def _write_csv(
    path: Path,
    fields: list[str],
    rows: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _report_markdown(summary: dict[str, Any]) -> str:
    metrics = summary["pipeline_metrics"]
    local = metrics.get("gpt_policy_local_renderer", {})
    openai = metrics.get("gpt_policy_openai_renderer", {})
    baseline = summary.get("baseline_metrics", {})
    sections = [
        (
            "Did ResponseContract improve action correctness?",
            _comparison_value(baseline, metrics, "expected_action_match"),
        ),
        (
            "Did it improve no_reply?",
            _comparison_value(baseline, metrics, "no_reply_recall"),
        ),
        (
            "Did it improve handoff?",
            _comparison_value(baseline, metrics, "handoff_recall"),
        ),
        (
            "Did it improve reactions?",
            _comparison_value(baseline, metrics, "reaction_action_match"),
        ),
        (
            "Did GPT produce less unnecessary text?",
            (
                f"OpenAI renderer average characters: "
                f"`{openai.get('average_character_count')}`; "
                f"character compliance: `{openai.get('total_character_compliance')}`."
            ),
        ),
        (
            "Did bubble compliance improve?",
            _comparison_value(baseline, metrics, "bubble_count_compliance"),
        ),
        (
            "Did incoming-question repetition decrease?",
            _comparison_value(baseline, metrics, "repeated_question_rate"),
        ),
        (
            "Did unsupported facts decrease?",
            _comparison_value(baseline, metrics, "unsupported_fact_flags"),
        ),
        (
            "Can Qwen render after GPT makes the decision?",
            (
                f"Local renderer validity: `{local.get('renderer_validity')}`; "
                f"action match: `{local.get('expected_action_match')}`."
            ),
        ),
        (
            "Remaining local renderer errors",
            json.dumps(local.get("renderer_failure_types", {}), ensure_ascii=False),
        ),
        (
            "Remaining OpenAI renderer errors",
            json.dumps(openai.get("renderer_failure_types", {}), ensure_ascii=False),
        ),
        (
            "GPU latency",
            (
                f"Local median/p90 total latency: "
                f"`{local.get('median_total_latency_ms')}` / "
                f"`{local.get('p90_total_latency_ms')}` ms; GPU: "
                f"`{json.dumps(summary.get('gpu_status'), ensure_ascii=False)}`."
            ),
        ),
        (
            "Should a larger local model be tested?",
            f"Technical recommendation: `{summary['technical_recommendation']}`.",
        ),
        (
            "Should training-data work begin?",
            (
                "A final model choice requires a short qualitative review of the "
                "diagnostic pack; no formal per-scenario rating gate is required."
            ),
        ),
    ]
    lines = [
        "# Local Telegram SLM Stage 2.5 Report",
        "",
        "> This experiment is not production-ready and does not enable autopilot.",
        "",
    ]
    for title, body in sections:
        lines.extend([f"## {title}", "", str(body), ""])
    return "\n".join(lines)


def _comparison_value(
    baseline: dict[str, Any],
    current: dict[str, dict[str, Any]],
    metric: str,
) -> str:
    value = {
        "local_direct": baseline.get("local_qwen", {}).get(metric),
        "openai_direct": baseline.get("openai_gpt4o_mini", {}).get(metric),
        "gpt_policy_local_renderer": current.get(
            "gpt_policy_local_renderer",
            {},
        ).get(metric),
        "gpt_policy_openai_renderer": current.get(
            "gpt_policy_openai_renderer",
            {},
        ).get(metric),
    }
    return f"`{json.dumps(value, ensure_ascii=False, sort_keys=True)}`"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value
