"""Stage 3D authoritative-human pilot selection and scoped PII review."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from conversation_agent.local_slm.stage2_dataset import stable_fingerprint
from conversation_agent.local_slm.telegram_curation import (
    AUTHORITATIVE_HUMAN,
    TelegramCurationError,
    reconciliation_fingerprint,
)
from conversation_agent.local_slm.telegram_import import select_review_sample
from conversation_agent.local_slm.telegram_privacy import scan_text


def recommend_pii_actions(
    *,
    review: Path,
    reconciliation: Path,
    output: Path,
) -> dict[str, Any]:
    _validate_reconciliation(reconciliation)
    review_rows = _read_csv(review)
    records = {
        str(item["record_id"]): item
        for item in _read_jsonl(reconciliation / "pii-records.jsonl")
    }
    episodes = {
        str(item["example_id"]): item
        for item in _read_jsonl(reconciliation / "episodes.reconciled.jsonl")
    }
    rows: list[dict[str, Any]] = []
    scope_counts: Counter[str] = Counter()
    for source in review_rows:
        record_id = str(source.get("record_id", ""))
        record = records.get(record_id, {})
        scopes = _record_scopes(record, episodes)
        scope = _primary_scope(scopes)
        scope_counts[scope] += 1
        pii_type = str(source.get("pii_type") or record.get("pii_type", "ambiguous"))
        rows.append(
            {
                "record_id": record_id,
                "pii_type": pii_type,
                "recommended_action": recommended_pii_action(pii_type),
                "scope": scope,
                "masked_fragment": f"[masked:{pii_type}]",
                "meaning_loss": _meaning_loss(pii_type),
                "authoritative_human": scope == "authoritative_human",
            }
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(
        output,
        (
            "record_id",
            "pii_type",
            "recommended_action",
            "scope",
            "masked_fragment",
            "meaning_loss",
            "authoritative_human",
        ),
        rows,
    )
    authoritative_rows = [
        {
            "record_id": item["record_id"],
            "pii_type": item["pii_type"],
            "recommended_action": item["recommended_action"],
            "approved_action": "",
            "notes": "",
        }
        for item in rows
        if item["authoritative_human"]
    ]
    _write_csv(
        output.parent / "final-pii-decisions.csv",
        (
            "record_id",
            "pii_type",
            "recommended_action",
            "approved_action",
            "notes",
        ),
        authoritative_rows,
    )
    (output.parent / "pii-review.md").write_text(
        _pii_review_markdown(rows),
        encoding="utf-8",
    )
    return {
        "output": str(output),
        "total_pii_findings": len(rows),
        "scope_counts": dict(sorted(scope_counts.items())),
        "authoritative_decisions_required": len(authoritative_rows),
        "recommendations_applied": False,
    }


def recommended_pii_action(pii_type: str) -> str:
    normalized = pii_type.casefold()
    if normalized in {"phone", "email", "secret_url"}:
        return "redact"
    if normalized in {
        "numeric_id",
        "telegram_username",
        "private_name",
        "real_person_name",
    }:
        return "replace_with_alias"
    if normalized in {
        "payment_card",
        "bank_details",
        "api_key",
        "bot_token",
        "session_string",
        "password",
        "passport",
        "address",
        "sensitive_self_harm",
    }:
        return "exclude"
    if normalized == "public_url":
        return "keep"
    return "manual_review"


def select_authoritative_pilot(
    *,
    reconciliation: Path,
    output: Path,
    authoritative_only: bool,
    min_examples: int,
    max_examples: int,
) -> dict[str, Any]:
    reconciliation_fp = _validate_reconciliation(reconciliation)
    if not authoritative_only:
        raise TelegramCurationError("--authoritative-only is required")
    if min_examples < 1 or max_examples < min_examples or max_examples > 82:
        raise TelegramCurationError("invalid pilot example limits")
    episodes = _read_jsonl(reconciliation / "episodes.reconciled.jsonl")
    pii_records = _read_jsonl(reconciliation / "pii-records.jsonl")
    pii_by_episode = {
        str(episode_id)
        for item in pii_records
        for episode_id in item.get("episode_ids", [])
    }
    authoritative = [
        item
        for item in episodes
        if item.get("stage3c", {}).get("classification") in AUTHORITATIVE_HUMAN
        and item.get("stage3c", {}).get("authoritative") is True
    ]
    safe = [
        item for item in authoritative if str(item["example_id"]) not in pii_by_episode
    ]
    if len(safe) < min_examples:
        status = "INSUFFICIENT_AUTHORITATIVE_DATA"
        selected: list[dict[str, Any]] = []
    else:
        selected = select_review_sample(safe, limit=min(max_examples, len(safe)))
        status = "READY_FOR_PII_REVIEW"
    privacy_findings = sum(
        len(scan_text(str(text)))
        for item in selected
        for section in ("incoming", "human_target")
        for text in item.get(section, {}).get("messages", [])
    )
    if privacy_findings:
        selected = []
        status = "PRIVACY_BLOCKED"
    enriched = [
        _selection_payload(
            item,
            reconciliation_fingerprint_value=reconciliation_fp,
        )
        for item in selected
    ]
    diversity = _diversity_report(enriched)
    diversity["source_reconciliation_fingerprint"] = reconciliation_fp
    output.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output / "selected.preview.jsonl", enriched)
    _write_json(output / "diversity-report.json", diversity)
    selection_fp = stable_fingerprint(
        {
            "source_reconciliation": reconciliation_fp,
            "selected": enriched,
            "diversity": diversity,
            "authoritative_only": True,
        }
    )
    (output / "selection-fingerprint.txt").write_text(
        selection_fp + "\n",
        encoding="utf-8",
    )
    (output / "selected-review.md").write_text(
        _selected_review_markdown(enriched),
        encoding="utf-8",
    )
    confirmation = (
        "python -m conversation_agent dataset telegram-confirm-curated "
        '--preview ".runtime\\private-imports\\telegram\\friend-pilot" '
        '--reconciliation ".runtime\\private-imports\\telegram\\'
        'friend-pilot-stage3c\\reconciliation" '
        f'--pilot-selection "{output}" '
        f'--pii-decisions "{output.parent / "final-pii-decisions.csv"}" '
        f"--fingerprint {selection_fp} --consent-confirmed "
        "--authoritative-only --max-examples 82"
    )
    (output.parent / "confirmation-command.txt").write_text(
        confirmation + "\n",
        encoding="utf-8",
    )
    summary = {
        "status": status,
        "authoritative_available": len(authoritative),
        "authoritative_with_pii": len(authoritative) - len(safe),
        "pilot_selected": len(selected),
        "excluded_ai": sum(
            item.get("stage3c", {}).get("classification") == "ai_generated"
            for item in episodes
        ),
        "excluded_unknown": sum(
            item.get("stage3c", {}).get("classification") == "unknown_historical"
            for item in episodes
        ),
        "selection_fingerprint": selection_fp,
        "diversity_categories": diversity["categories_present"],
        "confirmation_executed": False,
        "privacy_scan_findings": privacy_findings,
    }
    _write_json(output.parent / "summary.json", summary)
    (output.parent / "summary.md").write_text(
        _summary_markdown(summary),
        encoding="utf-8",
    )
    return summary


def selection_fingerprint(selection: Path) -> str:
    path = selection / "selection-fingerprint.txt"
    if not path.is_file():
        raise TelegramCurationError("pilot selection fingerprint is missing")
    recorded = path.read_text(encoding="utf-8-sig").strip()
    rows = _read_jsonl(selection / "selected.preview.jsonl")
    diversity = json.loads(
        (selection / "diversity-report.json").read_text(encoding="utf-8-sig")
    )
    source = str(
        diversity.get("source_reconciliation_fingerprint")
        or (rows[0].get("source_reconciliation_fingerprint", "") if rows else "")
    )
    computed = stable_fingerprint(
        {
            "source_reconciliation": source,
            "selected": rows,
            "diversity": diversity,
            "authoritative_only": True,
        }
    )
    if recorded != computed:
        raise TelegramCurationError("pilot selection fingerprint is invalid")
    return recorded


def resolve_profile_preview(
    *,
    agent_profile: Path,
    relationship_profile: Path,
    limit: int,
) -> dict[str, Any]:
    agent = json.loads(agent_profile.read_text(encoding="utf-8-sig"))
    relationship = json.loads(relationship_profile.read_text(encoding="utf-8-sig"))
    features = relationship.get("features") or agent.get("features", {})
    casing = features.get("casing", {})
    lower = float(casing.get("lowercase_frequency", 0.0))
    upper = float(casing.get("uppercase_frequency", 0.0))
    casing_mode = "lowercase" if lower > upper and lower >= 0.55 else "normal"
    length = features.get("response_length", {})
    bubbles = features.get("bubble_count", {})
    sample_count = int(relationship.get("sample_count", agent.get("sample_count", 0)))
    confidence = float(relationship.get("confidence", agent.get("confidence", 0.0)))
    return {
        "human_evidence": min(max(0, limit), sample_count),
        "sample_count": sample_count,
        "confidence": confidence,
        "fallback": sample_count < 5,
        "resolved_distributions": features,
        "casing": casing_mode,
        "casing_reason": "relationship human casing distribution",
        "length_reason": f"observed median={length.get('median')}",
        "bubble_reason": f"observed median={bubbles.get('median')}",
        "fixed_rules": [],
        "llm_called": False,
    }


def _selection_payload(
    episode: dict[str, Any],
    *,
    reconciliation_fingerprint_value: str,
) -> dict[str, Any]:
    output = json.loads(json.dumps(episode))
    output["provenance"].pop("message_ids", None)
    output["stage3c"].pop("message_record_ids", None)
    output["source_reconciliation_fingerprint"] = reconciliation_fingerprint_value
    output["selection_category"] = _categories(episode)
    output["style_features"] = _style_features(episode)
    output["pii_status"] = "clear"
    return output


def _categories(episode: dict[str, Any]) -> list[str]:
    messages = [str(item) for item in episode["human_target"]["messages"]]
    text = " ".join(messages)
    incoming = " ".join(
        str(item) for item in episode.get("incoming", {}).get("messages", [])
    )
    combined = f"{incoming} {text}".casefold()
    categories: list[str] = []
    length = len(text)
    if len(text.split()) <= 1:
        categories.append("one_word")
    if length <= 30:
        categories.append("short")
    if length >= 180:
        categories.append("long")
    if len(messages) > 1:
        categories.append("multi_bubble")
    if any(item and item[0].islower() for item in messages):
        categories.append("lowercase")
    if any(item and item[0].isupper() for item in messages):
        categories.append("normal_casing")
    if "?" in text:
        categories.append("owner_question")
    if "?" in incoming:
        categories.append("answers_question")
    else:
        categories.append("non_question_context")
    if re.search(r"https?://", text):
        categories.append("link")
    if re.search(r"[\U0001F300-\U0001FAFF]", text):
        categories.append("emoji")
    if re.search(
        r"(?i)\b(?:\u0431\u043b\u044f\w*|\u0445\u0443\u0439\w*|\u0435\u0431\w*)\b", text
    ):
        categories.append("profanity_or_slang")
    if re.search(
        r"(?i)\b(?:api|бот|код|сервер|модел|ошиб|python|telegram|"
        r"database|git|deploy|http)\w*",
        combined,
    ):
        categories.append("technical")
    else:
        categories.append("casual")
    if re.search(r"(?i)(?:ахах|хаха|лол|шут|joke|haha)", combined):
        categories.append("joke")
    if re.search(
        r"(?i)\b(?:важн|серьез|проблем|деньг|работ|здоров|документ)\w*",
        combined,
    ):
        categories.append("serious")
    if re.search(r"(?i)\b(?:созвон|звон|встреч|call|meet)\w*", combined):
        categories.append("call_coordination")
    if re.search(r"(?i)\b(?:кстати|ладно|другая тема|anyway)\b", combined):
        categories.append("topic_shift")
    if (
        "!" in text
        or "profanity_or_slang" in categories
        or re.search(r"(?i)\b(?:бесит|обид|рад|страш|любл)\w*", combined)
    ):
        categories.append("emotional")
    if any(_looks_like_typo(item) for item in messages):
        categories.append("typo_candidate")
    return categories or ["ordinary"]


def _looks_like_typo(text: str) -> bool:
    words = re.findall(r"[A-Za-zА-Яа-яЁё]+", text)
    return any(
        len(word) >= 5
        and (
            re.search(r"(?i)([бвгджзклмнпрстфхцчшщ])\1\1", word)
            or re.search(r"(?i)[бвгджзклмнпрстфхцчшщ]{5}", word)
        )
        for word in words
    )


def _style_features(episode: dict[str, Any]) -> dict[str, Any]:
    messages = [str(item) for item in episode["human_target"]["messages"]]
    return {
        "bubble_count": len(messages),
        "character_count": sum(len(item) for item in messages),
        "has_question": any("?" in item for item in messages),
        "casing_preserved": True,
        "grammar_preserved": True,
    }


def _diversity_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(
        category for item in rows for category in item["selection_category"]
    )
    lengths = [item["style_features"]["character_count"] for item in rows]
    bubbles = [item["style_features"]["bubble_count"] for item in rows]
    return {
        "selected": len(rows),
        "categories": dict(sorted(counts.items())),
        "categories_present": sorted(counts),
        "length_range": [min(lengths, default=0), max(lengths, default=0)],
        "bubble_count_range": [min(bubbles, default=0), max(bubbles, default=0)],
        "text_normalized": False,
        "synthetic_examples": 0,
        "fixed_rules": [],
    }


def _record_scopes(
    record: dict[str, Any],
    episodes: dict[str, dict[str, Any]],
) -> set[str]:
    scopes: set[str] = set()
    for episode_id in record.get("episode_ids", []):
        classification = (
            episodes.get(str(episode_id), {}).get("stage3c", {}).get("classification")
        )
        if classification in AUTHORITATIVE_HUMAN:
            scopes.add("authoritative_human")
        elif classification == "ai_generated":
            scopes.add("ai")
        elif classification == "unknown_historical":
            scopes.add("unknown")
        elif classification == "conflicting_evidence":
            scopes.add("conflicting")
    return scopes or {"excluded"}


def _primary_scope(scopes: set[str]) -> str:
    for value in ("authoritative_human", "ai", "unknown", "conflicting", "excluded"):
        if value in scopes:
            return value
    return "excluded"


def _meaning_loss(pii_type: str) -> str:
    return "high" if recommended_pii_action(pii_type) == "exclude" else "low"


def _validate_reconciliation(path: Path) -> str:
    recorded = (
        (path / "reconciliation-fingerprint.txt")
        .read_text(encoding="utf-8-sig")
        .strip()
    )
    if reconciliation_fingerprint(path) != recorded:
        raise TelegramCurationError("reconciliation fingerprint is invalid")
    return recorded


def _pii_review_markdown(rows: list[dict[str, Any]]) -> str:
    lines = ["# Scoped PII review", ""]
    for item in rows:
        lines.extend(
            (
                f"## {str(item['record_id'])[:18]}...",
                "",
                f"- Type: `{item['pii_type']}`",
                f"- Fragment: `{item['masked_fragment']}`",
                f"- Recommendation: `{item['recommended_action']}`",
                f"- Meaning loss: {item['meaning_loss']}",
                f"- Authoritative human: {item['authoritative_human']}",
                "",
            )
        )
    return "\n".join(lines)


def _selected_review_markdown(rows: list[dict[str, Any]]) -> str:
    lines = ["# Authoritative human pilot", ""]
    for index, item in enumerate(rows, start=1):
        lines.extend(
            (
                f"## {index}. {item['example_id']}",
                "",
                f"- Provenance: `{item['stage3c']['classification']}`",
                (
                    "- Evidence: "
                    f"authoritative={item['stage3c'].get('authoritative')}; "
                    f"verified={item['provenance'].get('verified')}"
                ),
                f"- PII: `{item['pii_status']}`",
                f"- Categories: {', '.join(item['selection_category'])}",
                f"- Style: `{item['style_features']}`",
                "",
                "**CONTACT (context only)**",
                "",
            )
        )
        lines.extend(f"> {text}" for text in item["incoming"]["messages"])
        lines.extend(("", "**OWNER (authoritative target bubbles)**", ""))
        lines.extend(f"- {text}" for text in item["human_target"]["messages"])
        lines.append("")
    return "\n".join(lines)


def _summary_markdown(summary: dict[str, Any]) -> str:
    return "\n".join(
        (
            "# Stage 3D authoritative pilot",
            "",
            f"- Status: `{summary['status']}`",
            f"- Authoritative available: {summary['authoritative_available']}",
            f"- Authoritative with PII: {summary['authoritative_with_pii']}",
            f"- Pilot selected: {summary['pilot_selected']}",
            f"- AI excluded: {summary['excluded_ai']}",
            f"- Unknown excluded: {summary['excluded_unknown']}",
            f"- Fingerprint: `{summary['selection_fingerprint']}`",
            "",
            "Confirmation has not been executed.",
            "",
        )
    )


def _write_csv(
    path: Path, fields: tuple[str, ...], rows: Iterable[dict[str, Any]]
) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(item) for item in csv.DictReader(handle)]


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    payload = "\n".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True) for item in rows
    )
    path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
