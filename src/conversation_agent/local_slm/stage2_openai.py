"""Structured OpenAI provider used only by the Stage 2 baseline benchmark."""

from __future__ import annotations

import time

from conversation_agent.llm.openai_client import OpenAIReplyClient
from conversation_agent.local_slm.models import Action, GenerationRequest, GenerationResult
from conversation_agent.local_slm.provider import (
    LocalModelError,
    generation_response_format,
    generation_system_prompt,
    parse_generation_text,
)


class OpenAIBenchmarkProvider:
    provider_name = "openai_gpt4o_mini"
    backend_name = "openai_responses"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4o-mini",
        timeout_seconds: float = 30.0,
        max_output_tokens: int = 256,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OPENAI_API_KEY is required for openai_gpt4o_mini")
        self.model = model
        self.max_output_tokens = max_output_tokens
        self._client = OpenAIReplyClient(
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
        )

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        started = time.perf_counter()
        requested_action: Action = (
            "no_reply" if request.policy.action == "wait" else request.policy.action
        )
        allowed_actions = request.allowed_actions or (requested_action,)
        response_format = generation_response_format(allowed_actions)
        schema = response_format["json_schema"]["schema"]
        system_prompt = request.system_prompt or generation_system_prompt(
            thinking=False,
            allowed_actions=allowed_actions,
        )
        semantic_context = request.semantic_context or request.context.render(
            budget_chars=16_384
        )
        errors: list[str] = []
        previous_output = ""
        for attempt in range(2):
            content = semantic_context
            if attempt:
                content += (
                    "\n\nPrevious invalid output:\n"
                    + previous_output
                    + "\nValidation errors: "
                    + ", ".join(errors)
                    + "\nReturn one corrected JSON object."
                )
            reply = await self._client.create_structured_reply(
                instructions=system_prompt,
                messages=[{"role": "user", "content": content}],
                schema=schema,
                max_output_tokens=min(request.max_output_tokens, self.max_output_tokens),
                temperature=request.temperature,
                top_p=request.top_p,
            )
            previous_output = reply.text
            try:
                parsed = parse_generation_text(
                    reply.text,
                    provider=self.provider_name,
                    allowed_actions=allowed_actions,
                )
            except LocalModelError as exc:
                errors = [str(exc)]
                continue
            latency_ms = int((time.perf_counter() - started) * 1000)
            return GenerationResult(
                action=parsed.action,
                messages=parsed.messages,
                reaction=parsed.reaction,
                handoff_required=parsed.handoff_required,
                confidence=parsed.confidence,
                provider=self.provider_name,
                backend=self.backend_name,
                model=reply.model,
                raw_output=reply.text,
                latency_ms=latency_ms,
                prompt_tokens=reply.prompt_tokens,
                completion_tokens=reply.completion_tokens,
                total_tokens=reply.total_tokens,
                retry_count=attempt,
            )
        reason = errors[-1] if errors else "empty structured response"
        raise LocalModelError(f"OpenAI returned invalid output twice: {reason}")
