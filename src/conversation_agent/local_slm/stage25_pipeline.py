"""GPT policy and contract-bound message renderers for Stage 2.5."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any, Protocol, cast

from conversation_agent.llm.openai_client import OpenAIReplyClient
from conversation_agent.local_slm.models import Action, GenerationResult
from conversation_agent.local_slm.provider import OpenAICompatibleLocalProvider
from conversation_agent.local_slm.stage2_dataset import BenchmarkScenario
from conversation_agent.local_slm.stage25_contract import (
    CONTRACT_ACTIONS,
    LengthPlanner,
    RendererValidation,
    ResponseContract,
    ResponseContractError,
    renderer_response_schema,
    response_contract_schema,
    validate_renderer_output,
)


@dataclass(frozen=True)
class PolicyContext:
    conversation: tuple[dict[str, str], ...]
    relationship: dict[str, Any]
    known_facts: tuple[str, ...]
    restrictions: tuple[str, ...]
    goal: str
    context_summary: str = ""

    @classmethod
    def from_scenario(cls, scenario: BenchmarkScenario) -> PolicyContext:
        """Build runtime input without benchmark labels or expected actions."""
        return cls(
            conversation=scenario.flat_conversation,
            relationship=dict(scenario.relationship),
            known_facts=scenario.known_facts,
            restrictions=scenario.forbidden_claims,
            goal=scenario.goal,
        )

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "conversation": list(self.conversation),
            "relationship": self.relationship,
            "known_facts": list(self.known_facts),
            "restrictions": list(self.restrictions),
            "goal": self.goal,
            "context_summary": self.context_summary,
        }


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

    def to_dict(self) -> dict[str, int | None]:
        return asdict(self)


@dataclass(frozen=True)
class PolicyPlan:
    contract: ResponseContract
    latency_ms: int
    model: str
    raw_output: str
    usage: Usage = Usage()


@dataclass(frozen=True)
class RenderedMessage:
    result: GenerationResult
    usage: Usage = Usage()


@dataclass(frozen=True)
class ContractPipelineResult:
    contract: ResponseContract
    output: GenerationResult
    renderer_validation: RendererValidation
    policy_latency_ms: int
    renderer_latency_ms: int
    total_latency_ms: int
    policy_model: str
    renderer_model: str | None
    renderer_name: str
    renderer_retry_count: int
    policy_usage: Usage
    renderer_usage: Usage

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract": self.contract.to_dict(),
            "output": self.output.to_dict(),
            "renderer_validation": self.renderer_validation.to_dict(),
            "policy_latency_ms": self.policy_latency_ms,
            "renderer_latency_ms": self.renderer_latency_ms,
            "total_latency_ms": self.total_latency_ms,
            "policy_model": self.policy_model,
            "renderer_model": self.renderer_model,
            "renderer_name": self.renderer_name,
            "renderer_retry_count": self.renderer_retry_count,
            "policy_usage": self.policy_usage.to_dict(),
            "renderer_usage": self.renderer_usage.to_dict(),
        }


class ContractPolicy(Protocol):
    provider_name: str
    calls: int

    async def plan(self, context: PolicyContext) -> PolicyPlan:
        ...


class ContractRenderer(Protocol):
    renderer_name: str
    calls: int

    async def render(
        self,
        context: PolicyContext,
        contract: ResponseContract,
        *,
        previous_output: str = "",
        repair_errors: tuple[str, ...] = (),
    ) -> RenderedMessage:
        ...


class GPTContractPolicy:
    provider_name = "gpt_policy"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4o-mini",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.model = model
        self.calls = 0
        self._client = OpenAIReplyClient(
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
        )
        self._length_planner = LengthPlanner()

    async def plan(self, context: PolicyContext) -> PolicyPlan:
        self.calls += 1
        started = time.perf_counter()
        recommendations = {
            action: self._length_planner.recommend(
                action=action,
                conversation=context.conversation,
                relationship=context.relationship,
                known_facts=context.known_facts,
                goal=context.goal,
            )
            for action in CONTRACT_ACTIONS
        }
        payload = {
            **context.to_prompt_dict(),
            "length_recommendations_by_action": recommendations,
        }
        reply = await self._client.create_structured_reply(
            instructions=_policy_instructions(),
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            ],
            schema=response_contract_schema(),
            schema_name="response_contract",
            max_output_tokens=700,
            temperature=0.1,
            top_p=0.9,
        )
        try:
            value = json.loads(reply.text)
        except json.JSONDecodeError as exc:
            raise ResponseContractError(["policy_invalid_json"]) from exc
        if not isinstance(value, dict):
            raise ResponseContractError(["policy_output_not_object"])
        contract = ResponseContract.from_dict(value)
        semantic_errors = validate_policy_contract(contract, context)
        if semantic_errors:
            raise ResponseContractError(semantic_errors)
        return PolicyPlan(
            contract=contract,
            latency_ms=int((time.perf_counter() - started) * 1000),
            model=reply.model,
            raw_output=reply.text,
            usage=Usage(
                prompt_tokens=reply.prompt_tokens,
                completion_tokens=reply.completion_tokens,
                total_tokens=reply.total_tokens,
            ),
        )


class OpenAIContractRenderer:
    renderer_name = "openai_renderer"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4o-mini",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.model = model
        self.calls = 0
        self._client = OpenAIReplyClient(
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
        )

    async def render(
        self,
        context: PolicyContext,
        contract: ResponseContract,
        *,
        previous_output: str = "",
        repair_errors: tuple[str, ...] = (),
    ) -> RenderedMessage:
        self.calls += 1
        content = _renderer_context(
            context,
            contract,
            previous_output=previous_output,
            repair_errors=repair_errors,
        )
        reply = await self._client.create_structured_reply(
            instructions=_renderer_instructions(contract),
            messages=[{"role": "user", "content": content}],
            schema=renderer_response_schema(contract),
            schema_name="contract_renderer_response",
            max_output_tokens=256,
            temperature=0.5,
            top_p=0.9,
        )
        result = parse_renderer_output(
            reply.text,
            provider=self.renderer_name,
            model=reply.model,
            latency_ms=0,
        )
        return RenderedMessage(
            result=result,
            usage=Usage(
                prompt_tokens=reply.prompt_tokens,
                completion_tokens=reply.completion_tokens,
                total_tokens=reply.total_tokens,
            ),
        )


class LocalQwenContractRenderer:
    renderer_name = "local_qwen_renderer"

    def __init__(self, provider: OpenAICompatibleLocalProvider) -> None:
        self.provider = provider
        self.model = provider.model
        self.calls = 0

    async def render(
        self,
        context: PolicyContext,
        contract: ResponseContract,
        *,
        previous_output: str = "",
        repair_errors: tuple[str, ...] = (),
    ) -> RenderedMessage:
        self.calls += 1
        reply = await self.provider.create_structured_reply(
            instructions="/no_think\n" + _renderer_instructions(contract),
            user_content=_renderer_context(
                context,
                contract,
                previous_output=previous_output,
                repair_errors=repair_errors,
            ),
            schema=renderer_response_schema(contract),
            max_output_tokens=256,
        )
        return RenderedMessage(
            result=parse_renderer_output(
                reply.text,
                provider=self.renderer_name,
                model=reply.model,
                latency_ms=reply.latency_ms,
                tokens_per_second=reply.tokens_per_second,
            ),
            usage=Usage(
                prompt_tokens=reply.prompt_tokens,
                completion_tokens=reply.completion_tokens,
                total_tokens=reply.total_tokens,
            ),
        )


async def execute_contract_pipeline(
    *,
    policy: ContractPolicy,
    renderer: ContractRenderer,
    context: PolicyContext,
) -> ContractPipelineResult:
    plan = await policy.plan(context)
    return await execute_renderer_with_plan(
        plan=plan,
        renderer=renderer,
        context=context,
    )


async def execute_renderer_with_plan(
    *,
    plan: PolicyPlan,
    renderer: ContractRenderer,
    context: PolicyContext,
) -> ContractPipelineResult:
    started = time.perf_counter()
    contract = plan.contract
    if contract.target_bubble_count == 0:
        output = GenerationResult(
            action=cast(Action, contract.action),
            messages=(),
            reaction=contract.reaction,
            handoff_required=contract.handoff_required,
            confidence=contract.confidence,
            provider=renderer.renderer_name,
            backend="contract_short_circuit",
            model=getattr(renderer, "model", None),
        )
        validation = validate_renderer_output(
            contract,
            output,
            incoming_messages=_incoming_messages(context),
        )
        return ContractPipelineResult(
            contract=contract,
            output=output,
            renderer_validation=validation,
            policy_latency_ms=plan.latency_ms,
            renderer_latency_ms=0,
            total_latency_ms=(
                plan.latency_ms + int((time.perf_counter() - started) * 1000)
            ),
            policy_model=plan.model,
            renderer_model=output.model,
            renderer_name=renderer.renderer_name,
            renderer_retry_count=0,
            policy_usage=plan.usage,
            renderer_usage=Usage(),
        )
    previous = ""
    validation = RendererValidation(False, ("not_rendered",), {})
    rendered: RenderedMessage | None = None
    renderer_started = time.perf_counter()
    attempts = 0
    for attempt in range(2):
        attempts = attempt + 1
        rendered = await renderer.render(
            context,
            contract,
            previous_output=previous,
            repair_errors=validation.errors if attempt else (),
        )
        validation = validate_renderer_output(
            contract,
            rendered.result,
            incoming_messages=_incoming_messages(context),
        )
        if validation.valid:
            break
        previous = rendered.result.raw_output
    assert rendered is not None
    renderer_latency_ms = int((time.perf_counter() - renderer_started) * 1000)
    output = GenerationResult(
        **{
            **rendered.result.to_dict(),
            "messages": rendered.result.messages,
            "latency_ms": renderer_latency_ms,
            "retry_count": max(0, attempts - 1),
        }
    )
    return ContractPipelineResult(
        contract=contract,
        output=output,
        renderer_validation=validation,
        policy_latency_ms=plan.latency_ms,
        renderer_latency_ms=renderer_latency_ms,
        total_latency_ms=(
            plan.latency_ms + int((time.perf_counter() - started) * 1000)
        ),
        policy_model=plan.model,
        renderer_model=output.model,
        renderer_name=renderer.renderer_name,
        renderer_retry_count=max(0, attempts - 1),
        policy_usage=plan.usage,
        renderer_usage=rendered.usage,
    )


def validate_policy_contract(
    contract: ResponseContract,
    context: PolicyContext,
) -> tuple[str, ...]:
    known = {fact.casefold() for fact in context.known_facts}
    restrictions = {claim.casefold() for claim in context.restrictions}
    errors: list[str] = []
    if any(fact.casefold() not in known for fact in contract.required_facts):
        errors.append("required_fact_not_known")
    if not restrictions.issubset(
        {claim.casefold() for claim in contract.forbidden_claims}
    ):
        errors.append("missing_runtime_restriction")
    return tuple(errors)


def parse_renderer_output(
    text: str,
    *,
    provider: str,
    model: str,
    latency_ms: int,
    tokens_per_second: float | None = None,
) -> GenerationResult:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"renderer_invalid_json:{exc.msg}") from exc
    if not isinstance(value, dict):
        raise TypeError("renderer_output_not_object")
    action = str(value.get("action", ""))
    if action not in CONTRACT_ACTIONS:
        raise ValueError("renderer_invalid_action")
    raw_messages = value.get("messages")
    if not isinstance(raw_messages, list) or not all(
        isinstance(message, str) for message in raw_messages
    ):
        raise TypeError("renderer_messages_not_string_array")
    reaction = value.get("reaction")
    if reaction is not None and not isinstance(reaction, str):
        raise TypeError("renderer_invalid_reaction")
    handoff = value.get("handoff_required")
    if not isinstance(handoff, bool):
        raise TypeError("renderer_invalid_handoff")
    confidence = value.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise TypeError("renderer_invalid_confidence")
    return GenerationResult(
        action=cast(Action, action),
        messages=tuple(
            message.strip() for message in raw_messages if message.strip()
        ),
        reaction=reaction,
        handoff_required=handoff,
        confidence=float(confidence),
        provider=provider,
        backend="contract_renderer",
        model=model,
        raw_output=text,
        latency_ms=latency_ms,
        tokens_per_second=tokens_per_second,
    )


def _policy_instructions() -> str:
    return (
        "You are a dialogue policy, not a message writer. Return only ResponseContract JSON. "
        "Never write final Telegram messages and never add a messages field. Choose whether "
        "reply is needed. Copy required_facts only verbatim from known_facts. Copy every "
        "restriction verbatim into forbidden_claims. Use the supplied adaptive length "
        "recommendation for the chosen action unless conversation context clearly requires "
        "a smaller valid limit. Keep max_questions at 0 or 1 for replies."
    )


def _renderer_instructions(contract: ResponseContract) -> str:
    return (
        "Render a natural Russian Telegram response that follows the supplied "
        "ResponseContract exactly. Return only strict JSON. Do not reconsider the action. "
        "Do not add facts. Avoid assistant language, headings, lists, repetition, and "
        "explanations. Keep each bubble conversational and concise.\nContract:\n"
        + json.dumps(contract.to_dict(), ensure_ascii=False, separators=(",", ":"))
    )


def _renderer_context(
    context: PolicyContext,
    contract: ResponseContract,
    *,
    previous_output: str,
    repair_errors: tuple[str, ...],
) -> str:
    value = {
        "conversation": list(context.conversation),
        "relationship": context.relationship,
        "allowed_facts": list(context.known_facts),
        "contract": contract.to_dict(),
    }
    if repair_errors:
        value["repair"] = {
            "previous_output": previous_output,
            "violations": list(repair_errors),
            "instruction": "Fix only these violations; keep the same contract and action.",
        }
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _incoming_messages(context: PolicyContext) -> tuple[str, ...]:
    return tuple(
        turn.get("content", "")
        for turn in context.conversation
        if turn.get("role") in {"contact", "user"}
    )
