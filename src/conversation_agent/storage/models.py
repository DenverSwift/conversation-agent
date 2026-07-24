"""Data models shared by feedback storage implementations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NewGeneratedReply:
    dialog_id: int
    incoming_message_id: int
    created_at: str
    model: str
    prompt_version: str
    generated_reply_text: str
    context_json: str


@dataclass(frozen=True)
class FeedbackUpdate:
    status: str
    updated_at: str
    category: str | None = None
    comment: str | None = None
    corrected_reply_text: str | None = None


@dataclass(frozen=True)
class GeneratedReplyRecord:
    id: int
    dialog_id: int
    incoming_message_id: int
    sent_message_id: int | None
    created_at: str
    sent_at: str | None
    model: str
    prompt_version: str
    generated_reply_text: str
    context_json: str
    delivery_status: str
    feedback_status: str
    feedback_category: str | None
    feedback_comment: str | None
    corrected_reply_text: str | None
    feedback_updated_at: str | None
