"""Local generation providers without mandatory heavyweight dependencies."""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from conversation_agent.local_slm.models import Action, GenerationRequest, GenerationResult
from conversation_agent.local_slm.runtime_config import LocalLLMConfig


class LocalModelError(RuntimeError):
    """Raised when the local model endpoint cannot produce a valid response."""


class LocalSchemaUnsupported(LocalModelError):
    """Raised when a local endpoint rejects JSON Schema response formatting."""


@dataclass(frozen=True)
class LocalStructuredReply:
    text: str
    model: str
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    tokens_per_second: float | None


class GenerationProvider(Protocol):
    provider_name: str

    async def health_check(self) -> bool:
        """Return whether the provider is available."""
        ...

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        """Generate Telegram bubbles for a compact local context."""
        ...


class FakeLocalGenerationProvider:
    provider_name = "fake-local"

    async def health_check(self) -> bool:
        return True

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        if request.policy.action != "reply":
            return GenerationResult(
                action=request.policy.action,
                messages=(),
                confidence=request.policy.confidence,
                handoff_required=request.policy.needs_handoff,
                provider=self.provider_name,
            )
        text = _latest_contact_text(request)
        if "бот" in text.lower() or "автомат" in text.lower():
            messages = ("да, можем", "что именно надо автоматизировать?")
        else:
            messages = ("понял", "расскажи чуть подробнее")
        return GenerationResult(
            action="reply",
            messages=messages[: request.policy.suggested_bubble_count or 1],
            confidence=0.82,
            provider=self.provider_name,
            backend="fake",
            raw_output=json.dumps({"messages": messages}, ensure_ascii=False),
            latency_ms=1,
        )


class OpenAICompatibleLocalProvider:
    """Client for llama.cpp/vLLM/OpenAI-compatible local HTTP endpoints."""

    provider_name = "local_openai_compatible"
    backend_name = "llama.cpp"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 20,
        max_output_tokens: int = 256,
        temperature: float = 0.7,
        top_k: int = 20,
        top_p: float = 0.9,
        min_p: float = 0.0,
        presence_penalty: float = 1.5,
        context_tokens: int = 4096,
        api_key: str = "local-no-key",
        thinking: bool = False,
        seed: int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.root_url = self.base_url.removesuffix("/v1")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.context_tokens = context_tokens
        self.temperature = temperature
        self.top_k = top_k
        self.top_p = top_p
        self.min_p = min_p
        self.presence_penalty = presence_penalty
        self.thinking = thinking
        self.seed = seed

    @classmethod
    def from_config(cls, config: LocalLLMConfig) -> OpenAICompatibleLocalProvider:
        return cls(
            base_url=config.base_url,
            model=config.model,
            api_key=config.api_key,
            timeout_seconds=config.timeout_seconds,
            max_output_tokens=config.max_output_tokens,
            temperature=config.temperature,
            top_k=config.top_k,
            top_p=config.top_p,
            min_p=config.min_p,
            presence_penalty=config.presence_penalty,
            context_tokens=config.context_tokens,
            thinking=config.thinking,
            seed=config.seed,
        )

    async def health_check(self) -> bool:
        return await asyncio.to_thread(self._health_check_sync)

    async def list_models(self) -> list[str]:
        raw = await asyncio.to_thread(self._get_json, "/models")
        data = raw.get("data", []) if isinstance(raw, Mapping) else []
        return [str(item.get("id", "")) for item in data if isinstance(item, Mapping)]

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        started = time.perf_counter()
        raw: Any = None
        text = ""
        retry_count = 0
        result: GenerationResult | None = None
        errors: list[str] = []
        use_json_schema = True
        attempt = 0
        requested_action: Action = (
            "no_reply" if request.policy.action == "wait" else request.policy.action
        )
        allowed_actions = request.allowed_actions or (requested_action,)
        while attempt < 2:
            payload = self._build_payload(
                request,
                repair=attempt > 0,
                previous_output=text,
                previous_errors=errors,
                use_json_schema=use_json_schema,
                allowed_actions=allowed_actions,
            )
            try:
                raw = await asyncio.to_thread(self._post_json, "/chat/completions", payload)
            except LocalSchemaUnsupported:
                if not use_json_schema:
                    raise
                use_json_schema = False
                continue
            text = _extract_text(raw)
            try:
                _raise_for_thinking(json.dumps(raw, ensure_ascii=False), ())
                result = parse_generation_text(
                    text,
                    provider=self.provider_name,
                    allowed_actions=allowed_actions,
                )
                _raise_for_thinking(result.raw_output, result.messages)
                break
            except LocalModelError as exc:
                errors = [str(exc)]
                attempt += 1
                retry_count = attempt
        if result is None:
            reason = errors[-1] if errors else "unknown_error"
            raise LocalModelError(f"Local model returned invalid output twice: {reason}")
        _raise_for_thinking(result.raw_output, result.messages)
        usage = _extract_usage(raw)
        latency_ms = int((time.perf_counter() - started) * 1000)
        completion_tokens = usage.get("completion_tokens")
        return GenerationResult(
            action=result.action,
            messages=result.messages,
            reaction=result.reaction,
            handoff_required=result.handoff_required,
            confidence=result.confidence,
            provider=self.provider_name,
            backend=self.backend_name,
            model=self.model,
            raw_output=text,
            latency_ms=latency_ms,
            ttft_ms=None,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=completion_tokens,
            total_tokens=usage.get("total_tokens"),
            tokens_per_second=_tokens_per_second(completion_tokens, latency_ms),
            retry_count=retry_count,
        )

    async def create_structured_reply(
        self,
        *,
        instructions: str,
        user_content: str,
        schema: dict[str, Any],
        max_output_tokens: int,
    ) -> LocalStructuredReply:
        """Perform one structured local call; caller owns semantic retry policy."""
        started = time.perf_counter()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_content},
            ],
            "temperature": self.temperature,
            "top_k": self.top_k,
            "top_p": self.top_p,
            "min_p": self.min_p,
            "presence_penalty": self.presence_penalty,
            "max_tokens": min(max_output_tokens, self.max_output_tokens),
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "contract_renderer_response",
                    "strict": True,
                    "schema": schema,
                },
            },
            "chat_template_kwargs": {"enable_thinking": self.thinking},
            "stream": False,
        }
        if self.seed is not None:
            payload["seed"] = self.seed
        raw = await asyncio.to_thread(self._post_json, "/chat/completions", payload)
        text = _extract_text(raw)
        _raise_for_thinking(json.dumps(raw, ensure_ascii=False), ())
        usage = _extract_usage(raw)
        latency_ms = int((time.perf_counter() - started) * 1000)
        completion_tokens = usage.get("completion_tokens")
        return LocalStructuredReply(
            text=text,
            model=self.model,
            latency_ms=latency_ms,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=completion_tokens,
            total_tokens=usage.get("total_tokens"),
            tokens_per_second=_tokens_per_second(completion_tokens, latency_ms),
        )

    def _build_payload(
        self,
        request: GenerationRequest,
        *,
        repair: bool,
        previous_output: str,
        previous_errors: list[str],
        use_json_schema: bool,
        allowed_actions: tuple[Action, ...],
    ) -> dict[str, Any]:
        system = request.system_prompt or generation_system_prompt(
            thinking=self.thinking,
            allowed_actions=allowed_actions,
        )
        user_content = request.semantic_context or request.context.render(
            budget_chars=self.context_tokens * 4
        )
        if repair:
            user_content = (
                f"{user_content}\n\nPrevious invalid output:\n{previous_output}\n\n"
                f"Validation errors: {', '.join(previous_errors)}\n"
                "Repair by returning only one valid JSON object."
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "temperature": self.temperature,
            "top_k": self.top_k,
            "top_p": self.top_p,
            "min_p": self.min_p,
            "presence_penalty": self.presence_penalty,
            "max_tokens": self.max_output_tokens,
            "response_format": (
                generation_response_format(allowed_actions)
                if use_json_schema
                else {"type": "json_object"}
            ),
            "chat_template_kwargs": {"enable_thinking": self.thinking},
            "stream": False,
        }
        if self.seed is not None:
            payload["seed"] = self.seed
        return payload

    def _health_check_sync(self) -> bool:
        try:
            self._get_json_url(self.root_url + "/health")
            self._get_json("/models")
        except Exception:  # noqa: BLE001
            return False
        return True

    def _get_json(self, path: str) -> Any:
        return self._get_json_url(self.base_url + path)

    def _get_json_url(self, url: str) -> Any:
        with urllib.request.urlopen(
            url,
            timeout=self.timeout_seconds,
        ) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post_json(self, path: str, payload: dict[str, Any]) -> Any:
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            lowered = body.lower()
            if exc.code == 400 and ("json_schema" in lowered or "response_format" in lowered):
                raise LocalSchemaUnsupported(f"json_schema_unsupported:{body}") from exc
            raise LocalModelError(f"local_http_error:{exc.code}:{body}") from exc
        except urllib.error.URLError as exc:
            raise TimeoutError(f"Local generation endpoint unavailable: {exc}") from exc


def _extract_text(raw: Any) -> str:
    try:
        return str(raw["choices"][0]["message"]["content"])
    except Exception as exc:
        raise ValueError("OpenAI-compatible endpoint returned an unsupported response") from exc


def parse_generation_text(
    text: str,
    *,
    provider: str,
    allowed_actions: tuple[Action, ...] = ("reply", "no_reply", "reaction", "handoff"),
) -> GenerationResult:
    try:
        parsed = json.loads(_extract_json_object(text))
    except json.JSONDecodeError as exc:
        raise LocalModelError(f"invalid_json:{exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise LocalModelError("invalid_json:not_object")
    action = parsed.get("action")
    if action not in allowed_actions:
        raise LocalModelError("semantic_validation:invalid_action")
    raw_messages = parsed.get("messages")
    if not isinstance(raw_messages, list) or not all(isinstance(item, str) for item in raw_messages):
        raise LocalModelError("semantic_validation:messages_not_string_array")
    messages = tuple(item.strip() for item in raw_messages if item.strip())
    if action == "reply" and not messages:
        raise LocalModelError("semantic_validation:empty_reply")
    if action != "reply" and messages:
        raise LocalModelError("semantic_validation:messages_for_non_reply")
    if len(messages) > 4:
        raise LocalModelError("semantic_validation:too_many_bubbles")
    handoff_required = parsed.get("handoff_required")
    if not isinstance(handoff_required, bool):
        raise LocalModelError("semantic_validation:handoff_not_boolean")
    if handoff_required != (action == "handoff"):
        raise LocalModelError("semantic_validation:handoff_action_mismatch")
    reaction = parsed.get("reaction")
    if reaction is not None and not isinstance(reaction, str):
        raise LocalModelError("semantic_validation:invalid_reaction")
    if action != "reaction" and reaction is not None:
        raise LocalModelError("semantic_validation:reaction_action_mismatch")
    confidence = parsed.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
        raise LocalModelError("semantic_validation:invalid_confidence")
    return GenerationResult(
        action=cast(Action, action),
        messages=messages,
        reaction=reaction,
        handoff_required=handoff_required,
        confidence=float(confidence),
        provider=provider,
        raw_output=text,
    )


def generation_system_prompt(
    *,
    thinking: bool,
    allowed_actions: tuple[Action, ...],
) -> str:
    no_think = "" if thinking else "/no_think\n"
    action_instruction = (
        f"The dialogue policy requires action={allowed_actions[0]}; return exactly this action.\n"
        if len(allowed_actions) == 1
        else f"Choose one allowed action: {', '.join(allowed_actions)}.\n"
    )
    return (
        no_think
        + "You generate Telegram replies for a human operator.\n"
        "Return only one strict JSON object. Do not include markdown or explanations.\n"
        "Do not include <think>, reasoning_content, analysis, or internal reasoning.\n"
        "Required fields: action, messages, reaction, handoff_required, confidence.\n"
        + action_instruction
        +
        "For reply use 1-4 short Russian Telegram messages. For no_reply use empty messages.\n"
        "Use reaction only for action=reaction. Set it to null for every other action.\n"
        "Set handoff_required=true only when action=handoff; otherwise set it to false."
    )


def generation_response_format(allowed_actions: tuple[Action, ...]) -> dict[str, Any]:
    if not allowed_actions:
        raise ValueError("at least one allowed action is required")
    requested_action = allowed_actions[0]
    is_forced = len(allowed_actions) == 1
    is_reply = is_forced and requested_action == "reply"
    is_reaction = is_forced and requested_action == "reaction"
    is_handoff = is_forced and requested_action == "handoff"
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "telegram_response",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "action",
                    "messages",
                    "reaction",
                    "handoff_required",
                    "confidence",
                ],
                "properties": {
                    "action": (
                        {"type": "string", "const": requested_action}
                        if is_forced
                        else {"type": "string", "enum": list(allowed_actions)}
                    ),
                    "messages": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1 if is_reply else 0,
                        "maxItems": 4 if (is_reply or not is_forced) else 0,
                    },
                    "reaction": (
                        {"type": "string", "minLength": 1}
                        if is_reaction
                        else (
                            {"type": "null"}
                            if is_forced
                            else {"anyOf": [{"type": "string"}, {"type": "null"}]}
                        )
                    ),
                    "handoff_required": (
                        {"type": "boolean", "const": is_handoff}
                        if is_forced
                        else {"type": "boolean"}
                    ),
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                },
            },
        },
    }


def _extract_json_object(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        return stripped
    return stripped[start : end + 1]


def _raise_for_thinking(raw_output: str, messages: tuple[str, ...]) -> None:
    combined = "\n".join((raw_output, *messages)).lower()
    if "<think" in combined or "</think" in combined or "reasoning_content" in combined:
        raise LocalModelError("reasoning_output_detected")


def _extract_usage(raw: Any) -> dict[str, int]:
    if not isinstance(raw, Mapping):
        return {}
    usage = raw.get("usage")
    if not isinstance(usage, Mapping):
        return {}
    result: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            result[key] = value
    return result


def _tokens_per_second(completion_tokens: int | None, latency_ms: int) -> float | None:
    if completion_tokens is None or latency_ms <= 0:
        return None
    return round(completion_tokens / (latency_ms / 1000), 2)


def _latest_contact_text(request: GenerationRequest) -> str:
    for turn in reversed(request.context.conversation):
        if turn.get("role") in {"user", "contact"}:
            return turn.get("content", "")
    return ""
