"""Export provider-independent feedback without uploading or training."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from conversation_agent.storage.models import GeneratedReplyRecord
from conversation_agent.training.cleaning import redact_text


def write_feedback_exports(
    *,
    output_directory: Path,
    records: list[GeneratedReplyRecord],
    redact_pii: bool,
) -> dict[str, Any]:
    output_directory.mkdir(parents=True, exist_ok=True)
    redaction_counts = {"email": 0, "phone": 0, "url": 0, "secret": 0}
    reviewed: list[dict[str, Any]] = []
    positive: list[dict[str, Any]] = []
    negative: list[dict[str, Any]] = []

    for record in records:
        item = _reviewed_item(record, redact_pii, redaction_counts)
        reviewed.append(item)
        if record.feedback_status in {"approved", "corrected"}:
            positive.append(
                {
                    **item,
                    "target_reply": item["preferred_reply"],
                }
            )
        if record.feedback_status == "rejected":
            negative.append(item)

    _write_dict_jsonl(output_directory / "reviewed_feedback.jsonl", reviewed)
    _write_dict_jsonl(output_directory / "feedback_positive.jsonl", positive)
    _write_dict_jsonl(output_directory / "feedback_negative.jsonl", negative)

    summary: dict[str, Any] = {
        "reviewed_replies": len(reviewed),
        "positive_examples": len(positive),
        "negative_examples": len(negative),
        "corrected_examples": sum(
            record.feedback_status == "corrected" for record in records
        ),
        "redaction_counts": redaction_counts,
        "redact_pii": redact_pii,
        "export_timestamp": datetime.now(UTC).isoformat(),
    }
    (output_directory / "feedback_export_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def _reviewed_item(
    record: GeneratedReplyRecord,
    redact_pii: bool,
    counts: dict[str, int],
) -> dict[str, Any]:
    context = json.loads(record.context_json)
    original_reply = record.generated_reply_text
    corrected_reply = record.corrected_reply_text
    if redact_pii:
        context = [
            {
                "role": str(turn["role"]),
                "text": redact_text(str(turn["text"]), counts),
            }
            for turn in context
        ]
        original_reply = redact_text(original_reply, counts)
        if corrected_reply is not None:
            corrected_reply = redact_text(corrected_reply, counts)

    preferred_reply: str | None = None
    if record.feedback_status == "approved":
        preferred_reply = original_reply
    elif record.feedback_status == "corrected":
        preferred_reply = corrected_reply

    return {
        "reply_id": record.id,
        "dialog_id": record.dialog_id,
        "incoming_message_id": record.incoming_message_id,
        "sent_message_id": record.sent_message_id,
        "prompt_version": record.prompt_version,
        "model": record.model,
        "context": context,
        "original_generated_reply": original_reply,
        "preferred_reply": preferred_reply,
        "feedback_status": record.feedback_status,
        "feedback_category": record.feedback_category,
        "feedback_comment": record.feedback_comment,
        "feedback_source": record.feedback_source or "saved_messages",
        "feedback_trainer_user_id": record.feedback_trainer_user_id,
        "created_at": record.created_at,
    }


def _write_dict_jsonl(path: Path, items: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
