"""Response planning and hard renderer validation for Stage 2.5."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any, Literal, cast

from conversation_agent.local_slm.models import GenerationResult

ContractAction = Literal["reply", "no_reply", "reaction", "handoff"]
CONTRACT_ACTIONS: tuple[ContractAction, ...] = (
    "reply",
    "no_reply",
    "reaction",
    "handoff",
)


class ResponseContractError(ValueError):
    """Raised when a policy returns an invalid response plan."""

    def __init__(self, errors: list[str] | tuple[str, ...]) -> None:
        self.errors = tuple(errors)
        super().__init__(", ".join(self.errors))


@dataclass(frozen=True)
class ResponseContract:
    action: ContractAction
    goal: str
    required_facts: tuple[str, ...]
    forbidden_claims: tuple[str, ...]
    target_bubble_count: int
    max_bubble_count: int
    max_total_characters: int
    max_characters_per_bubble: int
    max_questions: int
    tone: str
    formality: float
    warmth: float
    directness: float
    allow_greeting: bool
    allow_emoji: bool
    reaction: str | None
    handoff_required: bool
    confidence: float

    def __post_init__(self) -> None:
        errors = validate_response_contract(self)
        if errors:
            raise ResponseContractError(errors)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ResponseContract:
        action = str(value.get("action", ""))
        if action not in CONTRACT_ACTIONS:
            raise ResponseContractError(["invalid_action"])
        required = value.get("required_facts", [])
        forbidden = value.get("forbidden_claims", [])
        if not isinstance(required, list) or not all(
            isinstance(item, str) for item in required
        ):
            raise ResponseContractError(["required_facts_not_string_array"])
        if not isinstance(forbidden, list) or not all(
            isinstance(item, str) for item in forbidden
        ):
            raise ResponseContractError(["forbidden_claims_not_string_array"])
        return cls(
            action=cast(ContractAction, action),
            goal=str(value.get("goal", "")).strip(),
            required_facts=tuple(item.strip() for item in required if item.strip()),
            forbidden_claims=tuple(item.strip() for item in forbidden if item.strip()),
            target_bubble_count=_strict_int(value, "target_bubble_count"),
            max_bubble_count=_strict_int(value, "max_bubble_count"),
            max_total_characters=_strict_int(value, "max_total_characters"),
            max_characters_per_bubble=_strict_int(
                value,
                "max_characters_per_bubble",
            ),
            max_questions=_strict_int(value, "max_questions"),
            tone=str(value.get("tone", "")).strip(),
            formality=_strict_float(value, "formality"),
            warmth=_strict_float(value, "warmth"),
            directness=_strict_float(value, "directness"),
            allow_greeting=_strict_bool(value, "allow_greeting"),
            allow_emoji=_strict_bool(value, "allow_emoji"),
            reaction=(
                str(value["reaction"]).strip()
                if value.get("reaction") is not None
                else None
            ),
            handoff_required=_strict_bool(value, "handoff_required"),
            confidence=_strict_float(value, "confidence"),
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["required_facts"] = list(self.required_facts)
        value["forbidden_claims"] = list(self.forbidden_claims)
        return value


@dataclass(frozen=True)
class RendererValidation:
    valid: bool
    errors: tuple[str, ...]
    contract_compliance: dict[str, bool]
    copy_analysis: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "contract_compliance": dict(self.contract_compliance),
            "copy_analysis": list(self.copy_analysis),
        }


class LengthPlanner:
    """Plan Telegram-sized limits without benchmark evaluation labels."""

    def recommend(
        self,
        *,
        action: ContractAction,
        conversation: tuple[dict[str, str], ...],
        relationship: dict[str, Any],
        known_facts: tuple[str, ...],
        goal: str,
    ) -> dict[str, int]:
        incoming = " ".join(
            turn.get("content", "")
            for turn in conversation
            if turn.get("role") in {"contact", "user"}
        )
        message_count = sum(
            turn.get("role") in {"contact", "user"} for turn in conversation
        )
        complexity = (
            len(incoming) // 100
            + max(0, message_count - 1)
            + min(2, len(known_facts))
            + int(len(goal) > 40)
        )
        urgent = bool(
            re.search(r"(?i)\b(?:срочно|быстро|сегодня|горит|немедленно)\b", incoming)
        )
        formal = float(relationship.get("formality", 0.5))
        if action == "no_reply":
            return _lengths(0, 0, 0, 0, 0)
        if action == "reaction":
            return _lengths(0, 0, 0, 0, 0)
        if action == "handoff":
            return _lengths(1, 1, 90, 90, 0)
        target = 1 if complexity <= 1 else 2 if complexity <= 4 else 3
        max_bubbles = min(4, target + (1 if complexity >= 3 else 0))
        base_chars = 95 + complexity * 28 + int(formal >= 0.7) * 25
        if urgent:
            base_chars = max(70, base_chars - 25)
        max_total = min(360, max(70, base_chars))
        per_bubble = min(180, max(60, (max_total + target - 1) // target))
        return _lengths(target, max_bubbles, max_total, per_bubble, 1)


def validate_response_contract(contract: ResponseContract) -> tuple[str, ...]:
    errors: list[str] = []
    if not contract.goal:
        errors.append("empty_goal")
    if not contract.tone:
        errors.append("empty_tone")
    if len(set(contract.required_facts)) != len(contract.required_facts):
        errors.append("duplicate_required_facts")
    if len(set(contract.forbidden_claims)) != len(contract.forbidden_claims):
        errors.append("duplicate_forbidden_claims")
    overlap = {
        item.casefold() for item in contract.required_facts
    } & {item.casefold() for item in contract.forbidden_claims}
    if overlap:
        errors.append("required_forbidden_overlap")
    for field_name in ("formality", "warmth", "directness", "confidence"):
        if not 0.0 <= float(getattr(contract, field_name)) <= 1.0:
            errors.append(f"{field_name}_out_of_range")
    if not 0 <= contract.target_bubble_count <= 3:
        errors.append("target_bubble_count_out_of_range")
    if not 0 <= contract.max_bubble_count <= 4:
        errors.append("max_bubble_count_out_of_range")
    if contract.target_bubble_count > contract.max_bubble_count:
        errors.append("target_exceeds_max_bubbles")
    if not 0 <= contract.max_total_characters <= 500:
        errors.append("max_total_characters_out_of_range")
    if not 0 <= contract.max_characters_per_bubble <= 250:
        errors.append("max_characters_per_bubble_out_of_range")
    if not 0 <= contract.max_questions <= 2:
        errors.append("max_questions_out_of_range")
    if contract.action == "no_reply":
        errors.extend(
            _require(
                {
                    "no_reply_target_bubbles": contract.target_bubble_count == 0,
                    "no_reply_max_bubbles": contract.max_bubble_count == 0,
                    "no_reply_total_characters": contract.max_total_characters == 0,
                    "no_reply_per_bubble_characters": (
                        contract.max_characters_per_bubble == 0
                    ),
                    "no_reply_questions": contract.max_questions == 0,
                    "no_reply_reaction": contract.reaction is None,
                    "no_reply_handoff": not contract.handoff_required,
                }
            )
        )
    elif contract.action == "reaction":
        errors.extend(
            _require(
                {
                    "reaction_missing": bool(contract.reaction),
                    "reaction_handoff": not contract.handoff_required,
                    "reaction_target_bubbles": contract.target_bubble_count <= 1,
                    "reaction_max_bubbles": contract.max_bubble_count <= 1,
                    "reaction_questions": contract.max_questions == 0,
                    "reaction_zero_text_limits": (
                        contract.target_bubble_count > 0
                        or (
                            contract.max_total_characters == 0
                            and contract.max_characters_per_bubble == 0
                        )
                    ),
                }
            )
        )
    elif contract.action == "handoff":
        errors.extend(
            _require(
                {
                    "handoff_required_false": contract.handoff_required,
                    "handoff_reaction": contract.reaction is None,
                    "handoff_target_bubbles": contract.target_bubble_count <= 1,
                    "handoff_max_bubbles": contract.max_bubble_count <= 1,
                    "handoff_questions": contract.max_questions == 0,
                }
            )
        )
    elif contract.action == "reply":
        errors.extend(
            _require(
                {
                    "reply_target_bubbles": 1 <= contract.target_bubble_count <= 3,
                    "reply_max_bubbles": (
                        contract.target_bubble_count
                        <= contract.max_bubble_count
                        <= 4
                    ),
                    "reply_total_characters": (
                        20 <= contract.max_total_characters <= 500
                    ),
                    "reply_per_bubble_characters": (
                        10 <= contract.max_characters_per_bubble <= 250
                    ),
                    "reply_questions": contract.max_questions <= 1,
                    "reply_reaction": contract.reaction is None,
                    "reply_handoff": not contract.handoff_required,
                }
            )
        )
    return tuple(dict.fromkeys(errors))


def response_contract_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "action",
            "goal",
            "required_facts",
            "forbidden_claims",
            "target_bubble_count",
            "max_bubble_count",
            "max_total_characters",
            "max_characters_per_bubble",
            "max_questions",
            "tone",
            "formality",
            "warmth",
            "directness",
            "allow_greeting",
            "allow_emoji",
            "reaction",
            "handoff_required",
            "confidence",
        ],
        "properties": {
            "action": {"type": "string", "enum": list(CONTRACT_ACTIONS)},
            "goal": {"type": "string", "minLength": 1},
            "required_facts": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 8,
            },
            "forbidden_claims": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 12,
            },
            "target_bubble_count": {"type": "integer", "minimum": 0, "maximum": 3},
            "max_bubble_count": {"type": "integer", "minimum": 0, "maximum": 4},
            "max_total_characters": {
                "type": "integer",
                "minimum": 0,
                "maximum": 500,
            },
            "max_characters_per_bubble": {
                "type": "integer",
                "minimum": 0,
                "maximum": 250,
            },
            "max_questions": {"type": "integer", "minimum": 0, "maximum": 2},
            "tone": {"type": "string", "minLength": 1},
            "formality": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "warmth": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "directness": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "allow_greeting": {"type": "boolean"},
            "allow_emoji": {"type": "boolean"},
            "reaction": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "handoff_required": {"type": "boolean"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
    }


def validate_renderer_output(
    contract: ResponseContract,
    result: GenerationResult,
    *,
    incoming_messages: tuple[str, ...],
    allowed_facts: tuple[str, ...] = (),
) -> RendererValidation:
    messages = tuple(message.strip() for message in result.messages if message.strip())
    text = "\n".join(messages)
    total_characters = sum(len(message) for message in messages)
    copy_analysis = analyze_incoming_copy(
        messages,
        incoming_messages,
        allowed_facts=tuple(dict.fromkeys(contract.required_facts + allowed_facts)),
    )
    compliance = {
        "action": result.action == contract.action,
        "bubble_count": len(messages) <= contract.max_bubble_count,
        "target_bubble_count": len(messages) == contract.target_bubble_count,
        "total_characters": total_characters <= contract.max_total_characters,
        "characters_per_bubble": all(
            len(message) <= contract.max_characters_per_bubble for message in messages
        ),
        "question_count": text.count("?") <= contract.max_questions,
        "greeting": contract.allow_greeting or not _contains_greeting(messages),
        "emoji": contract.allow_emoji or not _contains_emoji(text),
        "forbidden_claims": not _matching_phrases(text, contract.forbidden_claims),
        "required_facts": not contract.required_facts
        or len(_matching_phrases(text, contract.required_facts))
        == len(contract.required_facts),
        "thinking": not re.search(r"(?i)<\/?think|reasoning_content", text),
        "assistant_meta": not _contains_assistant_meta(text),
        "heading_or_list": not re.search(
            r"(?m)^\s*(?:#{1,6}\s+|\d+[.)]\s+|[-*]\s+)",
            text,
        ),
        "repeated_incoming_question": not copy_analysis,
        "non_empty_reply": contract.action != "reply" or bool(messages),
        "no_reply_empty": contract.action != "no_reply"
        or (not messages and result.reaction is None),
        "handoff": (
            result.handoff_required == contract.handoff_required
            and (result.action == "handoff") == contract.handoff_required
        ),
        "reaction": (
            (result.reaction == contract.reaction)
            if contract.action == "reaction"
            else result.reaction is None
        ),
    }
    errors = tuple(key for key, valid in compliance.items() if not valid)
    return RendererValidation(
        valid=not errors,
        errors=errors,
        contract_compliance=compliance,
        copy_analysis=copy_analysis,
    )


def renderer_response_schema(contract: ResponseContract) -> dict[str, Any]:
    min_messages = 1 if contract.action == "reply" else 0
    max_messages = contract.max_bubble_count
    return {
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
            "action": {"type": "string", "const": contract.action},
            "messages": {
                "type": "array",
                "items": {
                    "type": "string",
                    "maxLength": max(1, contract.max_characters_per_bubble),
                },
                "minItems": min_messages,
                "maxItems": max_messages,
            },
            "reaction": (
                {"type": "string", "const": contract.reaction}
                if contract.action == "reaction" and contract.reaction
                else {"type": "null"}
            ),
            "handoff_required": {
                "type": "boolean",
                "const": contract.handoff_required,
            },
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
    }


def _strict_int(value: dict[str, Any], key: str) -> int:
    result = value.get(key)
    if not isinstance(result, int) or isinstance(result, bool):
        raise ResponseContractError([f"{key}_not_integer"])
    return result


def _strict_float(value: dict[str, Any], key: str) -> float:
    result = value.get(key)
    if not isinstance(result, (int, float)) or isinstance(result, bool):
        raise ResponseContractError([f"{key}_not_number"])
    return float(result)


def _strict_bool(value: dict[str, Any], key: str) -> bool:
    result = value.get(key)
    if not isinstance(result, bool):
        raise ResponseContractError([f"{key}_not_boolean"])
    return result


def _require(checks: dict[str, bool]) -> list[str]:
    return [name for name, valid in checks.items() if not valid]


def _lengths(
    target_bubbles: int,
    max_bubbles: int,
    max_total: int,
    max_per_bubble: int,
    max_questions: int,
) -> dict[str, int]:
    return {
        "target_bubble_count": target_bubbles,
        "max_bubble_count": max_bubbles,
        "max_total_characters": max_total,
        "max_characters_per_bubble": max_per_bubble,
        "max_questions": max_questions,
    }


def _matching_phrases(text: str, phrases: tuple[str, ...]) -> list[str]:
    lowered = text.casefold()
    return [phrase for phrase in phrases if phrase.casefold() in lowered]


def _contains_greeting(messages: tuple[str, ...]) -> bool:
    if not messages:
        return False
    return bool(
        re.match(
            r"(?i)^\s*(?:здравствуй(?:те)?|привет|добрый\s+(?:день|вечер|утро))\b",
            messages[0],
        )
    )


def _contains_emoji(text: str) -> bool:
    return bool(
        re.search(
            "[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF\u2600-\u27BF]",
            text,
        )
    )


def _contains_assistant_meta(text: str) -> bool:
    lowered = text.casefold()
    markers = (
        "как искусственный интеллект",
        "я являюсь виртуальным помощником",
        "благодарю вас за обращение",
        "если у вас есть дополнительные вопросы",
        "надеюсь, эта информация была полезной",
    )
    return any(marker in lowered for marker in markers)


def _repeats_incoming_question(
    messages: tuple[str, ...],
    incoming_messages: tuple[str, ...],
) -> bool:
    normalized_incoming = {
        _normalize_question(message)
        for message in incoming_messages
        if len(_normalize_question(message)) >= 18
    }
    return any(
        _normalize_question(message) in normalized_incoming
        for message in messages
        if len(_normalize_question(message)) >= 18
    )


def analyze_incoming_copy(
    messages: tuple[str, ...],
    incoming_messages: tuple[str, ...],
    *,
    allowed_facts: tuple[str, ...] = (),
) -> tuple[dict[str, Any], ...]:
    """Detect exact, near and partial copies without flagging short fact reuse."""
    findings: list[dict[str, Any]] = []
    allowed_tokens = set(_copy_tokens(" ".join(allowed_facts)))
    for output in messages:
        output_normalized = _normalize_copy(output)
        output_tokens = set(_copy_tokens(output))
        if len(output_normalized) < 10:
            continue
        for incoming in incoming_messages:
            incoming_normalized = _normalize_copy(incoming)
            if len(incoming_normalized) < 10:
                continue
            matcher = SequenceMatcher(None, output_normalized, incoming_normalized)
            similarity = matcher.ratio()
            token_union = output_tokens | set(_copy_tokens(incoming))
            token_overlap = (
                len(output_tokens & set(_copy_tokens(incoming))) / len(token_union)
                if token_union
                else 0.0
            )
            match = matcher.find_longest_match(
                0,
                len(output_normalized),
                0,
                len(incoming_normalized),
            )
            fragment = output_normalized[match.a : match.a + match.size]
            fragment_ratio = match.size / max(1, len(incoming_normalized))
            fact_reuse = (
                len(output_tokens) <= 7
                and bool(output_tokens)
                and output_tokens <= allowed_tokens
            )
            rule = None
            if output_normalized == incoming_normalized:
                rule = "exact_normalized_copy"
            elif similarity >= 0.9:
                rule = "near_copy"
            elif (
                match.size >= 18
                and fragment_ratio >= 0.55
                and token_overlap >= 0.45
            ):
                rule = "partial_incoming_copy"
            if rule and not fact_reuse:
                findings.append(
                    {
                        "rule_id": rule,
                        "similarity": round(similarity, 6),
                        "token_overlap": round(token_overlap, 6),
                        "matched_fragment": fragment,
                    }
                )
    unique = {
        (
            item["rule_id"],
            item["matched_fragment"],
        ): item
        for item in findings
    }
    return tuple(unique.values())


def _normalize_copy(value: str) -> str:
    return " ".join(_copy_tokens(value))


def _copy_tokens(value: str) -> list[str]:
    return re.findall(r"[0-9a-zа-яё]+", value.casefold())


def _normalize_question(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip().rstrip("?!.,")
