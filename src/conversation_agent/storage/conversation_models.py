"""Persistence records for approval-first conversation processing."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NewAgentDraft:
    conversation_id: str
    contact_id: str
    message_group_id: str
    incoming_message_id: int
    incoming_message_text: str
    created_at: str
    model: str
    prompt_version: str
    generated_reply_text: str
    context_json: str
    analyzer_json: str
    goal_json: str
    response_json: str
    behavior_plan_json: str
    prompt_inspection_json: str
    prompt_fingerprint: str
    confidence: float
    handoff_required: bool
    provider: str = "openai"


@dataclass(frozen=True)
class AgentDraftRecord:
    id: int
    conversation_id: str
    contact_id: str
    message_group_id: str
    incoming_message_id: int
    behavior_plan_id: int | None
    status: str
    created_at: str
    updated_at: str
    analyzer_json: str
    goal_json: str
    response_json: str
    behavior_plan_json: str
    prompt_inspection_json: str
    prompt_fingerprint: str
    confidence: float
    handoff_required: bool
    approved_by: str | None
    approved_at: str | None


@dataclass(frozen=True)
class ApprovalAction:
    id: int
    draft_id: int
    action: str
    payload_text: str | None
    created_at: str
