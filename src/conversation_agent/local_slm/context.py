"""Compact local context builder."""

from __future__ import annotations

from collections.abc import Sequence

from conversation_agent.local_slm.models import DialogueDecision, LocalContext


class LocalContextBuilder:
    def __init__(self, *, budget_chars: int = 2400) -> None:
        self.budget_chars = budget_chars

    def build(
        self,
        *,
        agent_id: str,
        decision: DialogueDecision,
        messages: Sequence[dict[str, str]],
        relationship: str = "unknown",
        facts: Sequence[str] = (),
        corrections: Sequence[str] = (),
        adapter_id: str | None = None,
    ) -> LocalContext:
        compact_messages = _tail_by_budget(messages, budget_chars=self.budget_chars // 2)
        return LocalContext(
            agent_id=agent_id,
            relationship=relationship,
            mode=decision.interaction_mode,
            emotion=decision.emotion,
            goal=decision.intent,
            facts=tuple(_trim_list(facts, 6)),
            conversation=tuple(compact_messages),
            corrections=tuple(_trim_list(corrections, 4)),
            adapter_id=adapter_id,
        )


def _tail_by_budget(
    messages: Sequence[dict[str, str]],
    *,
    budget_chars: int,
) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    used = 0
    for item in reversed(messages):
        content = str(item.get("content", item.get("text", ""))).strip()
        role = str(item.get("role", "user")).strip() or "user"
        if not content:
            continue
        cost = len(content) + len(role) + 4
        if selected and used + cost > budget_chars:
            break
        selected.append({"role": role, "content": content[:800]})
        used += cost
    return list(reversed(selected))


def _trim_list(values: Sequence[str], limit: int) -> list[str]:
    return [str(item).strip()[:300] for item in values if str(item).strip()][:limit]

