"""Conservative deterministic cleaning for provider-independent exports."""

from __future__ import annotations

import re
from dataclasses import replace

from conversation_agent.training.models import (
    CleaningStats,
    ContextTurn,
    TrainingExample,
)

URL_ONLY_RE = re.compile(r"^\s*https?://\S+\s*$", re.IGNORECASE)
BOT_COMMAND_RE = re.compile(r"^\s*/[A-Za-z0-9_]+(?:@\w+)?(?:\s|$)")
EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d ()-]{6,}\d)(?!\w)")
SENSITIVE_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
SECRET_RE = re.compile(
    r"(?i)(?:\b(?:api[_-]?key|token|secret)\b\s*[:=]\s*\S+|"
    r"\bsk-[A-Za-z0-9_-]{16,}\b|"
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b)"
)


def clean_examples(
    examples: list[TrainingExample],
    *,
    redact_pii: bool,
) -> tuple[list[TrainingExample], CleaningStats]:
    stats = CleaningStats()
    cleaned: list[TrainingExample] = []
    seen: set[tuple[tuple[tuple[str, str], ...], str]] = set()

    for example in examples:
        reason = _removal_reason(example)
        if reason is not None:
            stats.removed(reason)
            continue

        duplicate_key = (
            tuple((turn.role, _normalize(turn.text)) for turn in example.context),
            _normalize(example.target_reply),
        )
        if duplicate_key in seen:
            stats.removed("duplicate")
            continue
        seen.add(duplicate_key)

        if redact_pii:
            cleaned.append(_redact_example(example, stats))
        else:
            cleaned.append(example)

    return cleaned, stats


def redact_text(text: str, counts: dict[str, int]) -> str:
    text = _sub_with_count(EMAIL_RE, "<EMAIL>", text, counts, "email")
    text = _sub_sensitive_urls(text, counts)
    text = _sub_with_count(PHONE_RE, "<PHONE>", text, counts, "phone")
    return _sub_with_count(SECRET_RE, "<SECRET>", text, counts, "secret")


def _removal_reason(example: TrainingExample) -> str | None:
    target = example.target_reply.strip()
    if not target:
        return "empty_target"
    if not example.is_human_authored:
        return "not_human_authored"
    if URL_ONLY_RE.fullmatch(target):
        return "link_only_target"
    if BOT_COMMAND_RE.match(target):
        return "bot_command_target"
    if example.target_is_forwarded:
        return "forwarded_target"
    if not any(
        turn.role == "user" and _is_meaningful_text(turn.text)
        for turn in example.context
    ):
        return "no_meaningful_incoming_context"
    return None


def _is_meaningful_text(text: str) -> bool:
    stripped = text.strip()
    return bool(
        stripped
        and not URL_ONLY_RE.fullmatch(stripped)
        and not BOT_COMMAND_RE.match(stripped)
    )


def _redact_example(example: TrainingExample, stats: CleaningStats) -> TrainingExample:
    context = tuple(
        ContextTurn(
            role=turn.role,
            text=redact_text(turn.text, stats.redaction_counts),
        )
        for turn in example.context
    )
    return replace(
        example,
        context=context,
        target_reply=redact_text(example.target_reply, stats.redaction_counts),
    )


def _sub_with_count(
    pattern: re.Pattern[str],
    replacement: str,
    text: str,
    counts: dict[str, int],
    category: str,
) -> str:
    redacted, count = pattern.subn(replacement, text)
    counts[category] += count
    return redacted


def _sub_sensitive_urls(text: str, counts: dict[str, int]) -> str:
    def replace_url(match: re.Match[str]) -> str:
        url = match.group(0)
        if "?" not in url:
            return url
        counts["url"] += 1
        return "<URL>"

    return SENSITIVE_URL_RE.sub(replace_url, text)


def _normalize(text: str) -> str:
    return " ".join(text.split())
