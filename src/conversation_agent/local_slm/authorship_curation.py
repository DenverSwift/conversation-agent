"""Stage 3E authorship verification and conservative pilot cleanup."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from conversation_agent.local_slm.authoritative_pilot import (
    selection_fingerprint,
)
from conversation_agent.local_slm.batch_curation import (
    DEFAULT_DATASET_ROOT,
    _curated_training_payload,
)
from conversation_agent.local_slm.provenance_recovery import (
    discovery_report,
    load_discovered_locations,
)
from conversation_agent.local_slm.stage2_dataset import (
    registered_benchmark_fingerprints,
    stable_fingerprint,
)
from conversation_agent.local_slm.telegram_curation import (
    AuditEvidence,
    TelegramCurationError,
    load_audit_evidence,
    reconciliation_fingerprint,
)
from conversation_agent.local_slm.telegram_import import parse_optional_datetime
from conversation_agent.local_slm.telegram_privacy import scan_text

TransportStatus = Literal[
    "telegram_manual_path",
    "audited_send_path",
    "trainer_bot_path",
    "automated_send_path",
    "unknown_transport",
]
AuthorshipStatus = Literal[
    "human_authored",
    "ai_authored",
    "human_edited_ai",
    "unknown_authorship",
    "conflicting_authorship",
]

APPROVED_AUTHORSHIP_ACTIONS = frozenset(
    {"include_human", "exclude_ai", "exclude_uncertain"}
)
RECOMMENDED_AUTHORSHIP_ACTIONS = frozenset(
    {*APPROVED_AUTHORSHIP_ACTIONS, "manual_review"}
)
REQUIRED_REVIEW_POSITIONS = frozenset({7, *range(43, 53)})
MAX_REVIEW_ENTRIES = 20

_GENERIC_ASSISTANT_PATTERNS = (
    (
        r"\u0447\u0435\u043c (?:\u044f )?\u043c\u043e\u0433\u0443 "
        r"\u043f\u043e\u043c\u043e\u0447\u044c"
    ),
    (
        r"\u0447\u0442\u043e \u0438\u043c\u0435\u043d\u043d\u043e "
        r"(?:\u0432\u0430\u0441 )?\u0438\u043d\u0442\u0435\u0440\u0435\u0441\u0443\u0435\u0442"
    ),
    (
        r"\u043c\u043e\u0436\u0435\u043c \u043f\u043e\u0433\u043e\u0432\u043e\u0440\u0438\u0442\u044c "
        r"\u043e\u0431 \u044d\u0442\u043e\u043c"
    ),
    r"\u0447\u0442\u043e-\u0442\u043e \u043d\u0435 \u0442\u0430\u043a",
    (
        r"\u043d\u0435 \u0431\u0435\u0441\u043f\u043e\u043a\u043e\u0439\u0441\u044f, "
        r"\u044d\u0442\u043e \u044f"
    ),
    (
        r"\u0440\u0430\u0441\u0441\u043a\u0430\u0436\u0438(?:\u0442\u0435)? "
        r"\u043f\u043e\u0434\u0440\u043e\u0431\u043d\u0435\u0435"
    ),
)
_CLARIFICATION_PATTERNS = (
    r"\u0447\u0442\u043e \u0438\u043c\u0435\u043d\u043d\u043e",
    r"\u043a\u0430\u043a\u0438\u0435 \u0438\u043c\u0435\u043d\u043d\u043e",
    (
        r"\u043c\u043e\u0433(?:\u043b\u0438|\u043b\u0430|\u043b) \u0431\u044b "
        r"\u0443\u0442\u043e\u0447\u043d\u0438\u0442\u044c"
    ),
    r"\u0443\u0442\u043e\u0447\u043d\u0438(?:\u0442\u0435)?",
)
_SAFETY_PATTERNS = (
    (
        r"\u044f \u0437\u0434\u0435\u0441\u044c, \u0447\u0442\u043e\u0431\u044b "
        r"\u043f\u043e\u043c\u043e\u0447\u044c"
    ),
    (
        r"\u043e\u0431\u0440\u0430\u0442\u0438(?:\u0442\u0435)?\u0441\u044c "
        r"\u043a \u0441\u043f\u0435\u0446\u0438\u0430\u043b\u0438\u0441\u0442\u0443"
    ),
    r"\u0435\u0441\u043b\u0438 \u0432\u044b \u0432 \u043e\u043f\u0430\u0441\u043d\u043e\u0441\u0442\u0438",
    r"\u043d\u0435 \u043c\u043e\u0433\u0443 \u043f\u043e\u043c\u043e\u0447\u044c \u0441",
)
_BOT_PATTERN = re.compile(
    r"(?i)\b(?:bot|\u0431\u043e\u0442|\u0431\u043e\u0442\u0430|"
    r"\u0431\u043e\u0442\u043e\u043c|\u0431\u043e\u0442\u0443)\b"
)


@dataclass(frozen=True)
class TransportProvenance:
    status: TransportStatus
    authoritative: bool
    evidence: tuple[str, ...]
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "evidence": list(self.evidence)}


@dataclass(frozen=True)
class AuthorshipProvenance:
    status: AuthorshipStatus
    authoritative: bool
    evidence: tuple[str, ...]
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "evidence": list(self.evidence)}


def reconcile_authorship(
    *,
    reconciliation: Path,
    pilot_selection: Path,
    output: Path,
    max_review_entries: int = MAX_REVIEW_ENTRIES,
) -> dict[str, Any]:
    if not 1 <= max_review_entries <= MAX_REVIEW_ENTRIES:
        raise TelegramCurationError("--max-review-entries must be between 1 and 20")
    reconciliation_fp = _validate_reconciliation(reconciliation)
    pilot_fp = selection_fingerprint(pilot_selection)
    selected = _read_jsonl(pilot_selection / "selected.preview.jsonl")
    if any(
        str(item.get("source_reconciliation_fingerprint", ""))
        != reconciliation_fp
        for item in selected
    ):
        raise TelegramCurationError("pilot selection does not match reconciliation")
    episodes = {
        str(item["example_id"]): item
        for item in _read_jsonl(reconciliation / "episodes.reconciled.jsonl")
    }
    messages = {
        str(item["record_id"]): item
        for item in _read_jsonl(reconciliation / "messages.reconciled.jsonl")
    }
    live_evidence = _load_live_audit_evidence(reconciliation)
    missing = [
        str(item.get("example_id", ""))
        for item in selected
        if str(item.get("example_id", "")) not in episodes
    ]
    if missing:
        raise TelegramCurationError("pilot contains examples absent from reconciliation")
    selected_ids = {str(item["example_id"]) for item in selected}
    pii_ids = {
        str(episode_id)
        for item in _read_jsonl(reconciliation / "pii-records.jsonl")
        for episode_id in item.get("episode_ids", [])
        if str(episode_id) in selected_ids
    }
    duplicate_counts = Counter(
        _normalized_target(item)
        for item in selected
        if _normalized_target(item)
    )
    full_order = list(episodes)
    order_index = {example_id: index for index, example_id in enumerate(full_order)}
    records: list[dict[str, Any]] = []
    for position, selected_item in enumerate(selected, start=1):
        example_id = str(selected_item["example_id"])
        episode = episodes[example_id]
        message_results = [
            _with_live_authorship_evidence(
                messages[record_id],
                episode=episode,
                evidence=live_evidence,
            )
            for record_id in episode.get("stage3c", {}).get(
                "message_record_ids", []
            )
            if record_id in messages
        ]
        transport = _episode_transport(message_results)
        authorship = _episode_authorship(message_results)
        flags = suspicious_authorship_flags(
            selected_item,
            position=position,
            duplicate_count=duplicate_counts[_normalized_target(selected_item)],
            neighbor_classes=_neighbor_classes(
                example_id,
                episodes=episodes,
                full_order=full_order,
                order_index=order_index,
            ),
        )
        recommended = _recommended_action(authorship.status, flags)
        records.append(
            {
                "example_id": example_id,
                "pilot_position": position,
                "transport": transport.to_dict(),
                "authorship": authorship.to_dict(),
                "neighboring_segment_evidence": _neighbor_classes(
                    example_id,
                    episodes=episodes,
                    full_order=full_order,
                    order_index=order_index,
                ),
                "heuristic_flags": flags,
                "heuristics_are_authoritative": False,
                "pii_status": "unresolved" if example_id in pii_ids else "clear",
                "review_required": bool(flags)
                or authorship.status
                in {
                    "ai_authored",
                    "unknown_authorship",
                    "conflicting_authorship",
                },
                "recommended_action": recommended,
            }
        )
    review_records = _review_sample(records, limit=max_review_entries)
    output.mkdir(parents=True, exist_ok=True)
    review = output.parent / "review"
    review.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output / "reconciliation.jsonl", records)
    authorship_fp = stable_fingerprint(
        {
            "source_reconciliation_fingerprint": reconciliation_fp,
            "source_pilot_fingerprint": pilot_fp,
            "records": records,
        }
    )
    (output / "fingerprint.txt").write_text(
        authorship_fp + "\n",
        encoding="utf-8",
    )
    counts = Counter(
        str(item["authorship"]["status"])
        for item in records
    )
    transport_authoritative = sum(
        item["transport"]["authoritative"] is True for item in records
    )
    suspicious_count = sum(bool(item["heuristic_flags"]) for item in records)
    proposed_clean = sum(
        item["authorship"]["status"] in {"human_authored", "human_edited_ai"}
        and not item["heuristic_flags"]
        and item["pii_status"] == "clear"
        for item in records
    )
    summary = {
        "schema_version": 1,
        "status": "READY_FOR_AUTHORSHIP_REVIEW",
        "source_reconciliation_fingerprint": reconciliation_fp,
        "source_pilot_fingerprint": pilot_fp,
        "authorship_fingerprint": authorship_fp,
        "pilot_examples": len(records),
        "transport_authoritative": transport_authoritative,
        "human_authored": counts["human_authored"],
        "ai_authored": counts["ai_authored"],
        "human_edited_ai": counts["human_edited_ai"],
        "unknown_authorship": counts["unknown_authorship"],
        "conflicting_authorship": counts["conflicting_authorship"],
        "suspicious": suspicious_count,
        "review_entries": len(review_records),
        "unresolved_review_entries": sum(
            item["review_required"] for item in records
        ),
        "proposed_clean_pilot": proposed_clean,
        "pii_inside_source_pilot": len(pii_ids),
        "heuristics_changed_authorship": False,
        "confirmation_executed": False,
        "live_audit_sources_checked": len(
            {item.source_alias for item in live_evidence}
        ),
    }
    _write_json(output / "provenance-summary.json", summary)
    _write_authorship_review(
        review / "authorship-review.md",
        review_records,
        selected,
    )
    _write_authorship_decisions(
        review / "authorship-decisions.csv",
        records,
    )
    _write_status_markdown(
        review / "confirmed-ai.md",
        records,
        selected,
        title="Confirmed AI authorship",
        statuses={"ai_authored"},
    )
    _write_status_markdown(
        review / "confirmed-human.md",
        records,
        selected,
        title="Confirmed human authorship",
        statuses={"human_authored", "human_edited_ai"},
    )
    _write_suspicious_segments(
        review / "suspicious-segments.md",
        [item for item in records if item["heuristic_flags"]],
    )
    _write_stage_summary(output.parent / "summary.md", summary)
    return summary


def suspicious_authorship_flags(
    episode: dict[str, Any],
    *,
    position: int,
    duplicate_count: int,
    neighbor_classes: list[str],
) -> list[str]:
    target = " ".join(
        str(item) for item in episode.get("human_target", {}).get("messages", [])
    )
    incoming = " ".join(
        str(item) for item in episode.get("incoming", {}).get("messages", [])
    )
    normalized = target.casefold()
    flags = {
        str(item)
        for item in episode.get("stage3c", {}).get("heuristic_flags", [])
    }
    if position in REQUIRED_REVIEW_POSITIONS:
        flags.add("required_stage3e_regression_review")
    if any(re.search(pattern, normalized) for pattern in _GENERIC_ASSISTANT_PATTERNS):
        flags.add("generic_assistant_response")
    if target.count("?") >= 2 or (
        "?" in target
        and any(re.search(pattern, normalized) for pattern in _CLARIFICATION_PATTERNS)
    ):
        flags.add("excessive_clarification_question")
    if any(re.search(pattern, normalized) for pattern in _SAFETY_PATTERNS):
        flags.add("safety_template_phrasing")
    if _BOT_PATTERN.search(incoming):
        flags.add("working_bot_context")
    if "ai_generated" in neighbor_classes:
        flags.add("neighboring_confirmed_ai")
    if duplicate_count > 1 and len(normalized) >= 24:
        flags.add("repeated_response_template")
    return sorted(flags)


def apply_conservative_authorship(
    *,
    authorship: Path,
    decisions: Path,
    pilot_selection: Path,
    output: Path,
    exclude_recommended_suspicious: bool,
    exclude_unresolved: bool,
    min_examples: int = 50,
) -> dict[str, Any]:
    if min_examples < 1:
        raise TelegramCurationError("--min-examples must be greater than zero")
    authorship_fp, summary = _validate_authorship(authorship)
    pilot_fp = selection_fingerprint(pilot_selection)
    if summary.get("source_pilot_fingerprint") != pilot_fp:
        raise TelegramCurationError("authorship does not match pilot selection")
    records = {
        str(item["example_id"]): item
        for item in _read_jsonl(authorship / "reconciliation.jsonl")
    }
    selected = _read_jsonl(pilot_selection / "selected.preview.jsonl")
    decision_map = _read_authorship_decisions(decisions)
    included: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for item in selected:
        example_id = str(item["example_id"])
        record = records.get(example_id)
        if record is None:
            raise TelegramCurationError("pilot example is missing authorship record")
        approved = decision_map.get(example_id, "")
        status = str(record["authorship"]["status"])
        flags = [str(value) for value in record.get("heuristic_flags", [])]
        reasons: list[str] = []
        if status in {"ai_authored", "conflicting_authorship"}:
            reasons.append(status)
        if record.get("pii_status") != "clear":
            reasons.append("unresolved_pii")
        if approved in {"exclude_ai", "exclude_uncertain"}:
            reasons.append(f"user_{approved}")
        user_included = approved == "include_human"
        evidence_included = status in {"human_authored", "human_edited_ai"}
        if (
            flags
            and exclude_recommended_suspicious
            and not user_included
            and not evidence_included
        ):
            reasons.append("recommended_suspicious")
        if (
            exclude_unresolved
            and not user_included
            and not evidence_included
        ):
            reasons.append("unresolved_authorship")
        if reasons:
            exclusions.append(
                {
                    "example_id": example_id,
                    "pilot_position": record["pilot_position"],
                    "reasons": sorted(set(reasons)),
                }
            )
            continue
        if not (user_included or evidence_included):
            exclusions.append(
                {
                    "example_id": example_id,
                    "pilot_position": record["pilot_position"],
                    "reasons": ["not_explicitly_human"],
                }
            )
            continue
        clean = json.loads(json.dumps(item))
        clean["transport_provenance"] = record["transport"]
        clean["authorship_provenance"] = {
            **record["authorship"],
            "user_approved": user_included,
        }
        clean["stage3e"] = {
            "source_authorship_fingerprint": authorship_fp,
            "source_pilot_fingerprint": pilot_fp,
            "pilot_position": record["pilot_position"],
            "heuristic_flags": flags,
            "text_normalized": False,
            "bubble_boundaries_preserved": True,
        }
        included.append(clean)
    output.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output / "selected.preview.jsonl", included)
    _write_clean_review(output / "selected-review.md", included)
    _write_exclusions(output / "exclusions.md", exclusions)
    diversity = _clean_diversity(included)
    _write_json(output / "diversity-report.json", diversity)
    clean_fp = stable_fingerprint(
        {
            "source_authorship_fingerprint": authorship_fp,
            "source_pilot_fingerprint": pilot_fp,
            "selected": included,
            "exclusions": exclusions,
            "diversity": diversity,
            "conservative": True,
        }
    )
    (output / "fingerprint.txt").write_text(clean_fp + "\n", encoding="utf-8")
    included_ids = {str(item["example_id"]) for item in included}
    unresolved_review = sum(
        str(item["example_id"]) in included_ids
        and not decision_map.get(str(item["example_id"]))
        for item in _read_jsonl(authorship / "reconciliation.jsonl")
        if item.get("review_required")
    )
    status = (
        "CLEAN_PILOT_READY"
        if len(included) >= min_examples and unresolved_review == 0
        else "INSUFFICIENT_AUTHORSHIP_VERIFIED_DATA"
    )
    result = {
        "status": status,
        "selected": len(included),
        "excluded": len(exclusions),
        "minimum_required": min_examples,
        "unresolved_review_entries": unresolved_review,
        "pii_inside_clean_pilot": sum(
            item.get("pii_status") != "clear"
            for item in (
                records[str(value["example_id"])] for value in included
            )
        ),
        "clean_pilot_fingerprint": clean_fp,
        "source_authorship_fingerprint": authorship_fp,
        "source_pilot_fingerprint": pilot_fp,
        "confirmation_executed": False,
    }
    _write_json(output / "summary.json", result)
    _write_confirmation_command(
        output.parent / "confirmation-command.txt",
        authorship=authorship,
        clean_pilot=output,
        decisions=decisions,
        fingerprint=clean_fp,
    )
    return result


def clean_pilot_fingerprint(path: Path) -> str:
    recorded_path = path / "fingerprint.txt"
    if not recorded_path.is_file():
        raise TelegramCurationError("clean pilot fingerprint is missing")
    recorded = recorded_path.read_text(encoding="utf-8-sig").strip()
    summary = json.loads(
        (path / "summary.json").read_text(encoding="utf-8-sig")
    )
    selected = _read_jsonl(path / "selected.preview.jsonl")
    diversity = json.loads(
        (path / "diversity-report.json").read_text(encoding="utf-8-sig")
    )
    exclusions = _read_exclusions(path / "exclusions.md")
    computed = stable_fingerprint(
        {
            "source_authorship_fingerprint": summary[
                "source_authorship_fingerprint"
            ],
            "source_pilot_fingerprint": summary[
                "source_pilot_fingerprint"
            ],
            "selected": selected,
            "exclusions": exclusions,
            "diversity": diversity,
            "conservative": True,
        }
    )
    if computed != recorded or summary.get("clean_pilot_fingerprint") != recorded:
        raise TelegramCurationError("clean pilot fingerprint is invalid")
    return recorded


def confirm_clean_pilot(
    *,
    preview: Path,
    reconciliation: Path,
    authorship: Path,
    clean_pilot: Path,
    authorship_decisions: Path,
    fingerprint: str,
    consent_confirmed: bool,
    dataset_root: Path = DEFAULT_DATASET_ROOT,
) -> dict[str, Any]:
    if not consent_confirmed:
        raise TelegramCurationError("--consent-confirmed is required")
    reconciliation_fp = _validate_reconciliation(reconciliation)
    clean_fp = clean_pilot_fingerprint(clean_pilot)
    if fingerprint != clean_fp:
        raise TelegramCurationError("clean pilot fingerprint does not match")
    _, authorship_summary = _validate_authorship(authorship)
    if authorship_summary.get("source_reconciliation_fingerprint") != reconciliation_fp:
        raise TelegramCurationError("authorship does not match reconciliation")
    source_preview = json.loads(
        (reconciliation / "manifest.json").read_text(encoding="utf-8-sig")
    ).get("source_preview_fingerprint")
    current_preview = (
        (preview / "preview-fingerprint.txt")
        .read_text(encoding="utf-8-sig")
        .strip()
    )
    if source_preview != current_preview:
        raise TelegramCurationError("preview does not match reconciliation")
    if source_preview in registered_benchmark_fingerprints():
        raise TelegramCurationError("benchmark data cannot be confirmed")
    selected = _read_jsonl(clean_pilot / "selected.preview.jsonl")
    if len(selected) < 50:
        raise TelegramCurationError("clean pilot requires at least 50 examples")
    records = {
        str(item["example_id"]): item
        for item in _read_jsonl(authorship / "reconciliation.jsonl")
    }
    decisions = _read_authorship_decisions(authorship_decisions)
    selected_ids = {str(item["example_id"]) for item in selected}
    pii_inside = [
        item
        for item in _read_jsonl(reconciliation / "pii-records.jsonl")
        if selected_ids.intersection(
            str(value) for value in item.get("episode_ids", [])
        )
    ]
    if pii_inside:
        raise TelegramCurationError("unresolved PII exists inside clean pilot")
    for item in selected:
        example_id = str(item["example_id"])
        record = records.get(example_id)
        if record is None:
            raise TelegramCurationError("clean pilot example lacks authorship")
        status = str(record["authorship"]["status"])
        approved = decisions.get(example_id, "")
        if status in {"ai_authored", "conflicting_authorship"}:
            raise TelegramCurationError("non-human authorship exists in clean pilot")
        if status == "unknown_authorship" and approved != "include_human":
            raise TelegramCurationError(
                "unknown authorship requires explicit include_human"
            )
        if record.get("review_required") and not approved:
            raise TelegramCurationError(
                "unresolved review entry exists inside clean pilot"
            )
    privacy_findings = sum(
        len(scan_text(str(text)))
        for item in selected
        for section in ("incoming", "human_target")
        for text in item.get(section, {}).get("messages", [])
    )
    if privacy_findings:
        raise TelegramCurationError("privacy scan failed for clean pilot")
    destination = dataset_root / "raw" / f"clean-{clean_fp[:12]}"
    if destination.exists():
        raise TelegramCurationError("clean pilot destination already exists")
    destination.mkdir(parents=True, exist_ok=False)
    payloads: list[dict[str, Any]] = []
    for item in selected:
        example_id = str(item["example_id"])
        record = records[example_id]
        payload = _curated_training_payload(
            item,
            user_batch_approved=False,
            authoritative_only=True,
        )
        payload["provenance"].update(
            {
                "source": "stage3e_authorship_verified_pilot",
                "authorship_status": record["authorship"]["status"],
                "authorship_authoritative": record["authorship"][
                    "authoritative"
                ],
                "user_authorship_approved": (
                    decisions.get(example_id) == "include_human"
                ),
            }
        )
        payloads.append(payload)
    _write_jsonl(destination / "examples.jsonl", payloads)
    dataset_fp = stable_fingerprint(payloads)
    _write_json(
        destination / "manifest.json",
        {
            "schema_version": 1,
            "source_reconciliation_fingerprint": reconciliation_fp,
            "source_authorship_fingerprint": authorship_summary[
                "authorship_fingerprint"
            ],
            "clean_pilot_fingerprint": clean_fp,
            "dataset_fingerprint": dataset_fp,
            "examples": len(payloads),
            "source_type": "imported_human_verified",
            "training_performed": False,
        },
    )
    return {
        "confirmed": True,
        "dataset": str(destination),
        "examples": len(payloads),
        "dataset_fingerprint": dataset_fp,
        "training_performed": False,
    }


def _episode_transport(
    message_results: list[dict[str, Any]],
) -> TransportProvenance:
    kinds = {
        str(source.get("evidence_kind", ""))
        for item in message_results
        for source in item.get("evidence_sources", [])
    }
    methods = {
        str(item.get("match_method", ""))
        for item in message_results
    }
    if any("generated_reply_send_audit" in item for item in kinds):
        status: TransportStatus = "automated_send_path"
    elif any(
        item == "trainer_human_correction" or item.startswith("style_source:fix")
        for item in kinds
    ):
        status = "trainer_bot_path"
    elif methods.intersection(
        {"exact_chat_and_message_id", "exact_hash_destination_time"}
    ):
        status = "audited_send_path"
    elif message_results:
        status = "telegram_manual_path"
    else:
        status = "unknown_transport"
    authoritative = bool(
        message_results
        and all(
            item.get("match_method")
            in {"exact_chat_and_message_id", "exact_hash_destination_time"}
            for item in message_results
        )
    )
    confidence = min(
        (float(item.get("confidence", 0.0)) for item in message_results),
        default=0.0,
    )
    return TransportProvenance(
        status=status,
        authoritative=authoritative,
        evidence=tuple(sorted(kinds or methods)),
        confidence=round(confidence, 6),
    )


def _load_live_audit_evidence(reconciliation: Path) -> list[AuditEvidence]:
    discovery = reconciliation.parent / "discovery"
    if not discovery.is_dir():
        return []
    return load_audit_evidence(
        discovery_report(discovery),
        load_discovered_locations(discovery),
    )


def _with_live_authorship_evidence(
    message_result: dict[str, Any],
    *,
    episode: dict[str, Any],
    evidence: list[AuditEvidence],
) -> dict[str, Any]:
    output = json.loads(json.dumps(message_result))
    message_id = _optional_int(output.get("telegram_message_id"))
    record_ids = episode.get("stage3c", {}).get("message_record_ids", [])
    try:
        bubble_index = list(record_ids).index(output.get("record_id"))
    except ValueError:
        bubble_index = 0
    bubbles = episode.get("human_target", {}).get("messages", [])
    timestamps = episode.get("human_target", {}).get("timestamps", [])
    text = str(bubbles[bubble_index]) if bubble_index < len(bubbles) else ""
    timestamp = (
        str(timestamps[bubble_index])
        if bubble_index < len(timestamps)
        else ""
    )
    text_hash = _normalized_text_hash(text)
    exact_sources: list[dict[str, Any]] = []
    for item in evidence:
        if not text_hash or item.text_hash != text_hash:
            continue
        distance = _timestamp_distance(timestamp, item.timestamp)
        exact_message = (
            message_id is not None and item.message_id == message_id
        )
        exact_human_edit = (
            item.evidence_kind == "trainer_human_correction"
            or item.evidence_kind.startswith("style_source:fix")
        ) and distance is not None and distance <= 86400
        if not (exact_message or exact_human_edit):
            continue
        exact_sources.append(item.safe_dict())
    existing = {
        (
            str(item.get("source_alias", "")),
            str(item.get("record_id", "")),
            str(item.get("evidence_kind", "")),
        )
        for item in output.get("evidence_sources", [])
    }
    output.setdefault("evidence_sources", []).extend(
        item
        for item in exact_sources
        if (
            str(item.get("source_alias", "")),
            str(item.get("record_id", "")),
            str(item.get("evidence_kind", "")),
        )
        not in existing
    )
    return output


def _normalized_text_hash(value: str) -> str:
    normalized = " ".join(value.casefold().split())
    return (
        hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if normalized
        else ""
    )


def _timestamp_distance(left: str, right: str | None) -> float | None:
    if not left or not right:
        return None
    left_value = parse_optional_datetime(left)
    right_value = parse_optional_datetime(right)
    if left_value is None or right_value is None:
        return None
    return abs((left_value - right_value).total_seconds())


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _episode_authorship(
    message_results: list[dict[str, Any]],
) -> AuthorshipProvenance:
    message_statuses: list[AuthorshipStatus] = []
    evidence: set[str] = set()
    for item in message_results:
        status, kinds = _message_authorship(item.get("evidence_sources", []))
        message_statuses.append(status)
        evidence.update(kinds)
    statuses = set(message_statuses)
    if "conflicting_authorship" in statuses:
        status: AuthorshipStatus = "conflicting_authorship"
    elif "ai_authored" in statuses and statuses.intersection(
        {"human_authored", "human_edited_ai"}
    ):
        status = "conflicting_authorship"
    elif "ai_authored" in statuses:
        status = "ai_authored"
    elif statuses and statuses <= {"human_authored", "human_edited_ai"}:
        status = (
            "human_edited_ai"
            if "human_edited_ai" in statuses
            else "human_authored"
        )
    elif not statuses:
        status = "unknown_authorship"
    else:
        status = "unknown_authorship"
    authoritative = status != "unknown_authorship"
    confidence = {
        "human_authored": 1.0,
        "ai_authored": 1.0,
        "human_edited_ai": 1.0,
        "conflicting_authorship": 1.0,
        "unknown_authorship": 0.0,
    }[status]
    return AuthorshipProvenance(
        status=status,
        authoritative=authoritative,
        evidence=tuple(sorted(evidence)),
        confidence=confidence,
    )


def _message_authorship(
    sources: Iterable[dict[str, Any]],
) -> tuple[AuthorshipStatus, set[str]]:
    ai: set[str] = set()
    human: set[str] = set()
    edited: set[str] = set()
    transport_only: set[str] = set()
    for source in sources:
        kind = str(source.get("evidence_kind", "")).casefold()
        if (
            "generated_reply_send_audit" in kind
            or kind.startswith(
                ("style_source:approved_ai", "style_source:rejected")
            )
            or "ai_generated" in kind
        ):
            ai.add(kind)
        elif (
            kind == "trainer_human_correction"
            or kind.startswith("style_source:fix")
            or "human_edit" in kind
            or "human_fix" in kind
        ):
            edited.add(kind)
        elif "human_takeover" in kind or "human_manual" in kind or "manual_input" in kind:
            human.add(kind)
        elif kind:
            transport_only.add(kind)
    evidence = ai | human | edited | transport_only
    if ai and (human or edited):
        return "conflicting_authorship", evidence
    if edited:
        return "human_edited_ai", evidence
    if human:
        return "human_authored", evidence
    if ai:
        return "ai_authored", evidence
    return "unknown_authorship", evidence


def _neighbor_classes(
    example_id: str,
    *,
    episodes: dict[str, dict[str, Any]],
    full_order: list[str],
    order_index: dict[str, int],
) -> list[str]:
    index = order_index[example_id]
    neighbor_ids = full_order[max(0, index - 1) : index] + full_order[
        index + 1 : index + 2
    ]
    return [
        str(episodes[item].get("stage3c", {}).get("classification", "unknown"))
        for item in neighbor_ids
    ]


def _recommended_action(
    status: str,
    flags: list[str],
) -> str:
    if status == "ai_authored":
        return "exclude_ai"
    if status == "conflicting_authorship":
        return "exclude_uncertain"
    if status in {"human_authored", "human_edited_ai"}:
        return "include_human"
    return "exclude_uncertain" if flags else "manual_review"


def _review_sample(
    records: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    candidates = [
        item
        for item in records
        if item["heuristic_flags"]
        or item["authorship"]["status"]
        in {"ai_authored", "conflicting_authorship"}
    ]
    return sorted(
        candidates,
        key=lambda item: (
            item["pilot_position"] not in REQUIRED_REVIEW_POSITIONS,
            item["authorship"]["status"] != "ai_authored",
            -len(item["heuristic_flags"]),
            item["pilot_position"],
        ),
    )[:limit]


def _read_authorship_decisions(path: Path) -> dict[str, str]:
    output: dict[str, str] = {}
    for row in _read_csv(path):
        action = str(row.get("approved_action", "")).strip()
        if action and action not in APPROVED_AUTHORSHIP_ACTIONS:
            raise TelegramCurationError(f"invalid authorship action: {action}")
        if action:
            output[str(row.get("example_id", ""))] = action
    return output


def _validate_authorship(path: Path) -> tuple[str, dict[str, Any]]:
    fingerprint_path = path / "fingerprint.txt"
    if not fingerprint_path.is_file():
        raise TelegramCurationError("authorship fingerprint is missing")
    recorded = fingerprint_path.read_text(encoding="utf-8-sig").strip()
    summary = json.loads(
        (path / "provenance-summary.json").read_text(encoding="utf-8-sig")
    )
    records = _read_jsonl(path / "reconciliation.jsonl")
    computed = stable_fingerprint(
        {
            "source_reconciliation_fingerprint": summary[
                "source_reconciliation_fingerprint"
            ],
            "source_pilot_fingerprint": summary["source_pilot_fingerprint"],
            "records": records,
        }
    )
    if recorded != computed or summary.get("authorship_fingerprint") != recorded:
        raise TelegramCurationError("authorship fingerprint is invalid")
    return recorded, summary


def _validate_reconciliation(path: Path) -> str:
    recorded = (
        (path / "reconciliation-fingerprint.txt")
        .read_text(encoding="utf-8-sig")
        .strip()
    )
    if reconciliation_fingerprint(path) != recorded:
        raise TelegramCurationError("reconciliation fingerprint is invalid")
    return recorded


def _normalized_target(item: dict[str, Any]) -> str:
    text = " ".join(
        str(value)
        for value in item.get("human_target", {}).get("messages", [])
    )
    return " ".join(text.casefold().split())


def _write_authorship_review(
    path: Path,
    review_records: list[dict[str, Any]],
    selected: list[dict[str, Any]],
) -> None:
    selected_by_id = {str(item["example_id"]): item for item in selected}
    lines = ["# Authorship review", ""]
    for record in review_records:
        item = selected_by_id[str(record["example_id"])]
        lines.extend(
            (
                f"## {record['pilot_position']}. {record['example_id']}",
                "",
                f"- Transport: `{record['transport']['status']}`",
                (
                    "- Authorship: "
                    f"`{record['authorship']['status']}`; "
                    f"confidence={record['authorship']['confidence']}"
                ),
                (
                    "- Authorship evidence: "
                    + ", ".join(record["authorship"]["evidence"])
                ),
                (
                    "- Neighbor evidence: "
                    + ", ".join(record["neighboring_segment_evidence"])
                ),
                "- Heuristic flags: " + ", ".join(record["heuristic_flags"]),
                f"- Recommended: `{record['recommended_action']}`",
                "",
                "**CONTACT (context only)**",
                "",
            )
        )
        lines.extend(
            f"> {text}" for text in item.get("incoming", {}).get("messages", [])
        )
        lines.extend(("", "**OWNER (target bubbles)**", ""))
        lines.extend(
            f"- {text}"
            for text in item.get("human_target", {}).get("messages", [])
        )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_authorship_decisions(
    path: Path,
    records: list[dict[str, Any]],
) -> None:
    _write_csv(
        path,
        (
            "example_id",
            "recommended_action",
            "approved_action",
            "reason",
            "notes",
        ),
        [
            {
                "example_id": item["example_id"],
                "recommended_action": item["recommended_action"],
                "approved_action": "",
                "reason": ",".join(item["heuristic_flags"])
                or item["authorship"]["status"],
                "notes": "",
            }
            for item in records
        ],
    )


def _write_status_markdown(
    path: Path,
    records: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    *,
    title: str,
    statuses: set[str],
) -> None:
    selected_by_id = {str(item["example_id"]): item for item in selected}
    lines = [f"# {title}", ""]
    for record in records:
        if record["authorship"]["status"] not in statuses:
            continue
        item = selected_by_id[str(record["example_id"])]
        lines.extend(
            (
                f"## {record['example_id']}",
                "",
                f"- Authorship: `{record['authorship']['status']}`",
                "",
            )
        )
        lines.extend(
            f"- {text}"
            for text in item.get("human_target", {}).get("messages", [])
        )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_suspicious_segments(
    path: Path,
    records: list[dict[str, Any]],
) -> None:
    lines = ["# Suspicious segments", ""]
    for item in records:
        lines.extend(
            (
                f"- `{item['example_id']}` at pilot position "
                f"{item['pilot_position']}: "
                + ", ".join(item["heuristic_flags"]),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_stage_summary(path: Path, summary: dict[str, Any]) -> None:
    path.write_text(
        "\n".join(
            (
                "# Stage 3E authorship verification",
                "",
                f"- Status: `{summary['status']}`",
                f"- Pilot examples: {summary['pilot_examples']}",
                (
                    "- Transport-authoritative: "
                    f"{summary['transport_authoritative']}"
                ),
                f"- Human-authored: {summary['human_authored']}",
                f"- Human-edited AI: {summary['human_edited_ai']}",
                f"- AI-authored: {summary['ai_authored']}",
                f"- Unknown authorship: {summary['unknown_authorship']}",
                f"- Suspicious: {summary['suspicious']}",
                f"- Review entries: {summary['review_entries']}",
                (
                    "- Proposed conservative clean pilot: "
                    f"{summary['proposed_clean_pilot']}"
                ),
                "",
                "Confirmation has not been executed.",
                "",
            )
        ),
        encoding="utf-8",
    )


def _write_clean_review(path: Path, selected: list[dict[str, Any]]) -> None:
    lines = ["# Conservative clean pilot", ""]
    for index, item in enumerate(selected, start=1):
        lines.extend(
            (
                f"## {index}. {item['example_id']}",
                "",
                (
                    "- Authorship: "
                    f"`{item['authorship_provenance']['status']}`"
                ),
                "- Text normalized: false",
                "",
                "**CONTACT (context only)**",
                "",
            )
        )
        lines.extend(
            f"> {text}" for text in item.get("incoming", {}).get("messages", [])
        )
        lines.extend(("", "**OWNER (preserved bubbles)**", ""))
        lines.extend(
            f"- {text}"
            for text in item.get("human_target", {}).get("messages", [])
        )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_exclusions(
    path: Path,
    exclusions: list[dict[str, Any]],
) -> None:
    path.write_text(
        "\n".join(
            [
                "# Conservative exclusions",
                "",
                *[
                    f"- `{item['example_id']}` at position "
                    f"{item['pilot_position']}: {', '.join(item['reasons'])}"
                    for item in exclusions
                ],
                "",
                "<!-- exclusions-json:",
                json.dumps(exclusions, ensure_ascii=True, sort_keys=True),
                "-->",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _read_exclusions(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig")
    match = re.search(
        r"<!-- exclusions-json:\s*(\[.*\])\s*-->",
        text,
        re.DOTALL,
    )
    if match is None:
        raise TelegramCurationError("clean pilot exclusions metadata is missing")
    value = json.loads(match.group(1))
    if not isinstance(value, list):
        raise TelegramCurationError("clean pilot exclusions metadata is invalid")
    return [dict(item) for item in value]


def _clean_diversity(selected: list[dict[str, Any]]) -> dict[str, Any]:
    lengths = [
        sum(len(str(text)) for text in item["human_target"]["messages"])
        for item in selected
    ]
    bubbles = [
        len(item["human_target"]["messages"])
        for item in selected
    ]
    return {
        "selected": len(selected),
        "length_range": [min(lengths, default=0), max(lengths, default=0)],
        "bubble_count_range": [min(bubbles, default=0), max(bubbles, default=0)],
        "authorship": dict(
            sorted(
                Counter(
                    str(item["authorship_provenance"]["status"])
                    for item in selected
                ).items()
            )
        ),
        "text_normalized": False,
        "synthetic_examples": 0,
    }


def _write_confirmation_command(
    path: Path,
    *,
    authorship: Path,
    clean_pilot: Path,
    decisions: Path,
    fingerprint: str,
) -> None:
    stage3e_root = authorship.parent
    stage3c_root = stage3e_root.parent / (
        stage3e_root.name.removesuffix("-stage3e") + "-stage3c"
    )
    preview = stage3e_root.parent / stage3e_root.name.removesuffix("-stage3e")
    command = (
        "python -m conversation_agent dataset telegram-confirm-clean-pilot "
        f'--preview "{preview}" '
        f'--reconciliation "{stage3c_root / "reconciliation"}" '
        f'--authorship "{authorship}" '
        f'--clean-pilot "{clean_pilot}" '
        f'--authorship-decisions "{decisions}" '
        f"--fingerprint {fingerprint} --consent-confirmed"
    )
    path.write_text(command + "\n", encoding="utf-8")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True)
        for item in rows
    )
    path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def _write_csv(
    path: Path,
    fields: tuple[str, ...],
    rows: Iterable[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(item) for item in csv.DictReader(handle)]
