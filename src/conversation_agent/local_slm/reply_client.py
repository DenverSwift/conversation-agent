"""Adapter that lets the existing Telegram responder use the local router."""

from __future__ import annotations

from conversation_agent.local_slm.context import LocalContextBuilder
from conversation_agent.local_slm.models import DialoguePolicyInput, GenerationRequest
from conversation_agent.local_slm.policy import DialoguePolicy, safe_policy_decision
from conversation_agent.local_slm.router import HybridGenerationRouter


class LocalRouterReplyClient:
    def __init__(
        self,
        *,
        policy: DialoguePolicy,
        context_builder: LocalContextBuilder,
        router: HybridGenerationRouter,
        agent_id: str = "informal-manager",
    ) -> None:
        self.policy = policy
        self.context_builder = context_builder
        self.router = router
        self.agent_id = agent_id

    async def create_reply(self, *, instructions: str, messages: list[dict[str, str]]) -> str:
        del instructions
        contact_messages = tuple(
            item.get("content", "")
            for item in messages
            if item.get("role") in {"user", "contact"}
        )
        decision = safe_policy_decision(
            self.policy,
            DialoguePolicyInput(messages=contact_messages, recent_history=tuple(messages)),
        )
        context = self.context_builder.build(
            agent_id=self.agent_id,
            decision=decision,
            messages=messages,
        )
        result = await self.router.generate(GenerationRequest(policy=decision, context=context))
        if result.selected.action != "reply":
            return ""
        return "\n".join(result.selected.messages)

