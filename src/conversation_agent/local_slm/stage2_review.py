"""Deterministic blind A/B review workflow for Stage 2."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from conversation_agent.local_slm.stage2_dataset import atomic_write_json
from conversation_agent.local_slm.stage2_runner import load_run_results

RATING_DIMENSIONS = (
    "naturalness",
    "relevance",
    "brevity",
    "telegram_likeness",
    "relationship_fit",
    "emotional_appropriateness",
    "factual_discipline",
    "personality_fit",
)
WINNERS = ("A", "B", "tie_good", "tie_bad")


@dataclass(frozen=True)
class BlindPair:
    pair_id: str
    scenario_id: str
    repetition: int
    candidate_a_provider: str
    candidate_b_provider: str
    payload: dict[str, Any]


def build_blind_pairs(
    run_dir: Path,
    *,
    seed: int,
    category: str | None = None,
) -> list[BlindPair]:
    grouped: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for result in load_run_results(run_dir):
        if result.get("provider_error"):
            continue
        if category and category not in {
            result.get("category"),
            *result.get("tags", []),
        }:
            continue
        key = (str(result["scenario_id"]), int(result.get("repetition", 1)))
        grouped.setdefault(key, {})[str(result["provider"])] = result
    pairs: list[BlindPair] = []
    for (scenario_id, repetition), candidates in sorted(grouped.items()):
        if set(candidates) != {"local_qwen", "openai_gpt4o_mini"}:
            continue
        providers = deterministic_ab_order(
            scenario_id=scenario_id,
            repetition=repetition,
            seed=seed,
        )
        candidate_a = candidates[providers[0]]
        candidate_b = candidates[providers[1]]
        snapshot = dict(candidate_a.get("scenario", {}))
        payload = {
            "pair_id": f"{scenario_id}__r{repetition}",
            "scenario_id": scenario_id,
            "category": snapshot.get("category", candidate_a.get("category")),
            "relationship": snapshot.get("relationship", {}),
            "conversation": snapshot.get("conversation", []),
            "known_facts": snapshot.get("known_facts", []),
            "restrictions": snapshot.get("forbidden_claims", []),
            "candidate_A": _candidate_text(candidate_a),
            "candidate_B": _candidate_text(candidate_b),
        }
        pairs.append(
            BlindPair(
                pair_id=payload["pair_id"],
                scenario_id=scenario_id,
                repetition=repetition,
                candidate_a_provider=providers[0],
                candidate_b_provider=providers[1],
                payload=payload,
            )
        )
    return pairs


def deterministic_ab_order(
    *,
    scenario_id: str,
    repetition: int,
    seed: int,
) -> tuple[str, str]:
    digest = hashlib.sha256(f"{seed}:{scenario_id}:{repetition}".encode()).digest()
    if digest[0] % 2:
        return ("local_qwen", "openai_gpt4o_mini")
    return ("openai_gpt4o_mini", "local_qwen")


def save_human_review(
    *,
    run_dir: Path,
    reviewer: str,
    pair: BlindPair,
    ratings: dict[str, Any],
) -> Path:
    _validate_ratings(ratings)
    path = run_dir / "reviews" / reviewer / f"{pair.pair_id}.json"
    value = {
        "pair_id": pair.pair_id,
        "scenario_id": pair.scenario_id,
        "repetition": pair.repetition,
        "reviewer": reviewer,
        "blind": True,
        "winner": ratings["winner"],
        "candidate_A": ratings["candidate_A"],
        "candidate_B": ratings["candidate_B"],
        "note": str(ratings.get("note", "")),
    }
    atomic_write_json(path, value)
    return path


def reveal_mapping(
    run_dir: Path,
    *,
    seed: int,
    category: str | None = None,
) -> dict[str, dict[str, str]]:
    return {
        pair.pair_id: {
            "A": pair.candidate_a_provider,
            "B": pair.candidate_b_provider,
        }
        for pair in build_blind_pairs(run_dir, seed=seed, category=category)
    }


def run_interactive_review(
    *,
    run_dir: Path,
    reviewer: str,
    seed: int,
    category: str | None,
    only_unreviewed: bool,
    reveal: bool,
) -> dict[str, Any]:
    if reveal:
        mapping = reveal_mapping(run_dir, seed=seed, category=category)
        print(json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True))
        return {"revealed_pairs": len(mapping), "reviews_changed": False}
    pairs = build_blind_pairs(run_dir, seed=seed, category=category)
    review_dir = run_dir / "reviews" / reviewer
    completed = {
        path.stem for path in review_dir.glob("*.json")
    } if review_dir.is_dir() else set()
    if only_unreviewed:
        pairs = [pair for pair in pairs if pair.pair_id not in completed]
    index = 0
    saved = 0
    while index < len(pairs):
        pair = pairs[index]
        _print_blind_payload(pair.payload, index=index, total=len(pairs))
        try:
            command = input(
                "Enter 'rate', 'skip', 'back', 'progress', or 'quit': "
            )
        except EOFError:
            break
        command = command.replace("\x00", "").lstrip("\ufeff").strip().lower()
        if command == "quit":
            break
        if command == "skip":
            index += 1
            continue
        if command == "back":
            index = max(0, index - 1)
            continue
        if command == "progress":
            print(f"Reviewed on disk: {len(completed) + saved}; remaining: {len(pairs) - index}")
            continue
        if command != "rate":
            print("Unknown command.")
            continue
        ratings = _collect_ratings()
        save_human_review(
            run_dir=run_dir,
            reviewer=reviewer,
            pair=pair,
            ratings=ratings,
        )
        saved += 1
        index += 1
    return {
        "available_pairs": len(pairs),
        "saved_this_session": saved,
        "remaining": max(0, len(pairs) - index),
        "provider_mapping_revealed": False,
    }


def _candidate_text(result: dict[str, Any]) -> dict[str, Any]:
    normalized = result.get("normalized_output") or {}
    return {
        "action": normalized.get("action"),
        "messages": normalized.get("messages", []),
        "reaction": normalized.get("reaction"),
        "handoff_required": normalized.get("handoff_required"),
    }


def _validate_ratings(ratings: dict[str, Any]) -> None:
    if ratings.get("winner") not in WINNERS:
        raise ValueError(f"winner must be one of {WINNERS}")
    for candidate in ("candidate_A", "candidate_B"):
        value = ratings.get(candidate)
        if not isinstance(value, dict):
            raise TypeError(f"{candidate} ratings are required")
        for dimension in RATING_DIMENSIONS:
            rating = value.get(dimension)
            if not isinstance(rating, int) or not 1 <= rating <= 5:
                raise ValueError(f"{candidate}.{dimension} must be 1-5")
        if value.get("correct_action") not in {"yes", "no"}:
            raise ValueError(f"{candidate}.correct_action must be yes/no")
        if value.get("hallucination") not in {"yes", "no", "unsure"}:
            raise ValueError(f"{candidate}.hallucination must be yes/no/unsure")
        if value.get("bot_like") not in {"yes", "no"}:
            raise ValueError(f"{candidate}.bot_like must be yes/no")
        if value.get("needs_human_edit") not in {"no", "minor", "major"}:
            raise ValueError(
                f"{candidate}.needs_human_edit must be no/minor/major"
            )


def _collect_ratings() -> dict[str, Any]:
    winner = _ask_choice("Winner", WINNERS)
    result: dict[str, Any] = {"winner": winner}
    for candidate in ("candidate_A", "candidate_B"):
        print(f"Ratings for {candidate}:")
        values: dict[str, Any] = {}
        for dimension in RATING_DIMENSIONS:
            values[dimension] = int(_ask_choice(dimension, ("1", "2", "3", "4", "5")))
        values["correct_action"] = _ask_choice("correct action", ("yes", "no"))
        values["hallucination"] = _ask_choice(
            "hallucination",
            ("yes", "no", "unsure"),
        )
        values["bot_like"] = _ask_choice("bot-like", ("yes", "no"))
        values["needs_human_edit"] = _ask_choice(
            "needs human edit",
            ("no", "minor", "major"),
        )
        result[candidate] = values
    result["note"] = input("Free-text note (optional): ").strip()
    return result


def _ask_choice(label: str, choices: tuple[str, ...]) -> str:
    while True:
        value = input(f"{label} [{'/'.join(choices)}]: ").strip()
        if value in choices:
            return value
        print("Invalid choice.")


def _print_blind_payload(payload: dict[str, Any], *, index: int, total: int) -> None:
    print(f"\nReview {index + 1}/{total}")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
