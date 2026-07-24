"""Inspect AA.2 runtime metadata without exposing private content by default."""

from __future__ import annotations

import argparse
import json
import sys

from conversation_agent.agent.context_builder import ChatMessage
from conversation_agent.settings import Settings
from conversation_agent.storage.sqlite_repository import SQLiteFeedbackRepository
from conversation_agent.style.bundle import load_style_bundle
from conversation_agent.style.runtime import StyleRuntime


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect safe AA.2 style runtime metadata."
    )
    parser.add_argument(
        "--show-private-content",
        action="store_true",
        help="Print the fully composed private prompt to this terminal.",
    )
    args = parser.parse_args()
    try:
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
        runtime = StyleRuntime(
            bundle=bundle,
            bundle_directory=settings.style_bundle_directory,
            repository=repository,
            contact_id=settings.allowed_telegram_user_id,
            retrieval_limit=settings.style_retrieval_limit,
            rules_max_chars=settings.style_rules_max_chars,
            examples_max_chars=settings.style_examples_max_chars,
        )
        composed = runtime.compose(
            [ChatMessage(role="user", content="local inspection", provenance="contact")]
        )
        metadata = {
            "style_adaptation_enabled": settings.style_adaptation_enabled,
            "bundle_built_at": bundle.built_at,
            "source_example_count": bundle.source_example_count,
            "behavior_rule_count": len(bundle.rules),
            "contact_profile_loaded": (
                settings.allowed_telegram_user_id in bundle.contact_profiles
            ),
            "candidate_example_count": composed.candidate_count,
            "selected_example_count": composed.selected_count,
            "selected_fix_count": composed.selected_fix_count,
            "provenance_counts": composed.provenance_counts,
            "estimated_prompt_chars": composed.estimated_chars,
            "prompt_version": settings.prompt_version,
        }
        print(json.dumps(metadata, sort_keys=True))
        if args.show_private_content:
            print(
                "WARNING: PRIVATE STYLE CONTENT FOLLOWS. "
                "Do not copy, log, or share this output.",
                file=sys.stderr,
            )
            print(composed.instructions)
            print(json.dumps(composed.messages, ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001
        print(f"Style inspection failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
