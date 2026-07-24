"""Live AA.2 style adaptation orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from conversation_agent.agent.context_builder import ChatMessage
from conversation_agent.storage.repository import FeedbackRepository
from conversation_agent.style.bundle import load_manual_overrides
from conversation_agent.style.compiler import examples_from_feedback
from conversation_agent.style.composer import compose_style_prompt
from conversation_agent.style.models import ComposedPrompt, StyleBundle
from conversation_agent.style.retrieval import retrieve_examples


class StyleRuntime:
    def __init__(
        self,
        *,
        bundle: StyleBundle,
        bundle_directory: Path,
        repository: FeedbackRepository | None,
        contact_id: int,
        retrieval_limit: int,
        rules_max_chars: int,
        examples_max_chars: int,
    ) -> None:
        self.bundle = bundle
        self.bundle_directory = bundle_directory
        self.repository = repository
        self.contact_id = contact_id
        self.retrieval_limit = retrieval_limit
        self.rules_max_chars = rules_max_chars
        self.examples_max_chars = examples_max_chars

    def compose(self, messages: Sequence[ChatMessage]) -> ComposedPrompt:
        query = messages[-1].content if messages else ""
        immediate_feedback = []
        if self.repository is not None:
            immediate_feedback, _ = examples_from_feedback(
                self.repository.reviewed_replies(),
                contact_id=self.contact_id,
            )
        candidates = [*immediate_feedback, *self.bundle.examples]
        selected = retrieve_examples(
            query,
            candidates,
            contact_id=self.contact_id,
            limit=self.retrieval_limit,
        )
        prompt = compose_style_prompt(
            bundle=self.bundle,
            manual_overrides=load_manual_overrides(self.bundle_directory),
            contact_id=self.contact_id,
            selected=selected,
            recent_messages=messages,
            rules_max_chars=self.rules_max_chars,
            examples_max_chars=self.examples_max_chars,
        )
        return ComposedPrompt(
            instructions=prompt.instructions,
            messages=prompt.messages,
            candidate_count=len(candidates),
            selected_count=prompt.selected_count,
            selected_fix_count=prompt.selected_fix_count,
            provenance_counts=prompt.provenance_counts,
            estimated_chars=prompt.estimated_chars,
        )
