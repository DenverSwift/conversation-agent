"""End-to-end conversation intelligence, response, and behavior planning."""

from __future__ import annotations

from conversation_agent.agent.analyzer import InteractionAnalyzer
from conversation_agent.agent.context_builder import ChatMessage
from conversation_agent.agent.goal_planner import GoalPlanner
from conversation_agent.agent.prompt_composer import PromptComposer
from conversation_agent.agent.response_generator import ResponseGenerator
from conversation_agent.agent.retriever import ExampleRetriever
from conversation_agent.domain.models import (
    BusinessProfile,
    ConversationState,
    IdentityProfile,
    IncomingMessageGroup,
    PipelineResult,
    RelationshipProfile,
    StyleProfile,
)
from conversation_agent.telegram.behavior import TelegramBehaviorPlanner


class ConversationPipeline:
    def __init__(
        self,
        *,
        analyzer: InteractionAnalyzer,
        goal_planner: GoalPlanner,
        retriever: ExampleRetriever,
        prompt_composer: PromptComposer,
        response_generator: ResponseGenerator,
        behavior_planner: TelegramBehaviorPlanner,
        compiled_style_rules: str = "",
    ) -> None:
        self.analyzer = analyzer
        self.goal_planner = goal_planner
        self.retriever = retriever
        self.prompt_composer = prompt_composer
        self.response_generator = response_generator
        self.behavior_planner = behavior_planner
        self.compiled_style_rules = compiled_style_rules

    async def process(
        self,
        *,
        group: IncomingMessageGroup,
        recent_messages: list[ChatMessage],
        identity: IdentityProfile,
        business: BusinessProfile,
        style: StyleProfile,
        relationship: RelationshipProfile,
        state: ConversationState,
    ) -> PipelineResult:
        analysis = await self.analyzer.analyze(
            group=group,
            recent_messages=recent_messages,
            state=state,
            business=business,
        )
        goal = self.goal_planner.plan(analysis, state, business)
        try:
            numeric_contact_id = int(group.contact_id)
        except ValueError:
            numeric_contact_id = 0
        examples = self.retriever.retrieve(group.text, contact_id=numeric_contact_id)
        prompt = self.prompt_composer.compose(
            identity=identity,
            business=business,
            style=style,
            relationship=relationship,
            state=state,
            analysis=analysis,
            goal=goal,
            recent_messages=recent_messages,
            examples=examples,
            compiled_style_rules=self.compiled_style_rules,
        )
        response = await self.response_generator.generate(prompt, goal)
        behavior = self.behavior_planner.plan(response, urgency=analysis.urgency)
        return PipelineResult(
            analysis=analysis,
            goal=goal,
            response=response,
            behavior=behavior,
            prompt=prompt,
        )
