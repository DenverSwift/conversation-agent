"""Bounded OpenAI analysis for offline style compilation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any

from conversation_agent.style.models import StyleExample, StyleRule

ANALYZER_PROMPT_VERSION = "AA.2-observations-v1"
ANALYZER_PROMPT_TEMPLATE = (
    "Analyze observable Telegram writing behavior only. Preserve slang, profanity, "
    "misspellings, lowercase text, fragments, and unusual punctuation as evidence. "
    "Cover reply length, punctuation, greetings, acknowledgements, profanity, reciprocal "
    "teasing, disagreement, scheduling, formality, and generic assistant phrase avoidance. "
    "Positive records are style evidence. Negative records describe behavior that must not "
    "be copied. Evaluation records are AI-generated and must never establish Matvey style. "
    "Do not infer sensitive traits. Return JSON with a rules array. Each rule must include "
    "observation_id, behavior_category, text, applicable_context, scope, confidence, "
    "supporting_source_keys, supporting_source_hashes, polarity, source_type, "
    "source_priority, and evidence_count. Supporting identifiers must come from the input. "
    "Rules must be concrete behavior instructions, never vague personality labels."
)


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
        self.request_count = 0
        self.input_tokens_used: int | None = 0
        self.output_tokens_used: int | None = 0

    async def analyze_batch(
        self,
        examples: Sequence[StyleExample],
        *,
        batch_number: int,
    ) -> list[StyleRule]:
        payload = [example.to_dict() for example in examples]
        result = await self._request_json(
            instructions=ANALYZER_PROMPT_TEMPLATE,
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
                    self.request_count += 1
                    response: Any = await self._client.responses.create(
                        model=self._model,
                        instructions=instructions,
                        input=input_text,
                        store=False,
                    )
                    self._record_usage(getattr(response, "usage", None))
                    output_text = str(getattr(response, "output_text", "") or "")
                    if output_text.strip():
                        parsed = json.loads(output_text)
                        if isinstance(parsed, dict):
                            return parsed
                except Exception:  # noqa: BLE001, S110
                    pass

                self.request_count += 1
                chat_response: Any = await self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": instructions},
                        {"role": "user", "content": input_text},
                    ],
                    response_format={"type": "json_object"},
                )
                self._record_usage(getattr(chat_response, "usage", None))
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
            f"Style analysis failed after {self._max_attempts} attempts: "
            f"{type(last_error).__name__}"
        ) from last_error

    def _record_usage(self, usage: Any) -> None:
        if usage is None:
            self.input_tokens_used = None
            self.output_tokens_used = None
            return
        input_tokens = getattr(usage, "input_tokens", None)
        if input_tokens is None:
            input_tokens = getattr(usage, "prompt_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        if output_tokens is None:
            output_tokens = getattr(usage, "completion_tokens", None)
        if input_tokens is None or output_tokens is None:
            self.input_tokens_used = None
            self.output_tokens_used = None
            return
        if self.input_tokens_used is not None:
            self.input_tokens_used += int(input_tokens)
        if self.output_tokens_used is not None:
            self.output_tokens_used += int(output_tokens)


def _parse_rules(value: dict[str, Any]) -> list[StyleRule]:
    raw_rules = value.get("rules")
    if not isinstance(raw_rules, list):
        raise TypeError("Style analyzer response has no rules array")
    return [
        StyleRule.from_dict(item)
        for item in raw_rules
        if isinstance(item, dict) and str(item.get("text", "")).strip()
    ]
