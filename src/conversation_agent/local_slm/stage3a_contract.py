"""Adaptive response contract V2 and evidence-based style planning."""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from conversation_agent.local_slm.models import GenerationResult
from conversation_agent.local_slm.stage25_contract import (
    ResponseContract,
    analyze_incoming_copy,
)

ResponseContractV1 = ResponseContract
HUMAN_STYLE_SOURCES = frozenset(
    {"human_manual", "human_edit", "human_fix", "imported_human_verified"}
)
AI_SOURCES = frozenset(
    {"model_rejected", "model_accepted_unedited", "benchmark", "synthetic"}
)
ActionV2 = Literal["reply", "no_reply", "reaction", "handoff"]
CasingMode = Literal["lowercase", "normal", "mixed"]


@dataclass(frozen=True)
class StyleEvidence:
    evidence_id: str
    source_message_id: str
    source_type: str
    timestamp: str
    contact_id: str | None
    relationship_id: str | None
    origin: Literal["human", "model"]
    confidence: float
    bubbles: tuple[str, ...]
    extracted_features: dict[str, Any] = field(default_factory=dict)

    @property
    def is_positive_human_evidence(self) -> bool:
        return self.origin == "human" and self.source_type in HUMAN_STYLE_SOURCES

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "bubbles": list(self.bubbles)}


@dataclass(frozen=True)
class StyleStatistics:
    sample_count: int
    confidence: float
    recency: str | None
    source_distribution: dict[str, int]
    features: dict[str, Any]
    evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class AgentStyleProfile:
    agent_id: str
    statistics: StyleStatistics

    def to_dict(self) -> dict[str, Any]:
        return {"agent_id": self.agent_id, "statistics": self.statistics.to_dict()}


@dataclass(frozen=True)
class RelationshipStyleProfile:
    agent_id: str
    relationship_id: str
    contact_id: str | None
    statistics: StyleStatistics

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "relationship_id": self.relationship_id,
            "contact_id": self.contact_id,
            "statistics": self.statistics.to_dict(),
        }


@dataclass(frozen=True)
class ConversationStyleSnapshot:
    conversation_id: str
    sample_count: int
    confidence: float
    features: dict[str, Any]
    evidence_ids: tuple[str, ...]
    emotional_context: str = "neutral"
    topic: str = "current"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "evidence_ids": list(self.evidence_ids)}


@dataclass(frozen=True)
class SemanticPlan:
    action: ActionV2
    goal: str
    required_information: tuple[str, ...]
    allowed_facts: tuple[str, ...]
    forbidden_claims: tuple[str, ...]
    allowed_commitments: tuple[str, ...]
    must_acknowledge: bool
    clarification_needed: bool
    handoff_strategy: str
    uncertainty_strategy: str
    sensitive_data_strategy: str
    reaction: str | None
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in (
            "required_information",
            "allowed_facts",
            "forbidden_claims",
            "allowed_commitments",
        ):
            value[key] = list(value[key])
        return value


@dataclass(frozen=True)
class AdaptiveStylePlan:
    source: Literal["adaptive", "neutral_fallback"]
    casing_mode: CasingMode
    casing_confidence: float
    final_punctuation_probability: float
    exclamation_probability: float
    preferred_bubble_range: tuple[int, int]
    bubble_distribution: dict[str, float]
    preferred_character_range: tuple[int, int]
    observed_percentiles: dict[str, float]
    preferred_question_range: tuple[int, int]
    question_style: str
    greeting_probability: float
    emoji_probability: float
    slang_level: float
    formality: float
    warmth: float
    directness: float
    sentence_completeness: float
    mirroring_strength: float
    preferred_lexicon: tuple[str, ...]
    avoided_lexicon: tuple[str, ...]
    typo_tolerance: float
    rhythm: str
    confidence: float
    evidence_ids: tuple[str, ...]
    source_weights: dict[str, float]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "casing": {
                "mode": self.casing_mode,
                "confidence": self.casing_confidence,
            },
            "punctuation": {
                "final_punctuation_probability": self.final_punctuation_probability,
                "exclamation_probability": self.exclamation_probability,
            },
            "bubbles": {
                "preferred_range": list(self.preferred_bubble_range),
                "distribution": self.bubble_distribution,
            },
            "length": {
                "preferred_character_range": list(self.preferred_character_range),
                "observed_percentiles": self.observed_percentiles,
            },
            "questions": {
                "preferred_range": list(self.preferred_question_range),
                "style": self.question_style,
            },
            "greeting_probability": self.greeting_probability,
            "emoji_probability": self.emoji_probability,
            "slang_level": self.slang_level,
            "formality": self.formality,
            "warmth": self.warmth,
            "directness": self.directness,
            "sentence_completeness": self.sentence_completeness,
            "mirroring_strength": self.mirroring_strength,
            "preferred_lexicon": list(self.preferred_lexicon),
            "avoided_lexicon": list(self.avoided_lexicon),
            "typo_tolerance": self.typo_tolerance,
            "rhythm": self.rhythm,
            "confidence": self.confidence,
            "evidence_ids": list(self.evidence_ids),
            "source_weights": self.source_weights,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class SafetyConstraints:
    no_unknown_facts: bool = True
    no_unapproved_promises: bool = True
    no_sensitive_data_collection: bool = True
    no_secrets: bool = True
    restrictions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "restrictions": list(self.restrictions)}


@dataclass(frozen=True)
class ResponseContractV2:
    semantic: SemanticPlan
    style: AdaptiveStylePlan
    safety: SafetyConstraints
    version: int = 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "semantic": self.semantic.to_dict(),
            "style": self.style.to_dict(),
            "safety": self.safety.to_dict(),
        }


@dataclass(frozen=True)
class ResolverConfig:
    agent_weight: float = 0.45
    relationship_weight: float = 0.30
    conversation_weight: float = 0.25
    relationship_context_weight: float = 0.10
    minimum_adaptive_confidence: float = 0.20
    minimum_stable_samples: int = 5
    maximum_mirroring_strength: float = 0.35

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StyleFeatureExtractor:
    """Extract observable style statistics only from confirmed human evidence."""

    def profile(
        self,
        evidence: tuple[StyleEvidence, ...],
    ) -> StyleStatistics:
        usable = tuple(item for item in evidence if item.is_positive_human_evidence)
        bubbles = [bubble for item in usable for bubble in item.bubbles if bubble.strip()]
        lengths = [len(item) for item in bubbles]
        words = [
            word
            for bubble in bubbles
            for word in re.findall(r"[0-9a-zа-яё]+", bubble.casefold())
        ]
        starts_lower = [_starts_lower(item) for item in bubbles]
        starts_upper = [_starts_upper(item) for item in bubbles]
        features: dict[str, Any] = {
            "lowercase_start_rate": _mean(starts_lower),
            "uppercase_start_rate": _mean(starts_upper),
            "final_period_rate": _mean([item.rstrip().endswith(".") for item in bubbles]),
            "question_rate": _mean(["?" in item for item in bubbles]),
            "exclamation_rate": _mean(["!" in item for item in bubbles]),
            "emoji_rate": _mean([bool(_emojis(item)) for item in bubbles]),
            "emojis": [key for key, _ in Counter(_all_emojis(bubbles)).most_common(10)],
            "average_length": _mean(lengths),
            "median_length": _percentile(lengths, 0.5),
            "p25_length": _percentile(lengths, 0.25),
            "p75_length": _percentile(lengths, 0.75),
            "p90_length": _percentile(lengths, 0.9),
            "average_bubbles": _mean([len(item.bubbles) for item in usable]),
            "bubble_lengths": lengths,
            "greeting_rate": _mean([_has_greeting(item) for item in bubbles]),
            "greetings": _common_matches(bubbles, r"(?i)^(привет|здравствуйте|добрый день)"),
            "ellipsis_rate": _mean(["..." in item or "…" in item for item in bubbles]),
            "dash_rate": _mean(["-" in item or "—" in item for item in bubbles]),
            "typo_rate": 0.0,
            "slang_level": _slang_level(words),
            "formality": _formality(words),
            "directness": _directness(bubbles),
            "emotionality": _emotionality(bubbles),
            "sentence_completeness": _sentence_completeness(bubbles),
            "clarifying_question_rate": _mean([item.rstrip().endswith("?") for item in bubbles]),
            "common_words": [
                key for key, _ in Counter(words).most_common(20) if len(key) > 1
            ],
            "common_short_replies": [
                key
                for key, _ in Counter(
                    item.casefold().strip(" .!?") for item in bubbles if len(item) <= 18
                ).most_common(10)
            ],
        }
        sample_count = len(bubbles)
        return StyleStatistics(
            sample_count=sample_count,
            confidence=round(min(1.0, math.log1p(sample_count) / math.log(21)), 6)
            if sample_count
            else 0.0,
            recency=max((item.timestamp for item in usable), default=None),
            source_distribution=dict(Counter(item.source_type for item in usable)),
            features=features,
            evidence_ids=tuple(item.evidence_id for item in usable),
        )

    def conversation_snapshot(
        self,
        *,
        conversation_id: str,
        messages: tuple[str, ...],
        emotional_context: str = "neutral",
        topic: str = "current",
    ) -> ConversationStyleSnapshot:
        features = _observable_message_features(messages)
        return ConversationStyleSnapshot(
            conversation_id=conversation_id,
            sample_count=len(messages),
            confidence=round(min(0.65, len(messages) * 0.22), 6),
            features=features,
            evidence_ids=tuple(f"conversation:{index}" for index in range(len(messages))),
            emotional_context=emotional_context,
            topic=topic,
        )


class AdaptiveStyleResolver:
    def __init__(self, config: ResolverConfig | None = None) -> None:
        self.config = config or ResolverConfig()

    def resolve(
        self,
        *,
        semantic: SemanticPlan,
        agent_profile: AgentStyleProfile,
        relationship_profile: RelationshipStyleProfile,
        conversation: ConversationStyleSnapshot,
        relationship_context: dict[str, Any],
    ) -> AdaptiveStylePlan:
        sources = (
            ("agent", agent_profile.statistics, self.config.agent_weight),
            (
                "relationship",
                relationship_profile.statistics,
                self.config.relationship_weight,
            ),
        )
        weighted = {
            name: base * stats.confidence
            for name, stats, base in sources
        }
        weighted["conversation"] = (
            self.config.conversation_weight * conversation.confidence
        )
        weighted["relationship_context"] = self.config.relationship_context_weight
        stable_agent = (
            agent_profile.statistics.sample_count >= self.config.minimum_stable_samples
        )
        style_confidence = min(1.0, sum(weighted.values()))
        fallback = style_confidence < self.config.minimum_adaptive_confidence
        casing_score = _weighted_feature(
            sources,
            conversation,
            "lowercase_start_rate",
            self.config.conversation_weight,
        )
        formal = _weighted_feature(
            sources,
            conversation,
            "formality",
            self.config.conversation_weight,
        )
        explicit_formality = float(relationship_context.get("formality", 0.5))
        formal = _clamp((formal + explicit_formality) / 2)
        if explicit_formality >= 0.72:
            casing: CasingMode = "normal"
            casing_reason = "formal relationship context favors normal casing"
        elif casing_score >= 0.62:
            casing = "lowercase"
            casing_reason = "recent and profiled lowercase evidence"
        elif casing_score <= 0.35:
            casing = "normal"
            casing_reason = "profiled normal casing evidence"
        else:
            casing = "mixed"
            casing_reason = "mixed casing evidence"
        if stable_agent and agent_profile.statistics.features.get(
            "lowercase_start_rate", 0.0
        ) < 0.35:
            casing = "normal"
            casing_reason = "stable agent profile outweighs transient contact casing"
        lengths = _profile_lengths(
            agent_profile.statistics,
            relationship_profile.statistics,
            conversation,
        )
        bubble_center = _weighted_feature(
            sources,
            conversation,
            "average_bubbles",
            self.config.conversation_weight,
        )
        if semantic.action in {"no_reply", "reaction"}:
            bubble_range = (0, 0)
            character_range = (0, 0)
        else:
            bubble_center = max(1.0, bubble_center or float(conversation.sample_count > 1) + 1)
            low_bubble = max(1, min(3, round(bubble_center)))
            bubble_range = (low_bubble, min(4, low_bubble + int(style_confidence < 0.45)))
            lower = max(8, int(lengths.get("p25", 24)))
            upper = max(lower + 8, int(lengths.get("p75", 100)))
            character_range = (lower, upper)
        punctuation = _weighted_feature(
            sources,
            conversation,
            "final_period_rate",
            self.config.conversation_weight,
        )
        emoji = _weighted_feature(
            sources,
            conversation,
            "emoji_rate",
            self.config.conversation_weight,
        )
        toxic = conversation.emotional_context in {"aggressive", "toxic", "conflict"}
        mirroring = min(
            self.config.maximum_mirroring_strength,
            conversation.confidence * (0.25 if toxic else 0.5),
        )
        evidence_ids = tuple(
            dict.fromkeys(
                agent_profile.statistics.evidence_ids
                + relationship_profile.statistics.evidence_ids
                + conversation.evidence_ids
            )
        )
        return AdaptiveStylePlan(
            source="neutral_fallback" if fallback else "adaptive",
            casing_mode="normal" if fallback and explicit_formality >= 0.5 else casing,
            casing_confidence=round(max(0.2, style_confidence), 6),
            final_punctuation_probability=round(_clamp(punctuation), 6),
            exclamation_probability=round(
                _clamp(
                    _weighted_feature(
                        sources,
                        conversation,
                        "exclamation_rate",
                        self.config.conversation_weight,
                    )
                ),
                6,
            ),
            preferred_bubble_range=bubble_range,
            bubble_distribution={str(bubble_range[0]): round(max(0.5, style_confidence), 6)},
            preferred_character_range=character_range,
            observed_percentiles=lengths,
            preferred_question_range=(
                (0, 1) if semantic.clarification_needed else (0, 0)
            ),
            question_style="soft" if formal >= 0.65 else "direct",
            greeting_probability=round(
                _weighted_feature(
                    sources,
                    conversation,
                    "greeting_rate",
                    self.config.conversation_weight,
                ),
                6,
            ),
            emoji_probability=round(0.0 if toxic else _clamp(emoji), 6),
            slang_level=round(
                0.0
                if toxic
                else _weighted_feature(
                    sources,
                    conversation,
                    "slang_level",
                    self.config.conversation_weight,
                ),
                6,
            ),
            formality=round(formal, 6),
            warmth=round(float(relationship_context.get("warmth", 0.5)), 6),
            directness=round(float(relationship_context.get("directness", 0.7)), 6),
            sentence_completeness=round(
                _weighted_feature(
                    sources,
                    conversation,
                    "sentence_completeness",
                    self.config.conversation_weight,
                ),
                6,
            ),
            mirroring_strength=round(mirroring, 6),
            preferred_lexicon=_preferred_lexicon(
                agent_profile.statistics,
                relationship_profile.statistics,
            ),
            avoided_lexicon=("как искусственный интеллект", "гарантирую", "обещаю"),
            typo_tolerance=0.0 if toxic else round(1.0 - formal, 6),
            rhythm="multi_bubble" if bubble_range[0] > 1 else "compact",
            confidence=round(style_confidence, 6),
            evidence_ids=evidence_ids,
            source_weights={key: round(value, 6) for key, value in weighted.items()},
            reasons=(
                casing_reason,
                "style is resolved for this turn only",
                "toxic or sensitive language is never mirrored" if toxic else "bounded mirroring",
            ),
        )


def migrate_v1_to_semantic(
    contract: ResponseContract,
    *,
    known_facts: tuple[str, ...] = (),
) -> SemanticPlan:
    return SemanticPlan(
        action=contract.action,
        goal=contract.goal,
        required_information=contract.required_facts,
        allowed_facts=tuple(dict.fromkeys(known_facts + contract.required_facts)),
        forbidden_claims=contract.forbidden_claims,
        allowed_commitments=(),
        must_acknowledge=False,
        clarification_needed=contract.max_questions > 0,
        handoff_strategy="request_human" if contract.handoff_required else "none",
        uncertainty_strategy="ask" if contract.max_questions > 0 else "state_missing_information",
        sensitive_data_strategy="refuse_collection",
        reaction=contract.reaction,
        confidence=contract.confidence,
    )


def migrate_v1_to_v2(
    contract: ResponseContract,
    *,
    resolver: AdaptiveStyleResolver,
    agent_profile: AgentStyleProfile,
    relationship_profile: RelationshipStyleProfile,
    conversation: ConversationStyleSnapshot,
    relationship_context: dict[str, Any],
    known_facts: tuple[str, ...] = (),
) -> ResponseContractV2:
    semantic = migrate_v1_to_semantic(contract, known_facts=known_facts)
    return ResponseContractV2(
        semantic=semantic,
        style=resolver.resolve(
            semantic=semantic,
            agent_profile=agent_profile,
            relationship_profile=relationship_profile,
            conversation=conversation,
            relationship_context=relationship_context,
        ),
        safety=SafetyConstraints(restrictions=contract.forbidden_claims),
    )


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple[str, ...]
    checks: dict[str, bool]

    def to_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "errors": list(self.errors), "checks": self.checks}


class HardSemanticValidator:
    def validate(
        self,
        contract: ResponseContractV2,
        output: GenerationResult,
        *,
        incoming_messages: tuple[str, ...] = (),
    ) -> ValidationResult:
        semantic = contract.semantic
        text = "\n".join(output.messages)
        checks = {
            "schema": output.action in {"reply", "no_reply", "reaction", "handoff"},
            "action": output.action == semantic.action,
            "no_reply": semantic.action != "no_reply" or not output.messages,
            "reaction": semantic.action != "reaction"
            or output.reaction == semantic.reaction,
            "handoff": (output.action == "handoff")
            == (semantic.handoff_strategy != "none"),
            "required_meaning": all(
                item.casefold() in text.casefold()
                for item in semantic.required_information
            ),
            "forbidden_claims": not any(
                item.casefold() in text.casefold()
                for item in semantic.forbidden_claims
            ),
            "allowed_commitments": not _unapproved_commitment(
                text,
                semantic.allowed_commitments,
            ),
            "sensitive_data": not _requests_sensitive_data(text),
            "empty_reply": semantic.action != "reply" or bool(output.messages),
            "not_damaged": not bool(
                re.search(r"(?i)<\/?think|reasoning_content|\ufffd", text)
            ),
            "adaptive_critical_length": len(text)
            <= max(
                contract.style.preferred_character_range[1] * 3,
                contract.style.preferred_character_range[0] + 80,
            ),
            "no_incoming_copy": not analyze_incoming_copy(
                output.messages,
                incoming_messages,
                allowed_facts=contract.semantic.allowed_facts,
            ),
        }
        errors = tuple(key for key, passed in checks.items() if not passed)
        return ValidationResult(not errors, errors, checks)


class SafetyValidator:
    def validate(
        self,
        contract: ResponseContractV2,
        output: GenerationResult,
    ) -> ValidationResult:
        text = "\n".join(output.messages)
        checks = {
            "secrets": not _contains_secret(text),
            "credentials": not bool(
                re.search(r"(?i)\b(?:password|пароль|api[_ -]?key|token)\b\s*[:=]", text)
            ),
            "personal_data": not _requests_sensitive_data(text),
            "unapproved_promises": not _unapproved_commitment(
                text,
                contract.semantic.allowed_commitments,
            ),
            "restricted_actions": not any(
                item.casefold() in text.casefold()
                for item in contract.safety.restrictions
            ),
        }
        errors = tuple(key for key, passed in checks.items() if not passed)
        return ValidationResult(not errors, errors, checks)


@dataclass(frozen=True)
class SoftStyleResult:
    metrics: dict[str, float]
    deviations: tuple[str, ...]

    @property
    def fit(self) -> float:
        return round(statistics.fmean(self.metrics.values()), 6) if self.metrics else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fit": self.fit,
            "metrics": self.metrics,
            "deviations": list(self.deviations),
            "provider_failure": False,
        }


class SoftStyleEvaluator:
    def evaluate(
        self,
        plan: AdaptiveStylePlan,
        output: GenerationResult,
    ) -> SoftStyleResult:
        messages = output.messages
        text = "\n".join(messages)
        char_count = sum(len(item) for item in messages)
        question_count = text.count("?")
        casing_fit = 1.0
        if messages and plan.casing_mode == "lowercase":
            casing_fit = float(_starts_lower(messages[0]))
        elif messages and plan.casing_mode == "normal":
            casing_fit = float(not _starts_lower(messages[0]))
        metrics = {
            "casing_fit": casing_fit,
            "punctuation_fit": 1.0
            - abs(float(text.rstrip().endswith((".", "!", "?"))) - plan.final_punctuation_probability),
            "bubble_distribution_fit": float(
                plan.preferred_bubble_range[0]
                <= len(messages)
                <= plan.preferred_bubble_range[1]
            ),
            "length_distribution_fit": float(
                plan.preferred_character_range[0]
                <= char_count
                <= plan.preferred_character_range[1]
            ),
            "greeting_fit": 1.0
            - abs(float(bool(messages and _has_greeting(messages[0]))) - plan.greeting_probability),
            "emoji_fit": 1.0 - abs(float(bool(_emojis(text))) - plan.emoji_probability),
            "question_fit": float(
                plan.preferred_question_range[0]
                <= question_count
                <= plan.preferred_question_range[1]
            ),
            "formality_fit": 1.0 - abs(_formality(re.findall(r"[a-zа-яё]+", text.casefold())) - plan.formality),
            "warmth_fit": 1.0,
            "directness_fit": 1.0,
            "lexical_style_fit": float(
                not any(item.casefold() in text.casefold() for item in plan.avoided_lexicon)
            ),
            "conversational_rhythm_fit": float(
                (plan.rhythm == "compact" and len(messages) <= 1)
                or (plan.rhythm == "multi_bubble" and len(messages) > 1)
            ),
        }
        metrics = {key: round(_clamp(value), 6) for key, value in metrics.items()}
        deviations = tuple(key for key, value in metrics.items() if value < 0.5)
        return SoftStyleResult(metrics=metrics, deviations=deviations)


def response_contract_v2_schema() -> dict[str, Any]:
    """JSON Schema for stored V2 contracts; no universal style choice is fixed."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["version", "semantic", "style", "safety"],
        "properties": {
            "version": {"const": 2},
            "semantic": {"type": "object"},
            "style": {"type": "object"},
            "safety": {"type": "object"},
        },
    }


def empty_style_statistics() -> StyleStatistics:
    return StyleStatistics(0, 0.0, None, {}, {}, ())


def evidence_from_human_message(
    *,
    evidence_id: str,
    message_id: str,
    source_type: str,
    bubbles: tuple[str, ...],
    contact_id: str | None = None,
    relationship_id: str | None = None,
    timestamp: str | None = None,
) -> StyleEvidence:
    if source_type not in HUMAN_STYLE_SOURCES:
        raise ValueError(f"{source_type} cannot establish human style evidence")
    return StyleEvidence(
        evidence_id=evidence_id,
        source_message_id=message_id,
        source_type=source_type,
        timestamp=timestamp or datetime.now(UTC).isoformat(),
        contact_id=contact_id,
        relationship_id=relationship_id,
        origin="human",
        confidence=1.0 if source_type == "human_fix" else 0.9,
        bubbles=bubbles,
        extracted_features=_observable_message_features(bubbles),
    )


def _observable_message_features(messages: tuple[str, ...]) -> dict[str, Any]:
    lengths = [len(item) for item in messages if item.strip()]
    return {
        "lowercase_start_rate": _mean([_starts_lower(item) for item in messages]),
        "uppercase_start_rate": _mean([_starts_upper(item) for item in messages]),
        "final_period_rate": _mean([item.rstrip().endswith(".") for item in messages]),
        "question_rate": _mean(["?" in item for item in messages]),
        "exclamation_rate": _mean(["!" in item for item in messages]),
        "emoji_rate": _mean([bool(_emojis(item)) for item in messages]),
        "average_length": _mean(lengths),
        "median_length": _percentile(lengths, 0.5),
        "p25_length": _percentile(lengths, 0.25),
        "p75_length": _percentile(lengths, 0.75),
        "p90_length": _percentile(lengths, 0.9),
        "average_bubbles": float(max(1, len(messages))) if messages else 0.0,
        "greeting_rate": _mean([_has_greeting(item) for item in messages]),
        "ellipsis_rate": _mean(["..." in item or "…" in item for item in messages]),
        "dash_rate": _mean(["-" in item or "—" in item for item in messages]),
        "slang_level": _slang_level(
            [
                word
                for item in messages
                for word in re.findall(r"[a-zа-яё]+", item.casefold())
            ]
        ),
        "formality": 0.5,
        "directness": 0.7,
        "sentence_completeness": _sentence_completeness(list(messages)),
    }


def _weighted_feature(
    sources: tuple[tuple[str, StyleStatistics, float], ...],
    conversation: ConversationStyleSnapshot,
    key: str,
    conversation_weight: float,
) -> float:
    values: list[tuple[float, float]] = []
    for _, stats, weight in sources:
        value = stats.features.get(key)
        if isinstance(value, (int, float)):
            values.append((float(value), weight * stats.confidence))
    conversation_value = conversation.features.get(key)
    if isinstance(conversation_value, (int, float)):
        values.append(
            (float(conversation_value), conversation_weight * conversation.confidence)
        )
    denominator = sum(weight for _, weight in values)
    return sum(value * weight for value, weight in values) / denominator if denominator else 0.5


def _profile_lengths(
    agent: StyleStatistics,
    relationship: StyleStatistics,
    conversation: ConversationStyleSnapshot,
) -> dict[str, float]:
    values = []
    for source in (agent.features, relationship.features, conversation.features):
        for key in ("p25_length", "median_length", "p75_length", "p90_length"):
            if isinstance(source.get(key), (int, float)) and source[key]:
                values.append(float(source[key]))
    if not values:
        observed = float(conversation.features.get("median_length", 40) or 40)
        return {
            "p25": round(max(8, observed * 0.7), 3),
            "p50": round(observed, 3),
            "p75": round(max(20, observed * 1.6), 3),
            "p90": round(max(30, observed * 2.0), 3),
        }
    return {
        "p25": round(_percentile(values, 0.25), 3),
        "p50": round(_percentile(values, 0.5), 3),
        "p75": round(_percentile(values, 0.75), 3),
        "p90": round(_percentile(values, 0.9), 3),
    }


def _preferred_lexicon(
    agent: StyleStatistics,
    relationship: StyleStatistics,
) -> tuple[str, ...]:
    words = list(agent.features.get("common_words", []))
    words.extend(relationship.features.get("common_words", []))
    return tuple(key for key, _ in Counter(words).most_common(12))


def _starts_lower(value: str) -> bool:
    match = re.search(r"[A-Za-zА-Яа-яЁё]", value)
    return bool(match and match.group(0).islower())


def _starts_upper(value: str) -> bool:
    match = re.search(r"[A-Za-zА-Яа-яЁё]", value)
    return bool(match and match.group(0).isupper())


def _has_greeting(value: str) -> bool:
    return bool(re.match(r"(?i)^\s*(привет|здравствуйте|добрый\s+(?:день|вечер|утро))\b", value))


def _emojis(value: str) -> list[str]:
    return re.findall("[\U0001F300-\U0001FAFF\u2600-\u27BF]", value)


def _all_emojis(values: list[str]) -> list[str]:
    return [emoji for value in values for emoji in _emojis(value)]


def _common_matches(values: list[str], pattern: str) -> list[str]:
    matches = []
    for value in values:
        match = re.search(pattern, value)
        if match:
            matches.append(match.group(1).casefold())
    return [
        key
        for key, _ in Counter(matches).most_common(10)
    ]


def _slang_level(words: list[str]) -> float:
    slang = {"ага", "ок", "окей", "го", "ща", "чё", "че", "понял", "прив"}
    return _mean([word in slang for word in words])


def _formality(words: list[str]) -> float:
    if not words:
        return 0.5
    formal = {"вы", "вас", "вам", "пожалуйста", "здравствуйте", "благодарю"}
    informal = {"ты", "тебе", "тебя", "привет", "ага", "го"}
    return _clamp(0.5 + 0.12 * sum(word in formal for word in words) - 0.12 * sum(word in informal for word in words))


def _directness(values: list[str]) -> float:
    return _clamp(1.0 - _mean(["пожалуйста" in item.casefold() for item in values]) * 0.3)


def _emotionality(values: list[str]) -> float:
    return _clamp(_mean([bool(_emojis(item)) or "!" in item for item in values]))


def _sentence_completeness(values: list[str]) -> float:
    return _mean(
        [
            len(re.findall(r"[a-zа-яё]+", item.casefold())) >= 3
            or item.rstrip().endswith((".", "!", "?"))
            for item in values
        ]
    )


def _unapproved_commitment(text: str, allowed: tuple[str, ...]) -> bool:
    markers = re.findall(
        r"(?i)\b(?:гарантирую|обещаю|точно сделаю|отправлю сегодня|верну деньги)\b",
        text,
    )
    return any(
        not any(marker.casefold() in item.casefold() for item in allowed)
        for marker in markers
    )


def _requests_sensitive_data(text: str) -> bool:
    return bool(
        re.search(
            r"(?i)(?:пришлите|отправьте|укажите|нужен)\s+"
            r"(?:пароль|паспорт|cvv|номер карты|код из смс)",
            text,
        )
    )


def _contains_secret(text: str) -> bool:
    return bool(
        re.search(
            r"(?i)(?:sk-[a-z0-9_-]{16,}|[0-9]{8,}:[A-Za-z0-9_-]{20,})",
            text,
        )
    )


def _mean(values: list[Any]) -> float:
    return round(sum(float(item) for item in values) / len(values), 6) if values else 0.0


def _percentile(values: list[int] | list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(item) for item in values)
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(len(ordered) - 1, lower + 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
