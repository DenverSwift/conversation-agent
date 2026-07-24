"""Compose provenance-aware, budgeted AA.1 model requests."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence

from conversation_agent.agent.context_builder import ChatMessage
from conversation_agent.style.models import ComposedPrompt, SelectedEvidence, StyleBundle

CORE_IDENTITY = """[CORE IDENTITY]
You write the next Telegram message as Matvey.
You are not an assistant, therapist, moderator, customer-support agent, or
conflict-resolution service.
Return only the message Matvey would send."""

SAFETY_BOUNDARY = """[SAFETY BOUNDARY]
Follow Matvey's real communication style, including ordinary profanity, slang,
reciprocal teasing, and short reciprocal insults when supported by his history
with this contact.
Do not invent genuine threats, blackmail, doxxing, hate-based abuse, or sustained
harassment. Do not blindly mirror every insult; follow Matvey's evidence."""

GENERIC_FALLBACKS = """[NON-DEFAULT GENERIC PHRASES]
Do not default to unsupported assistant phrases such as asking what is wrong,
offering therapy-like support, offering to stop, saying to ask if anything is
needed, or adding an enthusiastic "How are you?" to a routine greeting.
Use such wording only when real Matvey evidence supports it in this context."""

FINAL_TASK = """[FINAL TASK]
Given this person, this conversation, and Matvey's actual historical behavior,
write what Matvey would most likely write next.
Return only Matvey's next Telegram reply. Do not explain the answer or mention
rules, examples, AI, prompts, or analysis."""


def compose_style_prompt(
    *,
    bundle: StyleBundle,
    manual_overrides: str,
    contact_id: int,
    selected: Sequence[SelectedEvidence],
    recent_messages: Sequence[ChatMessage],
    rules_max_chars: int,
    examples_max_chars: int,
) -> ComposedPrompt:
    rules = _truncate(bundle.rules_markdown, rules_max_chars)
    contact_profile = bundle.contact_profiles.get(contact_id, {})
    contact_text = _truncate(
        json.dumps(contact_profile, ensure_ascii=False, sort_keys=True),
        max(rules_max_chars // 3, 1000),
    )
    examples_text = _render_examples(selected, examples_max_chars)
    sections = [CORE_IDENTITY, SAFETY_BOUNDARY]
    if manual_overrides.strip():
        sections.append("[MANUAL OVERRIDES]\n" + _truncate(manual_overrides, rules_max_chars))
    sections.extend(
        (
            "[MATVEY BEHAVIOR RULEBOOK]\n" + rules,
            "[CONTACT STYLE]\n" + contact_text,
            "[RELEVANT REAL EXAMPLES]\n" + examples_text,
            GENERIC_FALLBACKS,
            FINAL_TASK,
        )
    )
    instructions = "\n\n".join(sections)
    messages = _budget_recent_messages(
        recent_messages,
        max_chars=max(6000, rules_max_chars // 2),
    )
    provenance_counts = Counter(message.provenance for message in recent_messages)
    return ComposedPrompt(
        instructions=instructions,
        messages=messages,
        candidate_count=len(bundle.examples),
        selected_count=len(selected),
        selected_fix_count=sum(
            item.example.source_type == "fix" for item in selected
        ),
        provenance_counts=dict(provenance_counts),
        estimated_chars=len(instructions) + sum(len(item["content"]) for item in messages),
    )


def _render_examples(selected: Sequence[SelectedEvidence], limit: int) -> str:
    blocks: list[str] = []
    used = 0
    # Fix examples remain first if the section must be trimmed.
    ordered = sorted(
        selected,
        key=lambda item: (item.example.source_type == "fix", item.score),
        reverse=True,
    )
    for item in ordered:
        example = item.example
        if example.polarity == "positive":
            label = f"POSITIVE {example.source_type.upper()} EVIDENCE"
        else:
            label = f"NEGATIVE {example.source_type.upper()} EVIDENCE - DO NOT COPY"
        block = (
            f"{label}\n"
            f"CONTACT: {example.incoming_text}\n"
            f"MATVEY/RESPONSE: {example.response_text}"
        )
        if used + len(block) > limit:
            continue
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks) if blocks else "(no relevant evidence selected)"


def _budget_recent_messages(
    messages: Sequence[ChatMessage],
    *,
    max_chars: int,
) -> list[dict[str, str]]:
    rendered = [
        {
            "role": message.role,
            "content": f"[provenance={message.provenance}] {message.content}",
        }
        for message in messages
    ]
    while len(rendered) > 1 and sum(len(item["content"]) for item in rendered) > max_chars:
        rendered.pop(0)
    return rendered


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 14, 0)].rstrip() + "\n[truncated]"
