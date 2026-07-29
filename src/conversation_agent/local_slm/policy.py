"""Dialogue policy implementations for local generation."""

from __future__ import annotations

import json
from typing import ClassVar, Protocol, cast

from conversation_agent.local_slm.models import Action, DialogueDecision, DialoguePolicyInput


class DialoguePolicy(Protocol):
    def decide(self, value: DialoguePolicyInput) -> DialogueDecision:
        """Return a compact structured decision without generating final text."""
        ...


class FakeDialoguePolicy:
    def __init__(self, decision: DialogueDecision | None = None) -> None:
        self.decision = decision

    def decide(self, value: DialoguePolicyInput) -> DialogueDecision:
        if self.decision is not None:
            return self.decision
        return DialogueDecision(
            action="reply",
            intent="service_inquiry",
            interaction_mode="business_informal",
            emotion="neutral",
            urgency=0.2,
            needs_handoff=False,
            needs_generation=True,
            suggested_bubble_count=2,
            confidence=0.9,
            reason="fake policy default",
        )


class RuleBasedDialoguePolicy:
    acknowledgements: ClassVar[set[str]] = {"ok", "ок", "понял", "поняла", "спасибо", "thanks", "ага"}
    reaction_markers: ClassVar[set[str]] = {"👍", "❤️", "🔥", "😂"}
    handoff_markers: ClassVar[set[str]] = {
        "юрист",
        "договор",
        "оплата",
        "счет",
        "счёт",
        "банк",
        "паспорт",
    }
    question_markers: ClassVar[set[str]] = {"?", "нуж", "мож", "как", "сколько", "бот", "автомат"}

    def decide(self, value: DialoguePolicyInput) -> DialogueDecision:
        text = " ".join(value.messages).strip().lower()
        if not value.permissions.get("reply", True):
            return _decision("wait", "paused", False, 0.95, "reply permission disabled")
        if not text:
            return _decision("no_reply", "empty", False, 0.95, "empty message")
        if text in self.acknowledgements:
            return _decision("no_reply", "acknowledgement", False, 0.88, "short ack")
        if text in self.reaction_markers:
            return _decision("reaction", "reaction_only", False, 0.86, "emoji-only message")
        if any(marker in text for marker in self.handoff_markers):
            return DialogueDecision(
                action="handoff",
                intent="sensitive_or_commercial_commitment",
                interaction_mode="guarded",
                emotion=_emotion(text),
                urgency=0.7,
                needs_handoff=True,
                needs_generation=False,
                suggested_bubble_count=0,
                confidence=0.78,
                reason="sensitive commitment keyword",
            )
        if any(marker in text for marker in self.question_markers):
            return DialogueDecision(
                action="reply",
                intent="service_inquiry",
                interaction_mode="business_informal",
                emotion=_emotion(text),
                urgency=0.3,
                needs_handoff=False,
                needs_generation=True,
                suggested_bubble_count=2,
                confidence=0.82,
                reason="question/service marker",
            )
        return _decision("reply", "casual_message", True, 0.64, "safe default reply")


class LocalClassifierDialoguePolicy:
    """Tiny file-backed classifier stub for experiments without heavy dependencies."""

    def __init__(self, rules_json: str | None = None) -> None:
        self.rules = json.loads(rules_json) if rules_json else []
        self.fallback = RuleBasedDialoguePolicy()

    def decide(self, value: DialoguePolicyInput) -> DialogueDecision:
        text = " ".join(value.messages).lower()
        for rule in self.rules:
            contains = str(rule.get("contains", "")).lower()
            if contains and contains in text:
                return DialogueDecision(
                    action=rule.get("action", "reply"),
                    intent=rule.get("intent", "configured_rule"),
                    interaction_mode=rule.get("interaction_mode", "business_informal"),
                    emotion=_emotion(text),
                    urgency=float(rule.get("urgency", 0.2)),
                    needs_handoff=bool(rule.get("needs_handoff", False)),
                    needs_generation=rule.get("action", "reply") == "reply",
                    suggested_bubble_count=int(rule.get("suggested_bubble_count", 1)),
                    confidence=float(rule.get("confidence", 0.75)),
                    reason=f"local classifier rule: {contains}",
                )
        return self.fallback.decide(value)


def safe_policy_decision(policy: DialoguePolicy, value: DialoguePolicyInput) -> DialogueDecision:
    try:
        return policy.decide(value)
    except Exception as exc:  # noqa: BLE001
        return DialogueDecision(
            action="handoff",
            intent="policy_error",
            interaction_mode="safe_fallback",
            emotion="unknown",
            urgency=1.0,
            needs_handoff=True,
            needs_generation=False,
            suggested_bubble_count=0,
            confidence=0.0,
            reason=f"{type(exc).__name__}: {exc}",
        )


def _decision(
    action: str,
    intent: str,
    needs_generation: bool,
    confidence: float,
    reason: str,
) -> DialogueDecision:
    return DialogueDecision(
        action=cast(Action, action),
        intent=intent,
        interaction_mode="casual" if intent != "service_inquiry" else "business_informal",
        emotion="neutral",
        urgency=0.1,
        needs_handoff=action == "handoff",
        needs_generation=needs_generation,
        suggested_bubble_count=1 if needs_generation else 0,
        confidence=confidence,
        reason=reason,
    )


def _emotion(text: str) -> str:
    if any(item in text for item in ("зл", "бес", "сука", "нах", "бля")):
        return "irritated"
    if any(item in text for item in ("спасибо", "класс", "супер")):
        return "positive"
    return "neutral"
