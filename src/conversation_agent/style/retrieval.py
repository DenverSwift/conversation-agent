"""Deterministic local retrieval for style evidence."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime

from conversation_agent.style.models import SelectedEvidence, StyleExample

TOKEN_RE = re.compile(r"[\w']+", re.UNICODE)
PROFANITY_MARKERS = (
    "fuck",
    "shit",
    "нахуй",
    "хуй",
    "бля",
    "пизд",
    "сука",
)


def retrieve_examples(
    query: str,
    examples: Sequence[StyleExample],
    *,
    contact_id: int,
    limit: int,
) -> list[SelectedEvidence]:
    if limit <= 0:
        return []
    deduplicated = _deduplicate(
        [example for example in examples if example.polarity in {"positive", "negative"}]
    )
    document_tokens = [_tokens(example.incoming_text) for example in deduplicated]
    document_frequency = Counter(
        token for tokens in document_tokens for token in set(tokens)
    )
    query_tokens = _tokens(query)
    scored: list[SelectedEvidence] = []
    for example, tokens in zip(deduplicated, document_tokens, strict=True):
        score = _tfidf_similarity(
            query_tokens,
            tokens,
            document_frequency,
            len(deduplicated),
        )
        if example.contact_id == contact_id:
            score += 1.5
        if example.source_type == "fix":
            score += 5.0
        elif example.source_type == "human_matvey":
            score += 1.0
        if example.polarity == "negative":
            score += 0.25
        if _has_profanity(query) == _has_profanity(example.incoming_text):
            score += 0.5
        if _intent(query) == _intent(example.incoming_text):
            score += 0.75
        score += _recency_bonus(example.created_at)
        scored.append(SelectedEvidence(example=example, score=score))
    scored.sort(
        key=lambda item: (
            item.score,
            item.example.source_type == "fix",
            item.example.created_at,
            item.example.example_id,
        ),
        reverse=True,
    )
    return scored[:limit]


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text)]


def _tfidf_similarity(
    query: Sequence[str],
    document: Sequence[str],
    document_frequency: Counter[str],
    document_count: int,
) -> float:
    if not query or not document:
        return 0.0
    query_counts = Counter(query)
    document_counts = Counter(document)
    vocabulary = set(query_counts) | set(document_counts)
    query_vector: dict[str, float] = {}
    document_vector: dict[str, float] = {}
    for token in vocabulary:
        idf = math.log((document_count + 1) / (document_frequency[token] + 1)) + 1
        query_vector[token] = query_counts[token] * idf
        document_vector[token] = document_counts[token] * idf
    dot = sum(query_vector[token] * document_vector[token] for token in vocabulary)
    query_norm = math.sqrt(sum(value * value for value in query_vector.values()))
    document_norm = math.sqrt(sum(value * value for value in document_vector.values()))
    return dot / (query_norm * document_norm) if query_norm and document_norm else 0.0


def _has_profanity(text: str) -> bool:
    normalized = text.lower()
    return any(marker in normalized for marker in PROFANITY_MARKERS)


def _intent(text: str) -> str:
    normalized = text.lower().strip()
    tokens = set(_tokens(normalized))
    if tokens & {"привет", "дарова", "здарова", "hello", "hi"}:
        return "greeting"
    if tokens & {"когда", "сегодня", "завтра", "встреча", "meeting", "schedule"}:
        return "scheduling"
    if tokens & {"ок", "ага", "понял", "да", "нет", "okay", "yes", "no"}:
        return "acknowledgement"
    if _has_profanity(normalized):
        return "aggressive_or_teasing"
    if "?" in normalized:
        return "question"
    if tokens & {"договор", "документ", "клиент", "оплата", "invoice", "contract"}:
        return "business"
    return "general"


def _recency_bonus(value: str) -> float:
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    age_days = max((datetime.now(UTC) - parsed).total_seconds() / 86400, 0)
    return 0.25 / (1 + age_days / 30)


def _deduplicate(examples: Sequence[StyleExample]) -> list[StyleExample]:
    seen: set[tuple[str, str, str]] = set()
    result: list[StyleExample] = []
    for example in examples:
        key = (
            " ".join(example.incoming_text.lower().split()),
            " ".join(example.response_text.lower().split()),
            example.polarity,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(example)
    return result
