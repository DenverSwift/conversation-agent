"""Bounded OpenAI analysis for offline style compilation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any

from conversation_agent.style.models import StyleExample, StyleRule


class OpenAIStyleAnalyzer:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        max_attempts: int = 3,
    ) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout_seconds)
        self._model = model
        self._max_attempts = max_attempts

    async def analyze_batch(
        self,
        examples: Sequence[StyleExample],
        *,
        batch_number: int,
    ) -> list[StyleRule]:
        payload = [example.to_dict() for example in examples]
        instructions = (
            "Analyze observable Telegram writing behavior only. Preserve slang, profanity, "
            "misspellings, lowercase text, fragments, and unusual punctuation as evidence. "
            "Cover response length, sentence count, capitalization, punctuation, final "
            "periods, greetings, acknowledgements, slang, profanity, reciprocal insults, "
            "teasing, emoji frequency, directness, formality, openings, endings, disagreement, "
            "aggression, scheduling, vague questions, frequent and avoided phrases, and "
            "friendly versus business communication. Describe contact tone, forms of address, "
            "and whether terse or aggressive exchanges appear reciprocal when evidence supports "
            "it. Positive records are style evidence. Negative records describe failed behavior "
            "that must not be copied or converted into positive rules. Evaluation records are "
            "AI-generated approvals and may describe outcome quality, but must never establish "
            "Matvey-authored style. "
            "Do not infer sensitive psychological traits. Return JSON with a rules array. "
            "Each rule must have text, confidence from 0 to 1, evidence_count, source_type, "
            "applicable_context, and scope (global or contact). Rules must be concrete "
            "behavior instructions, never vague personality labels."
        )
        result = await self._request_json(
            instructions=instructions,
            input_text=json.dumps(
                {"batch_number": batch_number, "examples": payload},
                ensure_ascii=False,
            ),
        )
        return _parse_rules(result)

    async def merge_rules(self, rules: Sequence[StyleRule]) -> list[StyleRule]:
        result = await self._request_json(
            instructions=(
                "Merge overlapping observations into a concise Matvey behavior rulebook. "
                "Keep context-specific differences, evidence metadata, profanity patterns, "
                "and contact scope. Return JSON with a rules array using the same schema. "
                "Do not invent traits or evidence."
            ),
            input_text=json.dumps(
                {"observations": [rule.to_dict() for rule in rules]},
                ensure_ascii=False,
            ),
        )
        return _parse_rules(result)

    async def _request_json(self, *, instructions: str, input_text: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                try:
                    response: Any = await self._client.responses.create(
                        model=self._model,
                        instructions=instructions,
                        input=input_text,
                        store=False,
                    )
                    output_text = str(getattr(response, "output_text", "") or "")
                    if output_text.strip():
                        parsed = json.loads(output_text)
                        if isinstance(parsed, dict):
                            return parsed
                except Exception:  # noqa: BLE001, S110
                    pass

                chat_response: Any = await self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": instructions},
                        {"role": "user", "content": input_text},
                    ],
                    response_format={"type": "json_object"},
                )
                raw_content = chat_response.choices[0].message.content or ""
                parsed = json.loads(raw_content)
                if isinstance(parsed, dict):
                    return parsed
                raise TypeError("Style analyzer returned a non-object JSON value")
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt + 1 < self._max_attempts:
                    await asyncio.sleep(min(2**attempt, 2))
        assert last_error is not None
        raise RuntimeError(
            f"Style analysis failed after {self._max_attempts} attempts: {last_error}"
        ) from last_error


def _parse_rules(value: dict[str, Any]) -> list[StyleRule]:
    raw_rules = value.get("rules")
    if not isinstance(raw_rules, list):
        raise TypeError("Style analyzer response has no rules array")
    return [
        StyleRule.from_dict(item)
        for item in raw_rules
        if isinstance(item, dict) and str(item.get("text", "")).strip()
    ]
