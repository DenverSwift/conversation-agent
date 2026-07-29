"""Validation for local Telegram generation output."""

from __future__ import annotations

from conversation_agent.local_slm.models import GenerationResult, ValidationResult

FORBIDDEN_PHRASES = (
    "as an ai",
    "как искусственный интеллект",
    "я не могу помочь",
)


class OutputValidator:
    def __init__(self, *, max_bubble_count: int = 4, max_message_length: int = 700) -> None:
        self.max_bubble_count = max_bubble_count
        self.max_message_length = max_message_length

    def validate(
        self,
        result: GenerationResult,
        *,
        stale: bool = False,
        takeover: bool = False,
        paused: bool = False,
        approval_required: bool = True,
    ) -> ValidationResult:
        errors: list[str] = []
        if result.action not in {"reply", "no_reply", "wait", "reaction", "handoff"}:
            errors.append("invalid_action")
        if stale:
            errors.append("stale_draft")
        if takeover:
            errors.append("human_takeover")
        if paused:
            errors.append("dialog_paused")
        if not approval_required:
            errors.append("approval_required_missing")
        messages = tuple(item.strip() for item in result.messages if item.strip())
        if result.action == "reply" and not messages:
            errors.append("empty_reply")
        if result.action != "reply" and messages:
            errors.append("messages_for_non_reply")
        if len(messages) > self.max_bubble_count:
            errors.append("too_many_bubbles")
        if len(set(messages)) != len(messages):
            errors.append("duplicate_messages")
        for message in messages:
            lowered = message.lower()
            if len(message) > self.max_message_length:
                errors.append("message_too_long")
            if any(phrase in lowered for phrase in FORBIDDEN_PHRASES):
                errors.append("forbidden_phrase")
            if "<think" in lowered or "</think" in lowered or "reasoning_content" in lowered:
                errors.append("reasoning_output")
            if "http://" in lowered or "https://" in lowered:
                errors.append("forbidden_link")
        raw_lowered = result.raw_output.lower()
        if "<think" in raw_lowered or "</think" in raw_lowered or "reasoning_content" in raw_lowered:
            errors.append("reasoning_output")
        normalized = GenerationResult(
            action=result.action,
            messages=messages[: self.max_bubble_count],
            reaction=result.reaction,
            handoff_required=result.handoff_required or result.action == "handoff",
            confidence=max(0.0, min(1.0, result.confidence)),
            provider=result.provider,
            backend=result.backend,
            model=result.model,
            raw_output=result.raw_output,
            latency_ms=result.latency_ms,
            ttft_ms=result.ttft_ms,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
            tokens_per_second=result.tokens_per_second,
            retry_count=result.retry_count,
        )
        return ValidationResult(valid=not errors, errors=tuple(sorted(set(errors))), normalized=normalized)
