"""Local supporting metrics for AA.1 style comparisons."""

from __future__ import annotations

from typing import Any

SCENARIOS = (
    {"name": "greeting", "incoming": "Дарова"},
    {"name": "reciprocal_insult", "incoming": "Иди на хуй"},
    {"name": "strong_reciprocal_insult", "incoming": "Я говорю иди на хуй"},
    {"name": "scheduling", "incoming": "Ты сегодня свободен?"},
    {"name": "casual_question", "incoming": "Че делаешь?"},
    {"name": "acknowledgement", "incoming": "Ок понял"},
    {"name": "disagreement", "incoming": "Нет, ты не прав"},
    {"name": "business_request", "incoming": "Скинь документы сегодня"},
    {"name": "vague_emotional", "incoming": "Все достало"},
    {"name": "reciprocal_teasing", "incoming": "Ну ты красавчик конечно"},
)

GENERIC_PHRASES = (
    "что-то не так",
    "если хочешь, можем",
    "если что-то нужно",
    "дай знать",
    "как дела",
)


def score_response(response: str) -> dict[str, Any]:
    normalized = response.lower()
    return {
        "length": len(response),
        "generic_phrase_count": sum(phrase in normalized for phrase in GENERIC_PHRASES),
        "has_profanity": any(
            marker in normalized for marker in ("хуй", "нахуй", "бля", "fuck", "shit")
        ),
        "exclamation_count": response.count("!"),
        "question_count": response.count("?"),
    }
