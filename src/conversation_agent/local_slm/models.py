"""Contracts for the local Telegram SLM experiment."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Action = Literal["reply", "no_reply", "wait", "reaction", "handoff"]
GenerationMode = Literal["local_only", "local_with_fallback", "openai_only", "compare_shadow"]


@dataclass(frozen=True)
class DialoguePolicyInput:
    messages: tuple[str, ...]
    recent_history: tuple[dict[str, str], ...] = ()
    conversation_state: str = "new_contact"
    permissions: dict[str, bool] = field(default_factory=lambda: {"reply": True})
    relationship: str = "unknown"


@dataclass(frozen=True)
class DialogueDecision:
    action: Action
    intent: str
    interaction_mode: str
    emotion: str
    urgency: float
    needs_handoff: bool
    needs_generation: bool
    suggested_bubble_count: int
    confidence: float
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LocalContext:
    agent_id: str
    relationship: str
    mode: str
    emotion: str
    goal: str
    facts: tuple[str, ...]
    conversation: tuple[dict[str, str], ...]
    corrections: tuple[str, ...] = ()
    adapter_id: str | None = None

    def render(self, *, budget_chars: int) -> str:
        sections = [
            f"<agent:{self.agent_id}>",
            f"<relationship:{self.relationship}>",
            f"<mode:{self.mode}>",
            f"<emotion:{self.emotion}>",
            f"<goal:{self.goal}>",
        ]
        if self.adapter_id:
            sections.append(f"<adapter:{self.adapter_id}>")
        if self.facts:
            sections.append("Facts:\n" + "\n".join(f"- {item}" for item in self.facts))
        if self.corrections:
            sections.append("Human fixes:\n" + "\n".join(f"- {item}" for item in self.corrections))
        if self.conversation:
            rendered = "\n".join(
                f"- {turn.get('role', 'user')}: {turn.get('content', '')}"
                for turn in self.conversation
            )
            sections.append("Conversation:\n" + rendered)
        sections.append("Generate 1-4 Telegram messages as JSON.")
        result = "\n\n".join(sections)
        return result[:budget_chars]


@dataclass(frozen=True)
class GenerationRequest:
    policy: DialogueDecision
    context: LocalContext
    max_output_tokens: int = 256
    temperature: float = 0.7
    top_p: float = 0.9


@dataclass(frozen=True)
class GenerationResult:
    action: Action
    messages: tuple[str, ...] = ()
    reaction: str | None = None
    handoff_required: bool = False
    confidence: float = 0.0
    provider: str = "unknown"
    backend: str = "unknown"
    model: str | None = None
    raw_output: str = ""
    latency_ms: int = 0
    ttft_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    tokens_per_second: float | None = None
    retry_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["messages"] = list(self.messages)
        return value


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple[str, ...] = ()
    normalized: GenerationResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "normalized": self.normalized.to_dict() if self.normalized else None,
        }


@dataclass(frozen=True)
class HybridResult:
    selected: GenerationResult
    validation: ValidationResult
    fallback_used: bool
    provider_results: dict[str, GenerationResult]
    route: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected": self.selected.to_dict(),
            "validation": self.validation.to_dict(),
            "fallback_used": self.fallback_used,
            "provider_results": {
                key: value.to_dict() for key, value in self.provider_results.items()
            },
            "route": list(self.route),
        }
