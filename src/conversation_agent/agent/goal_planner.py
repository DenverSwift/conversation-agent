"""Choose the next business action without drafting response text."""

from __future__ import annotations

from conversation_agent.domain.models import (
    BusinessProfile,
    ConversationState,
    GoalPlan,
    InteractionAnalysis,
)


class GoalPlanner:
    def __init__(self, *, handoff_threshold: float) -> None:
        self.handoff_threshold = handoff_threshold

    def plan(
        self,
        analysis: InteractionAnalysis,
        state: ConversationState,
        business: BusinessProfile,
    ) -> GoalPlan:
        del business
        if analysis.needs_human_handoff or analysis.confidence < self.handoff_threshold:
            return GoalPlan(
                goal="handoff_to_human",
                reason="analysis requested human review or confidence is below threshold",
                handoff_required=True,
            )
        if not analysis.should_reply or analysis.conversation_stage == "not_relevant":
            return GoalPlan(goal="do_not_reply", reason="no useful reply is required")
        missing = set(analysis.missing_information)
        if "client_task" in missing or "project_type" in missing:
            return GoalPlan(
                goal="ask_clarifying_question",
                reason="the client's task is not clear yet",
            )
        if "timeline" in missing:
            return GoalPlan(goal="qualify_timeline", reason="timeline is still unknown")
        if "budget" in missing and state.conversation_stage in {
            "qualification",
            "solution_matching",
        }:
            return GoalPlan(goal="qualify_budget", reason="budget is relevant at this stage")
        return GoalPlan(
            goal=analysis.recommended_goal,
            reason="selected from the validated interaction analysis",
        )
