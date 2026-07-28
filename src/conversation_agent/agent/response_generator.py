"""Structured response planning separated from Telegram delivery behavior."""

from __future__ import annotations

import json
from typing import Any

from conversation_agent.domain.models import GeneratedResponse, GoalPlan, PromptPackage
from conversation_agent.llm.conversation_client import StructuredLLMProvider

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "should_reply": {"type": "boolean"},
        "messages": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 8,
        },
        "tone": {"type": "string"},
        "goal": {
            "type": "string",
            "enum": [
                "acknowledge",
                "ask_clarifying_question",
                "qualify_budget",
                "qualify_timeline",
                "explain_service",
                "provide_portfolio",
                "handle_objection",
                "propose_call",
                "wait",
                "do_not_reply",
                "handoff_to_human",
            ],
        },
        "handoff_required": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "should_reply",
        "messages",
        "tone",
        "goal",
        "handoff_required",
        "confidence",
    ],
    "additionalProperties": False,
}

GENERATOR_TASK = """Generate the next response plan for a Telegram business
conversation. Follow the supplied identity, business restrictions, active goal,
style evidence, and recent conversation. Use only known business facts. Do not
grant discounts, promise dates, make legal commitments, or fabricate links.
Messages must be natural Telegram bubbles, not assistant explanations. A valid
result may set should_reply=false with an empty messages list."""


class ResponseGenerator:
    def __init__(
        self,
        provider: StructuredLLMProvider,
        *,
        model: str,
        max_bubble_count: int,
        max_message_length: int,
    ) -> None:
        self.provider = provider
        self.provider_name = str(
            getattr(provider, "provider_name", provider.__class__.__name__)
        )
        self.model = model
        self.max_bubble_count = max_bubble_count
        self.max_message_length = max_message_length

    async def generate(
        self,
        prompt: PromptPackage,
        goal: GoalPlan,
    ) -> GeneratedResponse:
        if goal.goal in {"do_not_reply", "wait"}:
            return GeneratedResponse(
                should_reply=False,
                messages=(),
                tone="neutral",
                goal=goal.goal,
                handoff_required=goal.handoff_required,
                confidence=1.0,
            )
        value = await self.provider.generate_structured(
            model=self.model,
            instructions=prompt.instructions + "\n\n[GENERATION TASK]\n" + GENERATOR_TASK,
            input_messages=[
                *prompt.input_messages,
                {
                    "role": "user",
                    "content": json.dumps({"goal": goal.goal}, ensure_ascii=False),
                },
            ],
            schema_name="response_plan",
            schema=RESPONSE_SCHEMA,
        )
        response = GeneratedResponse.from_dict(value)
        messages = _normalize_bubbles(
            response.messages,
            max_count=self.max_bubble_count,
            max_length=self.max_message_length,
        )
        return GeneratedResponse(
            should_reply=response.should_reply and bool(messages),
            messages=messages,
            tone=response.tone,
            goal=response.goal,
            handoff_required=response.handoff_required or goal.handoff_required,
            confidence=response.confidence,
        )


def _normalize_bubbles(
    messages: tuple[str, ...],
    *,
    max_count: int,
    max_length: int,
) -> tuple[str, ...]:
    bubbles: list[str] = []
    for message in messages:
        normalized = " ".join(message.split()).strip()
        while len(normalized) > max_length and len(bubbles) < max_count:
            split_at = normalized.rfind(" ", 0, max_length + 1)
            if split_at <= 0:
                split_at = max_length
            bubbles.append(normalized[:split_at].strip())
            normalized = normalized[split_at:].strip()
        if normalized and len(bubbles) < max_count:
            bubbles.append(normalized)
        if len(bubbles) >= max_count:
            break
    return tuple(bubbles)
