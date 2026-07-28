"""Safe trainer-card rendering and compact callback payloads."""

from __future__ import annotations

import json
from html import escape

from conversation_agent.storage.models import GeneratedReplyRecord

BAD_CATEGORIES = (
    "too_formal",
    "too_informal",
    "too_long",
    "too_short",
    "wrong_tone",
    "wrong_facts",
    "invented_information",
    "missed_context",
    "bad_emoji",
    "unnatural_phrase",
    "other",
)


def callback_data(action: str, reply_id: int, value: str | None = None) -> str:
    parts = [action, str(reply_id)]
    if value:
        parts.append(value)
    payload = ":".join(parts)
    if len(payload.encode("utf-8")) > 64:
        raise ValueError("Callback payload exceeds Telegram's 64-byte limit")
    return payload


def parse_callback(payload: str) -> tuple[str, int, str | None] | None:
    parts = payload.split(":", 2)
    if len(parts) < 2 or not parts[1].isdigit():
        return None
    return parts[0], int(parts[1]), parts[2] if len(parts) == 3 else None


def review_card(record: GeneratedReplyRecord) -> str:
    status = _status_label(record)
    incoming = _truncate(record.incoming_message_text or _incoming_from_context(record), 1100)
    reply = _truncate(record.generated_reply_text, 1500)
    analysis = _object(record.analyzer_json)
    goal = _object(record.goal_json)
    behavior = _object(record.behavior_plan_json)
    fields = [
        "<b>Conversation Agent review</b>",
        f"<b>Draft ID:</b> {record.id}",
        f"<b>Contact:</b> {record.dialog_id}",
        f"<b>Incoming:</b>\n{escape(incoming)}",
        f"<b>Intent:</b> {escape(str(analysis.get('intent', '-')))}",
        f"<b>Stage:</b> {escape(str(analysis.get('conversation_stage', '-')))}",
        f"<b>Goal:</b> {escape(str(goal.get('goal', '-')))}",
        f"<b>Proposed messages:</b>\n{escape(reply or '(no reply)')}",
        f"<b>Timing:</b> {_timing_summary(behavior)}",
        f"<b>Confidence:</b> {record.confidence:.2f}",
        f"<b>Handoff:</b> {'required' if record.handoff_required else 'no'}",
        f"<b>Delivery:</b> {escape(record.delivery_status)}",
        f"<b>Review:</b> {escape(status)}",
        f"<b>Provider:</b> {escape(record.provider)}",
        f"<b>Model:</b> {escape(record.model)}",
        f"<b>Prompt:</b> {escape(record.prompt_version)}",
        f"<b>Generated:</b> {escape(record.created_at)}",
    ]
    rendered = "\n\n".join(fields)
    if record.feedback_status == "corrected" and record.corrected_reply_text:
        correction = _truncate(record.corrected_reply_text, 700)
        rendered += f"\n\n<b>Correction:</b>\n{escape(correction)}"
    return rendered


def details_text(record: GeneratedReplyRecord) -> str:
    inspection = _object(record.prompt_inspection_json)
    analysis = _object(record.analyzer_json)
    goal = _object(record.goal_json)
    return "\n".join(
        (
            f"Draft ID: {record.id}",
            f"Reply ID: {record.id}",
            f"Incoming message ID: {record.incoming_message_id}",
            f"Sent message ID: {record.sent_message_id or '-'}",
            f"Draft status: {record.draft_status or '-'}",
            f"Delivery: {record.delivery_status}",
            f"Review: {record.feedback_status}",
            f"Category: {record.feedback_category or '-'}",
            f"Source: {record.feedback_source or '-'}",
            f"Intent: {analysis.get('intent', '-')}",
            f"Stage: {analysis.get('conversation_stage', '-')}",
            f"Active goal: {goal.get('goal', '-')}",
            f"Retrieved examples: {inspection.get('retrieved_example_count', 0)}",
            f"Retrieved Fix: {inspection.get('retrieved_fix_count', 0)}",
            f"Prompt tokens (estimated): {inspection.get('estimated_tokens', '-')}",
            f"Provider: {record.provider}",
            f"Model: {record.model}",
            f"Prompt: {record.prompt_version}",
            f"Generated: {record.created_at}",
            f"Sent: {record.sent_at or '-'}",
        )
    )


def review_keyboard(reply_id: int) -> list[list[tuple[str, str]]]:
    return [
        [
            ("✅ Approve", callback_data("approve", reply_id)),
            ("✏️ Fix", callback_data("fix", reply_id)),
            ("❌ Reject", callback_data("reject", reply_id)),
        ],
        [
            ("👤 Handoff", callback_data("handoff", reply_id)),
            ("⏸ Skip", callback_data("skip", reply_id)),
            ("ℹ️ Details", callback_data("details", reply_id)),
        ],
    ]


def category_keyboard(reply_id: int) -> list[list[tuple[str, str]]]:
    rows: list[list[tuple[str, str]]] = []
    for index in range(0, len(BAD_CATEGORIES), 2):
        rows.append(
            [
                (category.replace("_", " "), callback_data("cat", reply_id, category))
                for category in BAD_CATEGORIES[index : index + 2]
            ]
        )
    rows.append([("Cancel", callback_data("cancel", reply_id))])
    return rows


def _truncate(text: str, limit: int) -> str:
    normalized = text.strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 14].rstrip() + "\n[truncated]"


def _incoming_from_context(record: GeneratedReplyRecord) -> str:
    try:
        turns = json.loads(record.context_json)
    except (json.JSONDecodeError, TypeError):
        return ""
    for turn in reversed(turns):
        if isinstance(turn, dict) and turn.get("role") == "user":
            return str(turn.get("text", ""))
    return ""


def _status_label(record: GeneratedReplyRecord) -> str:
    if record.feedback_status == "approved":
        return "Approved"
    if record.feedback_status == "corrected":
        return "Corrected"
    if record.feedback_status == "rejected" and record.feedback_category == "should_not_reply":
        return "Should not have replied"
    if record.feedback_status == "rejected":
        category = record.feedback_category or "unspecified"
        return f"Rejected ({category})"
    return "Awaiting review"


def _object(value: str) -> dict[str, object]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _timing_summary(value: dict[str, object]) -> str:
    messages = value.get("messages", [])
    bubble_count = len(messages) if isinstance(messages, list) else 0
    read_delay = int(str(value.get("initial_read_delay_ms", 0) or 0))
    typing = int(str(value.get("typing_duration_ms", 0) or 0))
    return f"read {read_delay} ms; typing {typing} ms; bubbles {bubble_count}"
