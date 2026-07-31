"""Stage 3C provenance reconciliation and private dataset curation."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from conversation_agent.local_slm.provenance_recovery import (
    discovery_report,
    load_discovered_locations,
    quote_identifier,
    readonly_connection,
)
from conversation_agent.local_slm.stage2_dataset import (
    stable_fingerprint,
)
from conversation_agent.local_slm.telegram_import import (
    compute_preview_fingerprint,
    load_raw_messages,
    parse_optional_datetime,
)
from conversation_agent.local_slm.telegram_privacy import scan_text

OriginClass = Literal[
    "human_confirmed",
    "ai_generated",
    "human_edited_ai",
    "unknown_historical",
    "conflicting_evidence",
]
AUTHORITATIVE_HUMAN = frozenset({"human_confirmed", "human_edited_ai"})
BATCH_DECISIONS = frozenset(
    {
        "include_human",
        "exclude_ai",
        "exclude_private",
        "needs_individual_review",
        "skip",
    }
)
PII_ACTIONS = frozenset(
    {"redact", "replace_with_alias", "exclude", "keep_with_explicit_approval"}
)
RECONCILIATION_FILES = (
    "manifest.json",
    "messages.reconciled.jsonl",
    "episodes.reconciled.jsonl",
    "contamination-report.json",
    "provenance-summary.json",
    "pii-records.jsonl",
)


class TelegramCurationError(ValueError):
    """Raised when a Stage 3C curation safety gate fails."""


@dataclass(frozen=True)
class AuditEvidence:
    source_alias: str
    record_id: str
    classification: OriginClass
    chat_id: int | None
    message_id: int | None
    timestamp: str | None
    text_hash: str | None
    evidence_kind: str
    provider: str | None = None
    model: str | None = None
    metadata: dict[str, Any] | None = None

    def safe_dict(self) -> dict[str, Any]:
        return {
            "source_alias": self.source_alias,
            "record_id": self.record_id,
            "classification": self.classification,
            "evidence_kind": self.evidence_kind,
            "provider": self.provider,
            "model": self.model,
            "metadata": self.metadata or {},
        }


@dataclass(frozen=True)
class ReconciliationOptions:
    preview: Path
    discovery: Path
    output: Path
    read_only: bool
    ai_operation_start: datetime | None = None


def reconcile_telegram_preview(options: ReconciliationOptions) -> dict[str, Any]:
    if not options.read_only:
        raise TelegramCurationError("--read-only is required")
    preview_fingerprint = _recorded_preview_fingerprint(options.preview)
    if compute_preview_fingerprint(options.preview) != preview_fingerprint:
        raise TelegramCurationError("Stage 3B preview fingerprint is invalid")
    report = discovery_report(options.discovery)
    locations = load_discovered_locations(options.discovery)
    evidence = load_audit_evidence(report, locations)
    raw_messages = load_raw_messages(
        options.preview / "raw-messages.private.jsonl"
    )
    reconciled_messages: list[dict[str, Any]] = []
    message_results: dict[int, dict[str, Any]] = {}
    outgoing = [item for item in raw_messages if item.direction == "outgoing"]
    for index, message in enumerate(outgoing):
        neighbors = outgoing[max(0, index - 2) : index] + outgoing[index + 1 : index + 3]
        result = reconcile_outgoing_message(
            message_id=message.message_id,
            chat_id=message.peer_id,
            timestamp=message.timestamp,
            text=message.content,
            evidence=evidence,
            original_classification=(
                message.provenance_classification or "unknown_historical"
            ),
            ai_operation_start=options.ai_operation_start,
            neighbor_texts=[item.content for item in neighbors],
        )
        reconciled_messages.append(result)
        message_results[message.message_id] = result
    episodes = _read_jsonl(options.preview / "episodes.preview.jsonl")
    reconciled_episodes = [
        reconcile_episode(item, message_results) for item in episodes
    ]
    stage3b_privacy = json.loads(
        (options.preview / "privacy-report.json").read_text(encoding="utf-8-sig")
    )
    pii_records = _build_pii_records(
        raw_messages,
        reconciled_episodes,
        expected_count=int(stage3b_privacy.get("findings", 0)),
    )
    counts = Counter(item["classification"] for item in reconciled_messages)
    episode_counts = Counter(
        item["stage3c"]["classification"] for item in reconciled_episodes
    )
    authoritative_id_matches = sum(
        item["match_method"] == "exact_chat_and_message_id"
        for item in reconciled_messages
    )
    authoritative_hash_matches = sum(
        item["match_method"] == "exact_hash_destination_time"
        for item in reconciled_messages
    )
    secondary_matches = sum(
        item["match_method"] == "secondary_only"
        for item in reconciled_messages
    )
    pii_count = sum(
        item["pii_type"] != "sensitive_self_harm" for item in pii_records
    )
    sensitive_count = sum(
        item["pii_type"] == "sensitive_self_harm" for item in pii_records
    )
    options.output.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "stage": "3C",
        "read_only": True,
        "source_preview_fingerprint": preview_fingerprint,
        "source_preview_modified": False,
        "discovery_databases": int(report.get("candidate_databases", 0)),
        "audit_evidence_records": len(evidence),
        "ai_operation_start": (
            options.ai_operation_start.isoformat()
            if options.ai_operation_start is not None
            else None
        ),
        "ai_operation_start_is_assumption": options.ai_operation_start is not None,
        "outgoing_messages": len(outgoing),
        "authoritative_id_matches": authoritative_id_matches,
        "authoritative_hash_destination_time_matches": authoritative_hash_matches,
        "timestamp_hash_secondary_matches": secondary_matches,
        "message_counts": dict(sorted(counts.items())),
        "episode_counts": dict(sorted(episode_counts.items())),
        "candidate_episodes_after_filtering": sum(
            item["stage3c"]["classification"] in AUTHORITATIVE_HUMAN
            or item["stage3c"]["classification"] == "unknown_historical"
            for item in reconciled_episodes
        ),
        "pii_findings": pii_count,
        "sensitive_content_findings": sensitive_count,
        "external_services_called": [],
        "llm_used": False,
        "training_performed": False,
        "reconciliation_fingerprint": None,
    }
    contamination = {
        "schema_version": 1,
        "message_counts": dict(sorted(counts.items())),
        "episode_counts": dict(sorted(episode_counts.items())),
        "pii_findings": pii_count,
        "sensitive_content_findings": sensitive_count,
        "confirmed_ai_excluded_from_human_targets": episode_counts.get(
            "ai_generated", 0
        ),
        "conflicts_require_review": episode_counts.get("conflicting_evidence", 0),
        "unknown_requires_review": episode_counts.get("unknown_historical", 0),
        "heuristics_are_authoritative": False,
        "ai_is_style_evidence": False,
    }
    provenance_summary = {
        "schema_version": 1,
        "databases": int(report.get("candidate_databases", 0)),
        "readable_databases": int(report.get("readable_databases", 0)),
        "audit_evidence_records": len(evidence),
        "authoritative_id_matches": authoritative_id_matches,
        "authoritative_hash_destination_time_matches": authoritative_hash_matches,
        "timestamp_hash_secondary_matches": secondary_matches,
        "message_counts": dict(sorted(counts.items())),
        "episode_counts": dict(sorted(episode_counts.items())),
    }
    _write_jsonl(
        options.output / "messages.reconciled.jsonl",
        reconciled_messages,
    )
    _write_jsonl(
        options.output / "episodes.reconciled.jsonl",
        reconciled_episodes,
    )
    _write_jsonl(options.output / "pii-records.jsonl", pii_records)
    _write_json(options.output / "manifest.json", manifest)
    _write_json(options.output / "contamination-report.json", contamination)
    _write_json(options.output / "provenance-summary.json", provenance_summary)
    fingerprint = reconciliation_fingerprint(options.output)
    manifest["reconciliation_fingerprint"] = fingerprint
    _write_json(options.output / "manifest.json", manifest)
    (options.output / "reconciliation-fingerprint.txt").write_text(
        fingerprint + "\n",
        encoding="utf-8",
    )
    return {
        "output": str(options.output),
        "fingerprint": fingerprint,
        "outgoing_messages": len(outgoing),
        "authoritative_id_matches": authoritative_id_matches,
        "authoritative_hash_destination_time_matches": authoritative_hash_matches,
        "timestamp_hash_secondary_matches": secondary_matches,
        "message_counts": dict(sorted(counts.items())),
        "episode_counts": dict(sorted(episode_counts.items())),
        "candidate_episodes_after_filtering": manifest[
            "candidate_episodes_after_filtering"
        ],
        "pii_findings": pii_count,
        "sensitive_content_findings": sensitive_count,
    }


def load_audit_evidence(
    report: dict[str, Any],
    locations: dict[str, Path],
) -> list[AuditEvidence]:
    evidence: list[AuditEvidence] = []
    for database in report.get("databases", []):
        if not isinstance(database, dict) or not database.get("sqlite_valid"):
            continue
        alias = str(database.get("alias", ""))
        path = locations.get(alias)
        if path is None or not path.is_file():
            continue
        classification = str(database.get("schema_classification", ""))
        if classification == "feedback_and_send_audit":
            evidence.extend(_feedback_database_evidence(path, alias))
        elif classification == "style_compiler":
            evidence.extend(_style_compiler_evidence(path, alias))
        else:
            evidence.extend(_generic_database_evidence(path, alias, database))
    return evidence


def reconcile_outgoing_message(
    *,
    message_id: int,
    chat_id: int | None,
    timestamp: str,
    text: str,
    evidence: Iterable[AuditEvidence],
    original_classification: str,
    ai_operation_start: datetime | None,
    neighbor_texts: Iterable[str] = (),
) -> dict[str, Any]:
    text_hash = _normalized_text_hash(text)
    message_time = parse_optional_datetime(timestamp)
    authoritative: list[tuple[AuditEvidence, str, float]] = []
    secondary: list[tuple[AuditEvidence, str, float]] = []
    for item in evidence:
        same_chat = (
            chat_id is not None
            and item.chat_id is not None
            and chat_id == item.chat_id
        )
        if (
            same_chat
            and item.message_id is not None
            and message_id == item.message_id
        ):
            authoritative.append((item, "exact_chat_and_message_id", 1.0))
            continue
        same_hash = bool(text_hash and item.text_hash and text_hash == item.text_hash)
        time_distance = _timestamp_distance_seconds(message_time, item.timestamp)
        if same_chat and same_hash and time_distance is not None and time_distance <= 86400:
            authoritative.append((item, "exact_hash_destination_time", 0.94))
            continue
        if same_hash:
            secondary.append((item, "text_hash_only", 0.45))
        elif same_chat and time_distance is not None and time_distance <= 30:
            secondary.append((item, "destination_time_only", 0.35))
    classes = {item.classification for item, _, _ in authoritative}
    conflict_reasons: list[str] = []
    if len(classes) > 1:
        classification: OriginClass = "conflicting_evidence"
        confidence = 1.0
        method = "conflicting_authoritative_records"
        conflict_reasons.append("authoritative_sources_disagree")
    elif classes:
        classification = cast(OriginClass, next(iter(classes)))
        strongest = max(authoritative, key=lambda item: item[2])
        confidence = strongest[2]
        method = strongest[1]
    else:
        classification = "unknown_historical"
        confidence = 0.0
        method = "secondary_only" if secondary else "no_match"
    temporal_signal = "not_provided"
    if ai_operation_start is not None and message_time is not None:
        temporal_signal = (
            "before_user_provided_ai_start"
            if message_time < ai_operation_start
            else "after_user_provided_ai_start"
        )
        if classification == "unknown_historical":
            confidence = 0.1
    flags, style_shift = heuristic_review_signals(text, neighbor_texts)
    matches = [*authoritative, *secondary]
    return {
        "record_id": _private_record_id(message_id),
        "telegram_message_id": message_id,
        "classification": classification,
        "confidence": round(confidence, 6),
        "match_method": method,
        "matched_record_ids": [item.record_id for item, _, _ in matches],
        "evidence_sources": [item.safe_dict() for item, _, _ in matches],
        "conflict_reasons": conflict_reasons,
        "timestamp": timestamp,
        "original_stage3b_provenance": original_classification,
        "temporal_signal": temporal_signal,
        "temporal_signal_is_authoritative": False,
        "review_priority": _review_priority(classification, flags, style_shift),
        "heuristic_flags": flags,
        "style_shift_score": style_shift,
        "heuristics_changed_classification": False,
    }


def reconcile_episode(
    episode: dict[str, Any],
    message_results: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    output = json.loads(json.dumps(episode))
    ids = [
        int(item)
        for item in episode.get("provenance", {}).get("message_ids", [])
        if str(item).isdigit()
    ]
    results = [message_results[item] for item in ids if item in message_results]
    classes = {item["classification"] for item in results}
    if "conflicting_evidence" in classes:
        classification: OriginClass = "conflicting_evidence"
    elif "ai_generated" in classes and classes & AUTHORITATIVE_HUMAN:
        classification = "conflicting_evidence"
    elif "ai_generated" in classes:
        classification = "ai_generated"
    elif classes and classes <= AUTHORITATIVE_HUMAN:
        classification = (
            "human_edited_ai"
            if "human_edited_ai" in classes
            else "human_confirmed"
        )
    else:
        classification = "unknown_historical"
    output["stage3c"] = {
        "classification": classification,
        "message_record_ids": [item["record_id"] for item in results],
        "confidence": min(
            (float(item["confidence"]) for item in results),
            default=0.0,
        ),
        "authoritative": classification in {
            "human_confirmed",
            "human_edited_ai",
            "ai_generated",
        },
        "heuristic_flags": sorted(
            {
                flag
                for item in results
                for flag in item.get("heuristic_flags", [])
            }
        ),
        "review_priority": max(
            (int(item.get("review_priority", 0)) for item in results),
            default=0,
        ),
    }
    output["positive_human_target"] = classification in AUTHORITATIVE_HUMAN
    output["style_evidence_eligible"] = classification in AUTHORITATIVE_HUMAN
    if classification == "ai_generated":
        output["quality_flags"] = sorted(
            {*output.get("quality_flags", []), "ai_contamination_excluded"}
        )
    elif classification == "conflicting_evidence":
        output["quality_flags"] = sorted(
            {*output.get("quality_flags", []), "conflicting_evidence_review_required"}
        )
    return output


def heuristic_review_signals(
    text: str,
    neighbor_texts: Iterable[str],
) -> tuple[list[str], float]:
    normalized = text.casefold()
    flags: list[str] = []
    patterns = {
        "conditional_offer": (
            r"\bif you want\b",
            r"\u0435\u0441\u043b\u0438 \u0445\u043e\u0447\u0435\u0448\u044c",
        ),
        "assistant_like_followup": (
            r"\bwhat exactly\b",
            r"\u0447\u0442\u043e \u0438\u043c\u0435\u043d\u043d\u043e",
            (
                r"\u043c\u043e\u0436\u0435\u043c "
                r"\u043f\u043e\u0433\u043e\u0432\u043e\u0440\u0438\u0442\u044c"
            ),
        ),
        "generic_safety_template": (
            r"\beverything (?:is|will be) okay\b",
            r"\u0432\u0441\u0435 \u0432 \u043f\u043e\u0440\u044f\u0434\u043a\u0435",
            (
                r"\u0442\u044b \u0432 "
                r"\u0431\u0435\u0437\u043e\u043f\u0430\u0441\u043d\u043e\u0441\u0442\u0438"
            ),
        ),
    }
    for flag, values in patterns.items():
        if any(re.search(pattern, normalized) for pattern in values):
            flags.append(flag)
    words = re.findall(r"[A-Za-z\u0400-\u04ff]+", text)
    if len(words) >= 14 and text.rstrip().endswith((".", "?", "!")):
        flags.append("highly_complete_punctuation")
    flags.extend(sensitive_content_flags(text))
    neighbors = [item for item in neighbor_texts if item.strip()]
    neighbor_lengths = [len(item) for item in neighbors]
    baseline = sum(neighbor_lengths) / len(neighbor_lengths) if neighbor_lengths else len(text)
    shift = min(1.0, abs(len(text) - baseline) / max(40.0, baseline))
    if shift >= 0.65:
        flags.append("abrupt_length_shift")
    return sorted(set(flags)), round(shift, 6)


def sensitive_content_flags(text: str) -> list[str]:
    patterns = (
        r"\b(?:suicide|suicidal|kill myself|self[- ]harm)\b",
        r"\u0443\u0431\u0438\u0442\u044c \u0441\u0435\u0431\u044f",
        (
            r"\u043f\u043e\u043a\u043e\u043d\u0447\u0438\u0442\u044c "
            r"\u0441 \u0441\u043e\u0431\u043e\u0439"
        ),
        r"\u043d\u0435 \u0445\u043e\u0447\u0443 \u0436\u0438\u0442\u044c",
        r"\u0441\u0443\u0438\u0446\u0438\u0434",
    )
    normalized = text.casefold()
    return (
        ["sensitive_self_harm"]
        if any(re.search(pattern, normalized) for pattern in patterns)
        else []
    )


def reconciliation_fingerprint(path: Path) -> str:
    payload: dict[str, Any] = {}
    for name in RECONCILIATION_FILES:
        file_path = path / name
        if not file_path.is_file():
            raise TelegramCurationError(f"reconciliation file is missing: {name}")
        if name.endswith(".jsonl"):
            value: Any = _read_jsonl(file_path)
        else:
            value = json.loads(file_path.read_text(encoding="utf-8-sig"))
        if name == "manifest.json":
            value.pop("reconciliation_fingerprint", None)
        payload[name] = value
    return stable_fingerprint(payload)


def _feedback_database_evidence(path: Path, alias: str) -> list[AuditEvidence]:
    output: list[AuditEvidence] = []
    with readonly_connection(path) as connection:
        names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "generated_replies" not in names:
            return output
        rows = connection.execute(
            """
            SELECT id, dialog_id, sent_message_id, created_at, sent_at,
                   model, prompt_version, generated_reply_text,
                   corrected_reply_text, feedback_status, delivery_status
            FROM generated_replies
            """
        )
        for row in rows:
            record_id = f"{alias}:generated_replies:{row['id']}"
            sent_id = _optional_int(row["sent_message_id"])
            if sent_id is not None and str(row["delivery_status"]) == "sent":
                output.append(
                    AuditEvidence(
                        source_alias=alias,
                        record_id=record_id,
                        classification="ai_generated",
                        chat_id=_optional_int(row["dialog_id"]),
                        message_id=sent_id,
                        timestamp=_optional_text(row["sent_at"] or row["created_at"]),
                        text_hash=_normalized_text_hash(
                            str(row["generated_reply_text"] or "")
                        ),
                        evidence_kind="generated_reply_send_audit",
                        model=_optional_text(row["model"]),
                        metadata={
                            "prompt_version": _optional_text(row["prompt_version"]),
                            "feedback_status": _optional_text(row["feedback_status"]),
                        },
                    )
                )
            corrected = _optional_text(row["corrected_reply_text"])
            if corrected:
                output.append(
                    AuditEvidence(
                        source_alias=alias,
                        record_id=record_id + ":correction",
                        classification="human_edited_ai",
                        chat_id=_optional_int(row["dialog_id"]),
                        message_id=None,
                        timestamp=_optional_text(row["sent_at"] or row["created_at"]),
                        text_hash=_normalized_text_hash(corrected),
                        evidence_kind="trainer_human_correction",
                        model=_optional_text(row["model"]),
                        metadata={"feedback_status": "corrected"},
                    )
                )
    return output


def _style_compiler_evidence(path: Path, alias: str) -> list[AuditEvidence]:
    output: list[AuditEvidence] = []
    with readonly_connection(path) as connection:
        names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "source_analysis" not in names:
            return output
        rows = connection.execute(
            """
            SELECT source_key, source_type, contact_id, content_hash,
                   compiled_at, example_json
            FROM source_analysis
            """
        )
        for row in rows:
            source_type = str(row["source_type"] or "").casefold()
            classification = _source_type_classification(source_type)
            if classification is None:
                continue
            example = _json_object(row["example_json"])
            response_text = _optional_text(example.get("response_text"))
            timestamp = _optional_text(example.get("created_at")) or _optional_text(
                row["compiled_at"]
            )
            chat_id = _optional_int(example.get("contact_id") or row["contact_id"])
            output.append(
                AuditEvidence(
                    source_alias=alias,
                    record_id=(
                        f"{alias}:source_analysis:"
                        + hashlib.sha256(
                            str(row["source_key"]).encode("utf-8")
                        ).hexdigest()[:16]
                    ),
                    classification=classification,
                    chat_id=chat_id,
                    message_id=_optional_int(
                        example.get("sent_message_id")
                        or example.get("telegram_message_id")
                    ),
                    timestamp=timestamp,
                    text_hash=(
                        _normalized_text_hash(response_text)
                        if response_text
                        else _optional_text(row["content_hash"])
                    ),
                    evidence_kind=f"style_source:{source_type}",
                    metadata={
                        "source_type": source_type,
                        "feedback_status": _optional_text(
                            example.get("feedback_status")
                        ),
                    },
                )
            )
    return output


def _generic_database_evidence(
    path: Path,
    alias: str,
    database_report: dict[str, Any],
) -> list[AuditEvidence]:
    output: list[AuditEvidence] = []
    with readonly_connection(path) as connection:
        for table in database_report.get("tables", []):
            if not isinstance(table, dict):
                continue
            name = str(table.get("name", ""))
            columns = [str(item) for item in table.get("columns", [])]
            origin_column = _first_column(
                columns,
                "origin",
                "provenance",
                "source_type",
                "event_type",
                "action",
                "status",
            )
            if origin_column is None:
                continue
            selected = {
                item
                for item in (
                    _first_column(columns, "id", "record_id", "event_id"),
                    _first_column(
                        columns,
                        "dialog_id",
                        "chat_id",
                        "contact_id",
                        "destination_id",
                    ),
                    _first_column(
                        columns,
                        "sent_message_id",
                        "telegram_message_id",
                        "message_id",
                    ),
                    _first_column(
                        columns,
                        "created_at",
                        "sent_at",
                        "timestamp",
                        "updated_at",
                    ),
                    _first_column(
                        columns,
                        "text",
                        "message_text",
                        "response_text",
                        "final_text",
                        "corrected_reply_text",
                    ),
                    _first_column(columns, "text_hash", "content_hash", "draft_hash"),
                    _first_column(columns, "provider"),
                    _first_column(columns, "model", "model_id"),
                    origin_column,
                )
                if item is not None
            }
            if not selected:
                continue
            query = ", ".join(quote_identifier(item) for item in sorted(selected))
            rows = connection.execute(
                f"SELECT {query} FROM {quote_identifier(name)} LIMIT 100000"
            )
            for index, row in enumerate(rows, start=1):
                origin_value = str(row[origin_column] or "")
                classification = _origin_value_classification(origin_value)
                if classification is None:
                    continue
                record_column = _first_column(columns, "id", "record_id", "event_id")
                record_value = row[record_column] if record_column else index
                text_column = _first_column(
                    columns,
                    "text",
                    "message_text",
                    "response_text",
                    "final_text",
                    "corrected_reply_text",
                )
                hash_column = _first_column(
                    columns,
                    "text_hash",
                    "content_hash",
                    "draft_hash",
                )
                text_hash = (
                    _normalized_text_hash(str(row[text_column] or ""))
                    if text_column
                    else _optional_text(row[hash_column]) if hash_column else None
                )
                output.append(
                    AuditEvidence(
                        source_alias=alias,
                        record_id=f"{alias}:{name}:{record_value}",
                        classification=classification,
                        chat_id=_row_optional_int(
                            row,
                            _first_column(
                                columns,
                                "dialog_id",
                                "chat_id",
                                "contact_id",
                                "destination_id",
                            ),
                        ),
                        message_id=_row_optional_int(
                            row,
                            _first_column(
                                columns,
                                "sent_message_id",
                                "telegram_message_id",
                                "message_id",
                            ),
                        ),
                        timestamp=_row_optional_text(
                            row,
                            _first_column(
                                columns,
                                "created_at",
                                "sent_at",
                                "timestamp",
                                "updated_at",
                            ),
                        ),
                        text_hash=text_hash,
                        evidence_kind=f"generic_audit:{name}",
                        provider=_row_optional_text(
                            row, _first_column(columns, "provider")
                        ),
                        model=_row_optional_text(
                            row, _first_column(columns, "model", "model_id")
                        ),
                    )
                )
    return output


def _build_pii_records(
    raw_messages: Iterable[Any],
    episodes: list[dict[str, Any]],
    *,
    expected_count: int = 0,
) -> list[dict[str, Any]]:
    message_to_episodes: dict[str, list[str]] = {}
    for episode in episodes:
        for message_id in episode.get("provenance", {}).get("message_ids", []):
            message_to_episodes.setdefault(str(message_id), []).append(
                str(episode["example_id"])
            )
    records: list[dict[str, Any]] = []
    for message in raw_messages:
        findings = scan_text(message.content)
        for index, finding in enumerate(findings):
            records.append(
                {
                    "record_id": (
                        _private_record_id(message.message_id)
                        + f"-pii-{index + 1:02d}"
                    ),
                    "message_record_id": _private_record_id(message.message_id),
                    "episode_ids": message_to_episodes.get(
                        str(message.message_id), []
                    ),
                    "pii_type": finding.kind,
                    "suggested_action": _suggested_pii_action(finding.kind),
                }
            )
    existing = {
        (episode_id, str(item["pii_type"]))
        for item in records
        for episode_id in item.get("episode_ids", [])
    }
    preview_only: list[tuple[str, str]] = []
    for episode in episodes:
        episode_id = str(episode["example_id"])
        for pii_type in episode.get("privacy", {}).get("redactions", []):
            key = (episode_id, str(pii_type))
            if key in existing:
                continue
            preview_only.append(key)
    preview_only.sort(key=lambda item: (item[1] != "private_name", item))
    for episode_id, pii_type in preview_only:
        if expected_count and len(records) >= expected_count:
            break
        key = (episode_id, pii_type)
        if key in existing:
            continue
        records.append(
            {
                "record_id": (
                    "preview-pii-"
                    + hashlib.sha256(
                        f"{episode_id}:{pii_type}".encode()
                    ).hexdigest()[:12]
                ),
                "message_record_id": None,
                "episode_ids": [episode_id],
                "pii_type": pii_type,
                "suggested_action": _suggested_pii_action(pii_type),
            }
        )
        existing.add(key)
    for episode in episodes:
        episode_id = str(episode["example_id"])
        text = " ".join(
            [
                *episode.get("incoming", {}).get("messages", []),
                *episode.get("human_target", {}).get("messages", []),
            ]
        )
        if not sensitive_content_flags(text):
            continue
        records.append(
            {
                "record_id": (
                    "sensitive-"
                    + hashlib.sha256(episode_id.encode()).hexdigest()[:12]
                ),
                "message_record_id": None,
                "episode_ids": [episode_id],
                "pii_type": "sensitive_self_harm",
                "suggested_action": "exclude",
            }
        )
    return records


def _source_type_classification(value: str) -> OriginClass | None:
    if value in {"human_matvey", "human_manual", "human_confirmed"}:
        return "human_confirmed"
    if value in {"fix", "human_fix", "human_edit", "human_edited_ai"}:
        return "human_edited_ai"
    if value in {"approved_ai", "rejected", "ai_generated", "model_rejected"}:
        return "ai_generated"
    return None


def _origin_value_classification(value: str) -> OriginClass | None:
    normalized = value.casefold().replace("-", "_").replace(" ", "_")
    if any(
        token in normalized
        for token in ("human_takeover", "human_confirmed", "manual_reply")
    ):
        return "human_confirmed"
    if any(
        token in normalized
        for token in ("human_edit", "human_fix", "corrected_by_human")
    ):
        return "human_edited_ai"
    if any(
        token in normalized
        for token in ("ai_generated", "model_generated", "generated_reply")
    ):
        return "ai_generated"
    return None


def _review_priority(
    classification: OriginClass,
    flags: list[str],
    style_shift: float,
) -> int:
    if classification == "conflicting_evidence":
        return 100
    if classification != "unknown_historical":
        return 0
    return min(99, 25 + len(flags) * 15 + math.floor(style_shift * 20))


def _recorded_preview_fingerprint(preview: Path) -> str:
    path = preview / "preview-fingerprint.txt"
    if not path.is_file():
        raise TelegramCurationError("Stage 3B preview fingerprint is missing")
    return path.read_text(encoding="utf-8-sig").strip()


def _normalized_text_hash(value: str) -> str:
    normalized = " ".join(value.casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def _timestamp_distance_seconds(
    message_time: datetime | None,
    evidence_timestamp: str | None,
) -> float | None:
    if message_time is None or not evidence_timestamp:
        return None
    evidence_time = parse_optional_datetime(evidence_timestamp)
    if evidence_time is None:
        return None
    return abs((message_time - evidence_time).total_seconds())


def _suggested_pii_action(kind: str) -> str:
    if kind in {"private_name", "telegram_username", "numeric_id"}:
        return "replace_with_alias"
    if kind in {
        "api_key",
        "bank_details",
        "bot_token",
        "password",
        "passport",
        "payment_card",
        "secret_url",
        "session_string",
    }:
        return "exclude"
    return "redact"


def _private_record_id(message_id: int) -> str:
    return "record-" + hashlib.sha256(str(message_id).encode("ascii")).hexdigest()[:12]


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _first_column(columns: Iterable[str], *candidates: str) -> str | None:
    available = {item.casefold(): item for item in columns}
    return next((available[item] for item in candidates if item in available), None)


def _row_optional_int(row: sqlite3.Row, column: str | None) -> int | None:
    return _optional_int(row[column]) if column else None


def _row_optional_text(row: sqlite3.Row, column: str | None) -> str | None:
    return _optional_text(row[column]) if column else None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    parsed = str(value)
    return parsed if parsed else None


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


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
