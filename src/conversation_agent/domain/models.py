"""Domain contracts for the approval-first Telegram human agent."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

CONVERSATION_STAGES = {
    "new_contact",
    "discovery",
    "qualification",
    "solution_matching",
    "objection_handling",
    "call_proposed",
    "handed_off",
    "closed",
    "not_relevant",
}

GOALS = {
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
}


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _number(value: Any, default: float, *, low: float = 0.0, high: float = 1.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


@dataclass(frozen=True)
class IdentityProfile:
    user_id: str
    display_name: str
    language: str = "ru"
    role: str = ""
    company: str = ""
    communication_rules: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    escalation_rules: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> IdentityProfile:
        return cls(
            user_id=str(value.get("user_id", "local-user")),
            display_name=str(value.get("display_name", "User")),
            language=str(value.get("language", "ru")),
            role=str(value.get("role", "")),
            company=str(value.get("company", "")),
            communication_rules=_strings(value.get("communication_rules")),
            forbidden_claims=_strings(value.get("forbidden_claims")),
            escalation_rules=_strings(value.get("escalation_rules")),
        )


@dataclass(frozen=True)
class BusinessProfile:
    name: str
    description: str = ""
    services: tuple[str, ...] = ()
    target_audience: tuple[str, ...] = ()
    typical_client_tasks: tuple[str, ...] = ()
    restrictions: tuple[str, ...] = ()
    allowed_promises: tuple[str, ...] = ()
    pricing_rules: tuple[str, ...] = ()
    portfolio_links: tuple[str, ...] = ()
    qualification_criteria: tuple[str, ...] = ()
    desired_next_step: str = "clarify the client's task"
    handoff_conditions: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BusinessProfile:
        return cls(
            name=str(value.get("name", "Local business")),
            description=str(value.get("description", "")),
            services=_strings(value.get("services")),
            target_audience=_strings(value.get("target_audience")),
            typical_client_tasks=_strings(value.get("typical_client_tasks")),
            restrictions=_strings(value.get("restrictions")),
            allowed_promises=_strings(value.get("allowed_promises")),
            pricing_rules=_strings(value.get("pricing_rules")),
            portfolio_links=_strings(value.get("portfolio_links")),
            qualification_criteria=_strings(value.get("qualification_criteria")),
            desired_next_step=str(value.get("desired_next_step", "clarify the client's task")),
            handoff_conditions=_strings(value.get("handoff_conditions")),
        )


@dataclass(frozen=True)
class StyleProfile:
    average_message_length: int = 80
    formality: str = "neutral"
    vocabulary: tuple[str, ...] = ()
    punctuation: str = "natural"
    emoji_usage: str = "rare"
    profanity_usage: str = "evidence_only"
    openings: tuple[str, ...] = ()
    endings: tuple[str, ...] = ()
    directness: str = "direct"
    bubble_pattern: str = "short natural bubbles"
    forbidden_generic_phrases: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> StyleProfile:
        try:
            average_length = max(1, int(value.get("average_message_length", 80)))
        except (TypeError, ValueError):
            average_length = 80
        return cls(
            average_message_length=average_length,
            formality=str(value.get("formality", "neutral")),
            vocabulary=_strings(value.get("vocabulary")),
            punctuation=str(value.get("punctuation", "natural")),
            emoji_usage=str(value.get("emoji_usage", "rare")),
            profanity_usage=str(value.get("profanity_usage", "evidence_only")),
            openings=_strings(value.get("openings")),
            endings=_strings(value.get("endings")),
            directness=str(value.get("directness", "direct")),
            bubble_pattern=str(value.get("bubble_pattern", "short natural bubbles")),
            forbidden_generic_phrases=_strings(value.get("forbidden_generic_phrases")),
        )


@dataclass(frozen=True)
class RelationshipProfile:
    contact_id: str
    relationship_type: str = "unknown"
    formality: str = "neutral"
    warmth: str = "neutral"
    directness: str = "neutral"
    typical_reply_length: str = "medium"
    emoji_usage: str = "unknown"
    profanity_tolerance: str = "unknown"
    teasing_tolerance: str = "unknown"
    preferred_language: str = "unknown"
    recent_interaction_summary: str = ""
    confidence: float = 0.0
    last_updated: str = ""

    @classmethod
    def neutral(cls, contact_id: str) -> RelationshipProfile:
        return cls(contact_id=contact_id)


@dataclass(frozen=True)
class ConversationState:
    contact_id: str
    conversation_stage: str = "new_contact"
    detected_intent: str = "unknown"
    active_goal: str = "acknowledge"
    known_facts: tuple[str, ...] = ()
    missing_information: tuple[str, ...] = ()
    objections: tuple[str, ...] = ()
    commitments: tuple[str, ...] = ()
    next_recommended_action: str = "acknowledge"
    human_handoff_required: bool = False
    confidence: float = 0.0
    pending_draft_id: int | None = None
    pending_behavior_plan_id: int | None = None

    @classmethod
    def initial(cls, contact_id: str) -> ConversationState:
        return cls(contact_id=contact_id)


@dataclass(frozen=True)
class InteractionAnalysis:
    should_reply: bool
    intent: str
    interaction_mode: str
    conversation_stage: str
    urgency: float
    sentiment: str
    needs_empathy: bool
    needs_human_handoff: bool
    missing_information: tuple[str, ...]
    recommended_goal: str
    confidence: float
    fallback_used: bool = False

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> InteractionAnalysis:
        stage = str(value.get("conversation_stage", "discovery"))
        if stage not in CONVERSATION_STAGES:
            raise ValueError(f"Unsupported conversation stage: {stage}")
        goal = str(value.get("recommended_goal", "ask_clarifying_question"))
        if goal not in GOALS:
            raise ValueError(f"Unsupported recommended goal: {goal}")
        return cls(
            should_reply=bool(value.get("should_reply", True)),
            intent=str(value.get("intent", "unknown")),
            interaction_mode=str(value.get("interaction_mode", "business_inquiry")),
            conversation_stage=stage,
            urgency=_number(value.get("urgency"), 0.3),
            sentiment=str(value.get("sentiment", "neutral")),
            needs_empathy=bool(value.get("needs_empathy", False)),
            needs_human_handoff=bool(value.get("needs_human_handoff", False)),
            missing_information=_strings(value.get("missing_information")),
            recommended_goal=goal,
            confidence=_number(value.get("confidence"), 0.5),
        )

    @classmethod
    def safe_fallback(cls) -> InteractionAnalysis:
        return cls(
            should_reply=True,
            intent="unknown",
            interaction_mode="business_inquiry",
            conversation_stage="discovery",
            urgency=0.3,
            sentiment="neutral",
            needs_empathy=False,
            needs_human_handoff=False,
            missing_information=("client_task",),
            recommended_goal="ask_clarifying_question",
            confidence=0.2,
            fallback_used=True,
        )


@dataclass(frozen=True)
class GoalPlan:
    goal: str
    reason: str
    handoff_required: bool = False


@dataclass(frozen=True)
class GeneratedResponse:
    should_reply: bool
    messages: tuple[str, ...]
    tone: str
    goal: str
    handoff_required: bool
    confidence: float

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> GeneratedResponse:
        should_reply = bool(value.get("should_reply", True))
        messages = _strings(value.get("messages"))
        if not should_reply:
            messages = ()
        elif not messages:
            raise ValueError("A reply response must include at least one message")
        goal = str(value.get("goal", "acknowledge"))
        if goal not in GOALS:
            raise ValueError(f"Unsupported response goal: {goal}")
        return cls(
            should_reply=should_reply,
            messages=messages,
            tone=str(value.get("tone", "neutral")),
            goal=goal,
            handoff_required=bool(value.get("handoff_required", False)),
            confidence=_number(value.get("confidence"), 0.5),
        )


@dataclass(frozen=True)
class BehaviorMessage:
    text: str
    delay_before_ms: int


@dataclass(frozen=True)
class BehaviorPlan:
    initial_read_delay_ms: int
    pre_typing_delay_ms: int
    typing_duration_ms: int
    messages: tuple[BehaviorMessage, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IncomingMessage:
    message_id: int
    contact_id: str
    text: str
    received_at: str


@dataclass(frozen=True)
class IncomingMessageGroup:
    group_id: str
    contact_id: str
    messages: tuple[IncomingMessage, ...]
    started_at: str
    completed_at: str

    @property
    def text(self) -> str:
        return "\n".join(message.text for message in self.messages)

    @property
    def last_message_id(self) -> int:
        return max(message.message_id for message in self.messages)


@dataclass(frozen=True)
class PromptPackage:
    instructions: str
    input_messages: tuple[dict[str, str], ...]
    inspection: dict[str, Any]
    estimated_tokens: int
    retrieved_example_ids: tuple[str, ...] = ()
    retrieved_example_provenance: tuple[str, ...] = ()
    retrieved_example_scores: tuple[float, ...] = ()


@dataclass(frozen=True)
class PipelineResult:
    analysis: InteractionAnalysis
    goal: GoalPlan
    response: GeneratedResponse
    behavior: BehaviorPlan
    prompt: PromptPackage
    draft_id: int | None = None


@dataclass(frozen=True)
class RuntimeEvent:
    event_type: str
    occurred_at: str
    conversation_id: str
    message_group_id: str = ""
    draft_id: int | None = None
    behavior_plan_id: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
