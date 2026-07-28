"""Provider boundary for structured conversation intelligence and generation."""

from __future__ import annotations

import json
from typing import Any, Protocol


class StructuredLLMProvider(Protocol):
    async def generate_structured(
        self,
        *,
        model: str,
        instructions: str,
        input_messages: list[dict[str, str]],
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Return one application-validated JSON object."""
        ...


class OpenAIConversationClient:
    """OpenAI Responses API implementation with stateless structured output."""

    provider_name = "openai"

    def __init__(self, *, api_key: str, timeout_seconds: float) -> None:
        from openai import AsyncOpenAI

        self._client: Any = AsyncOpenAI(
            api_key=api_key,
            timeout=timeout_seconds,
        )

    async def generate_structured(
        self,
        *,
        model: str,
        instructions: str,
        input_messages: list[dict[str, str]],
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        response: Any = await self._client.responses.create(
            model=model,
            instructions=instructions,
            input=input_messages,
            text={
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": schema,
                    "strict": True,
                }
            },
            store=False,
        )
        if getattr(response, "status", None) != "completed":
            raise RuntimeError(
                f"OpenAI structured response did not complete: "
                f"{getattr(response, 'status', 'unknown')}"
            )
        output_text = str(getattr(response, "output_text", "") or "").strip()
        if not output_text:
            raise RuntimeError("OpenAI structured response was empty")
        value = json.loads(output_text)
        if not isinstance(value, dict):
            raise TypeError("OpenAI structured response must be a JSON object")
        return value


class DeterministicFakeProvider:
    """Offline provider used by simulation and tests."""

    provider_name = "deterministic_fake"

    async def generate_structured(
        self,
        *,
        model: str,
        instructions: str,
        input_messages: list[dict[str, str]],
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        del model, instructions, schema
        text = " ".join(item["content"] for item in input_messages).lower()
        if schema_name == "interaction_analysis":
            return _fake_analysis(text)
        if schema_name == "response_plan":
            return _fake_response(text)
        raise ValueError(f"Unsupported fake schema: {schema_name}")


def _fake_analysis(text: str) -> dict[str, Any]:
    if any(word in text for word in ("спам", "не интересно", "unsubscribe")):
        return {
            "should_reply": False,
            "intent": "not_relevant",
            "interaction_mode": "business_inquiry",
            "conversation_stage": "not_relevant",
            "urgency": 0.1,
            "sentiment": "neutral",
            "needs_empathy": False,
            "needs_human_handoff": False,
            "missing_information": [],
            "recommended_goal": "do_not_reply",
            "confidence": 0.9,
        }
    intent = "asks_about_services"
    missing = ["timeline"]
    goal = "qualify_timeline"
    if not any(word in text for word in ("бот", "automation", "автомат", "сайт", "ai")):
        intent = "general_business_inquiry"
        missing = ["client_task", "timeline"]
        goal = "ask_clarifying_question"
    return {
        "should_reply": True,
        "intent": intent,
        "interaction_mode": "business_inquiry",
        "conversation_stage": "discovery",
        "urgency": 0.4,
        "sentiment": "neutral",
        "needs_empathy": False,
        "needs_human_handoff": False,
        "missing_information": missing,
        "recommended_goal": goal,
        "confidence": 0.78,
    }


def _fake_response(text: str) -> dict[str, Any]:
    if '"goal": "do_not_reply"' in text:
        return {
            "should_reply": False,
            "messages": [],
            "tone": "neutral",
            "goal": "do_not_reply",
            "handoff_required": False,
            "confidence": 0.9,
        }
    if '"goal": "qualify_timeline"' in text:
        messages = ["Да, с таким можем помочь", "Какие сроки по проекту?"]
        goal = "qualify_timeline"
    else:
        messages = ["Привет", "Расскажи подробнее, что именно нужно сделать?"]
        goal = "ask_clarifying_question"
    return {
        "should_reply": True,
        "messages": messages,
        "tone": "informal_professional",
        "goal": goal,
        "handoff_required": False,
        "confidence": 0.76,
    }
