"""Deterministic representative-example selection for technical review."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from conversation_agent.local_slm.stage2_dataset import atomic_write_json, atomic_write_text
from conversation_agent.local_slm.stage2_runner import load_run_results

DIAGNOSTIC_GROUPS = (
    "wrong_action",
    "missed_no_reply",
    "missed_handoff",
    "wrong_reaction",
    "hallucination_or_unsupported_fact",
    "forbidden_claim",
    "repeated_incoming_question",
    "bubble_count_violation",
    "too_long",
    "too_short_or_empty",
    "assistant_like_phrase",
    "provider_failure",
    "best_qwen",
    "typical_qwen",
    "worst_qwen",
    "gpt_understands_but_too_long",
    "both_bad",
    "random_control",
)


def generate_diagnostic_pack(
    *,
    run_dir: Path,
    output_dir: Path,
    max_examples: int = 40,
    seed: int = 42,
) -> dict[str, Any]:
    if max_examples < 1:
        raise ValueError("max_examples must be positive")
    grouped = _group_results(load_run_results(run_dir))
    candidates: list[dict[str, Any]] = []
    group_available: Counter[str] = Counter()
    for scenario_id, providers in sorted(grouped.items()):
        pair = _build_pair(scenario_id, providers)
        if pair is None:
            continue
        pair["reasons"] = _classify(pair)
        pair["severity"] = _severity(pair)
        pair["selection_key"] = _selection_key(seed, scenario_id)
        candidates.append(pair)
        group_available.update(pair["reasons"])
    selected = _select_examples(candidates, max_examples=max_examples)
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_dir / "examples.json", selected)
    atomic_write_json(
        output_dir / "selection-summary.json",
        {
            "seed": seed,
            "max_examples": max_examples,
            "available_scenarios": len(candidates),
            "selected_scenarios": len(selected),
            "duplicate_scenario_ids": 0,
            "groups_requested": list(DIAGNOSTIC_GROUPS),
            "groups_available": dict(group_available),
            "groups_selected": dict(
                Counter(reason for item in selected for reason in item["reasons"])
            ),
            "categories_selected": dict(
                Counter(str(item.get("category", "unknown")) for item in selected)
            ),
        },
    )
    atomic_write_text(output_dir / "README.md", _readme())
    atomic_write_text(output_dir / "examples.md", _examples_markdown(selected))
    atomic_write_text(
        output_dir / "qwen-only.md",
        _provider_markdown(selected, key="qwen", title="Qwen examples"),
    )
    atomic_write_text(
        output_dir / "gpt-only.md",
        _provider_markdown(selected, key="gpt", title="GPT examples"),
    )
    atomic_write_text(output_dir / "comparison.md", _comparison_markdown(selected))
    atomic_write_text(output_dir / "user-notes-template.md", _user_notes_template())
    return {
        "output": str(output_dir),
        "selected_examples": len(selected),
        "available_scenarios": len(candidates),
        "groups_selected": sorted(
            {reason for item in selected for reason in item["reasons"]}
        ),
    }


def _group_results(
    results: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for item in results:
        scenario_id = str(item.get("scenario_id", ""))
        provider = str(item.get("pipeline") or item.get("provider") or "")
        if scenario_id and provider:
            grouped[scenario_id][provider] = item
    return grouped


def _build_pair(
    scenario_id: str,
    providers: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    qwen_name = _first_present(
        providers,
        (
            "gpt_policy_local_renderer",
            "local_qwen",
            "local_direct",
        ),
    )
    gpt_name = _first_present(
        providers,
        (
            "gpt_policy_openai_renderer",
            "openai_gpt4o_mini",
            "openai_direct",
        ),
    )
    if qwen_name is None and gpt_name is None:
        return None
    source = providers.get(qwen_name or "") or providers.get(gpt_name or "")
    assert source is not None
    scenario = dict(source.get("scenario", {}))
    return {
        "scenario_id": scenario_id,
        "category": source.get("category") or scenario.get("category"),
        "relationship": scenario.get("relationship", {}),
        "conversation": scenario.get("conversation", []),
        "known_facts": scenario.get("known_facts", []),
        "restrictions": scenario.get("forbidden_claims", []),
        "expected_actions": source.get("expected_actions")
        or scenario.get("expected_actions", []),
        "qwen": _candidate(providers.get(qwen_name or ""), qwen_name),
        "gpt": _candidate(providers.get(gpt_name or ""), gpt_name),
    }


def _candidate(
    result: dict[str, Any] | None,
    provider_name: str | None,
) -> dict[str, Any]:
    if result is None:
        return {
            "provider": provider_name,
            "missing": True,
            "action": None,
            "messages": [],
            "validation_flags": ["missing_result"],
            "automatic_evaluation": {},
            "provider_error": "missing_result",
        }
    output = result.get("normalized_output") or result.get("output") or {}
    validation = result.get("renderer_validation") or result.get("validation") or {}
    flags = list(validation.get("errors", []))
    evaluation = dict(result.get("automatic_evaluation", {}))
    flags.extend(evaluation.get("assistant_phrase_flags", []))
    flags.extend(evaluation.get("unsupported_fact_flags", []))
    flags.extend(f"forbidden:{item}" for item in evaluation.get("forbidden_claims", []))
    return {
        "provider": provider_name,
        "missing": False,
        "action": output.get("action"),
        "messages": output.get("messages", []),
        "reaction": output.get("reaction"),
        "handoff_required": output.get("handoff_required"),
        "validation_flags": sorted({str(item) for item in flags}),
        "automatic_evaluation": evaluation,
        "provider_error": (
            result.get("provider_error")
        ),
        "latency_ms": result.get("total_latency_ms") or result.get("latency_ms"),
    }


def _classify(pair: dict[str, Any]) -> list[str]:
    qwen = pair["qwen"]
    gpt = pair["gpt"]
    expected = set(pair.get("expected_actions", []))
    qeval = qwen["automatic_evaluation"]
    geval = gpt["automatic_evaluation"]
    reasons: list[str] = []
    if qwen.get("action") not in expected:
        reasons.append("wrong_action")
    if "no_reply" in expected and qwen.get("action") != "no_reply":
        reasons.append("missed_no_reply")
    if "handoff" in expected and qwen.get("action") != "handoff":
        reasons.append("missed_handoff")
    if "reaction" in expected and qwen.get("action") != "reaction":
        reasons.append("wrong_reaction")
    if qeval.get("unsupported_fact_flags"):
        reasons.append("hallucination_or_unsupported_fact")
    if qeval.get("forbidden_claims"):
        reasons.append("forbidden_claim")
    if "unnecessary_question_repetition" in qeval.get("assistant_phrase_flags", []):
        reasons.append("repeated_incoming_question")
    if qeval.get("bubble_count_compliance") is False:
        reasons.append("bubble_count_violation")
    qchars = int(qeval.get("character_count") or 0)
    if "excessive_length" in qeval.get("assistant_phrase_flags", []) or qchars > 300:
        reasons.append("too_long")
    if qwen.get("action") == "reply" and qchars < 12:
        reasons.append("too_short_or_empty")
    if qeval.get("assistant_phrase_flags"):
        reasons.append("assistant_like_phrase")
    if qwen.get("provider_error") or gpt.get("provider_error"):
        reasons.append("provider_failure")
    qwen_bad = _candidate_bad(qwen, expected)
    gpt_bad = _candidate_bad(gpt, expected)
    if not qwen_bad and not qwen["validation_flags"]:
        reasons.append("best_qwen")
    elif qwen_bad:
        reasons.append("worst_qwen")
    else:
        reasons.append("typical_qwen")
    if (
        gpt.get("action") in expected
        and (
            geval.get("bubble_count_compliance") is False
            or "excessive_length" in geval.get("assistant_phrase_flags", [])
            or int(geval.get("character_count") or 0) > 300
        )
    ):
        reasons.append("gpt_understands_but_too_long")
    if qwen_bad and gpt_bad:
        reasons.append("both_bad")
    if not qwen_bad and not gpt_bad and not qwen["validation_flags"] and not gpt["validation_flags"]:
        reasons.append("random_control")
    return list(dict.fromkeys(reasons))


def _candidate_bad(candidate: dict[str, Any], expected: set[str]) -> bool:
    evaluation = candidate.get("automatic_evaluation", {})
    return bool(
        candidate.get("provider_error")
        or candidate.get("action") not in expected
        or evaluation.get("bubble_count_compliance") is False
        or evaluation.get("forbidden_claims")
        or evaluation.get("unsupported_fact_flags")
    )


def _severity(pair: dict[str, Any]) -> int:
    weights = {
        "provider_failure": 12,
        "hallucination_or_unsupported_fact": 10,
        "forbidden_claim": 10,
        "wrong_action": 8,
        "missed_handoff": 8,
        "missed_no_reply": 7,
        "wrong_reaction": 7,
        "both_bad": 6,
        "repeated_incoming_question": 5,
        "bubble_count_violation": 4,
        "too_long": 3,
        "too_short_or_empty": 3,
        "assistant_like_phrase": 2,
    }
    return sum(weights.get(reason, 1) for reason in pair["reasons"])


def _select_examples(
    candidates: list[dict[str, Any]],
    *,
    max_examples: int,
) -> list[dict[str, Any]]:
    ordered = sorted(
        candidates,
        key=lambda item: (
            -int(item["severity"]),
            str(item["selection_key"]),
            str(item["scenario_id"]),
        ),
    )
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    category_counts: Counter[str] = Counter()
    for group in DIAGNOSTIC_GROUPS:
        matches = [
            item
            for item in ordered
            if group in item["reasons"] and item["scenario_id"] not in selected_ids
        ]
        if not matches:
            continue
        match = min(
            matches,
            key=lambda item: (
                category_counts[str(item.get("category", "unknown"))],
                -int(item["severity"]),
                str(item["selection_key"]),
            ),
        )
        selected.append(match)
        selected_ids.add(str(match["scenario_id"]))
        category_counts[str(match.get("category", "unknown"))] += 1
        if len(selected) >= max_examples:
            return _clean_selected(selected)
    for item in sorted(
        ordered,
        key=lambda value: (
            category_counts[str(value.get("category", "unknown"))],
            -int(value["severity"]),
            str(value["selection_key"]),
        ),
    ):
        if item["scenario_id"] in selected_ids:
            continue
        selected.append(item)
        selected_ids.add(str(item["scenario_id"]))
        category_counts[str(item.get("category", "unknown"))] += 1
        if len(selected) >= max_examples:
            break
    return _clean_selected(selected)


def _clean_selected(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            key: value
            for key, value in item.items()
            if key not in {"severity", "selection_key"}
        }
        for item in values
    ]


def _first_present(
    values: dict[str, dict[str, Any]],
    names: tuple[str, ...],
) -> str | None:
    return next((name for name in names if name in values), None)


def _selection_key(seed: int, scenario_id: str) -> str:
    return hashlib.sha256(f"{seed}:{scenario_id}".encode()).hexdigest()


def _readme() -> str:
    return (
        "# Automatic Diagnostic Pack\n\n"
        "Representative technical examples selected deterministically from saved "
        "automatic metrics. This is not a blind review and not a human quality score.\n"
    )


def _examples_markdown(values: list[dict[str, Any]]) -> str:
    lines = ["# Diagnostic Examples", ""]
    for item in values:
        lines.extend(_example_lines(item))
    return "\n".join(lines).rstrip() + "\n"


def _example_lines(item: dict[str, Any]) -> list[str]:
    return [
        f"## {item['scenario_id']}",
        "",
        f"- Category: `{item.get('category')}`",
        f"- Relationship: `{_inline_json(item.get('relationship'))}`",
        f"- Conversation: `{_inline_json(item.get('conversation'))}`",
        f"- Known facts: `{_inline_json(item.get('known_facts'))}`",
        f"- Restrictions: `{_inline_json(item.get('restrictions'))}`",
        f"- Expected actions: `{_inline_json(item.get('expected_actions'))}`",
        f"- Reasons: `{', '.join(item.get('reasons', []))}`",
        "",
        "### Qwen",
        f"- Action: `{item['qwen'].get('action')}`",
        f"- Messages: `{_inline_json(item['qwen'].get('messages'))}`",
        f"- Validation flags: `{_inline_json(item['qwen'].get('validation_flags'))}`",
        "",
        "### GPT",
        f"- Action: `{item['gpt'].get('action')}`",
        f"- Messages: `{_inline_json(item['gpt'].get('messages'))}`",
        f"- Validation flags: `{_inline_json(item['gpt'].get('validation_flags'))}`",
        "",
    ]


def _provider_markdown(
    values: list[dict[str, Any]],
    *,
    key: str,
    title: str,
) -> str:
    lines = [f"# {title}", ""]
    for item in values:
        candidate = item[key]
        lines.extend(
            [
                f"## {item['scenario_id']}",
                f"- Action: `{candidate.get('action')}`",
                f"- Messages: `{_inline_json(candidate.get('messages'))}`",
                f"- Flags: `{_inline_json(candidate.get('validation_flags'))}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _comparison_markdown(values: list[dict[str, Any]]) -> str:
    lines = ["# Qwen and GPT Comparison", ""]
    for item in values:
        lines.extend(
            [
                f"## {item['scenario_id']} — {', '.join(item.get('reasons', []))}",
                f"- Qwen: `{_inline_json(item['qwen'].get('messages'))}`",
                f"- GPT: `{_inline_json(item['gpt'].get('messages'))}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _user_notes_template() -> str:
    return """# Общий вывод

## Что делает Qwen плохо

## Что делает Qwen нормально

## Что делает GPT плохо

## Какие ответы выглядят естественно

## Какие ошибки критичны

## Итоговое решение
"""


def _inline_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace(
        "`",
        "'",
    )
