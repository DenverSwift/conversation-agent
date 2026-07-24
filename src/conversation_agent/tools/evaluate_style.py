"""Run local A-E prompt comparisons without sending Telegram messages."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from conversation_agent.agent.context_builder import ChatMessage
from conversation_agent.agent.prompt_builder import build_instructions
from conversation_agent.llm.openai_client import OpenAIReplyClient
from conversation_agent.settings import Settings
from conversation_agent.storage.sqlite_repository import SQLiteFeedbackRepository
from conversation_agent.style.bundle import load_style_bundle
from conversation_agent.style.composer import compose_style_prompt
from conversation_agent.style.evaluation import SCENARIOS, score_response
from conversation_agent.style.models import StyleBundle
from conversation_agent.style.retrieval import retrieve_examples
from conversation_agent.style.runtime import StyleRuntime


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare generic and AA.2 style prompt variants locally."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".runtime/style/evaluation.json"),
        help="Private local result path.",
    )
    args = parser.parse_args()
    try:
        summary = asyncio.run(run_evaluation(args.output))
    except Exception as exc:  # noqa: BLE001
        print(f"Style evaluation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


async def run_evaluation(output_path: Path) -> dict[str, Any]:
    settings = Settings.load()
    bundle = load_style_bundle(
        settings.style_bundle_directory,
        contact_id=settings.allowed_telegram_user_id,
        state_path=settings.style_compiler_state_path,
    )
    repository = None
    if settings.feedback_database_path.is_file():
        repository = SQLiteFeedbackRepository(settings.feedback_database_path)
        repository.initialize()
    client = OpenAIReplyClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        timeout_seconds=settings.openai_timeout_seconds,
    )
    runtime = StyleRuntime(
        bundle=bundle,
        bundle_directory=settings.style_bundle_directory,
        repository=repository,
        contact_id=settings.allowed_telegram_user_id,
        retrieval_limit=settings.style_retrieval_limit,
        rules_max_chars=settings.style_rules_max_chars,
        examples_max_chars=settings.style_examples_max_chars,
    )
    variants: dict[str, list[dict[str, Any]]] = {
        name: [] for name in ("A", "B", "C", "D", "E")
    }
    for scenario in SCENARIOS:
        incoming = str(scenario["incoming"])
        message = ChatMessage(role="user", content=incoming, provenance="contact")
        selected = retrieve_examples(
            incoming,
            bundle.examples,
            contact_id=settings.allowed_telegram_user_id,
            limit=settings.style_retrieval_limit,
        )
        no_contact = replace(bundle, contact_profiles={})
        prompts = {
            "A": (build_instructions(settings.readme_path), [{"role": "user", "content": incoming}]),
            "B": _variant_prompt(no_contact, (), message, settings),
            "C": _variant_prompt(bundle, (), message, settings),
            "D": _variant_prompt(bundle, selected, message, settings),
        }
        full = runtime.compose([message])
        prompts["E"] = (full.instructions, full.messages)
        for name, (instructions, messages) in prompts.items():
            response = await client.create_reply(
                instructions=instructions,
                messages=messages,
            )
            variants[name].append(
                {
                    "scenario": scenario["name"],
                    "response": response,
                    "metrics": score_response(response),
                }
            )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps({"variants": variants}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "scenario_count": len(SCENARIOS),
        "variant_count": len(variants),
        "output_path": str(output_path),
        "note": "Automatic metrics are supporting evidence only.",
    }


def _variant_prompt(
    bundle: StyleBundle,
    selected: Any,
    message: ChatMessage,
    settings: Settings,
) -> tuple[str, list[dict[str, str]]]:
    composed = compose_style_prompt(
        bundle=bundle,
        manual_overrides="",
        contact_id=settings.allowed_telegram_user_id,
        selected=selected,
        recent_messages=[message],
        rules_max_chars=settings.style_rules_max_chars,
        examples_max_chars=settings.style_examples_max_chars,
    )
    return composed.instructions, composed.messages


if __name__ == "__main__":
    raise SystemExit(main())
