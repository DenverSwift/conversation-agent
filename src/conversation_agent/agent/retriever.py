"""Provider-independent retrieval of real human style evidence."""

from __future__ import annotations

from conversation_agent.storage.repository import FeedbackRepository
from conversation_agent.style.compiler import examples_from_feedback
from conversation_agent.style.models import SelectedEvidence, StyleBundle
from conversation_agent.style.retrieval import retrieve_examples


class ExampleRetriever:
    def __init__(
        self,
        *,
        bundle: StyleBundle | None,
        repository: FeedbackRepository | None,
        limit: int,
    ) -> None:
        self.bundle = bundle
        self.repository = repository
        self.limit = limit

    def retrieve(self, query: str, *, contact_id: int) -> list[SelectedEvidence]:
        examples = list(self.bundle.examples) if self.bundle is not None else []
        if self.repository is not None:
            immediate, _ = examples_from_feedback(
                self.repository.reviewed_replies(),
                contact_id=contact_id,
            )
            examples = [*immediate, *examples]
        return retrieve_examples(
            query,
            examples,
            contact_id=contact_id,
            limit=self.limit,
        )
