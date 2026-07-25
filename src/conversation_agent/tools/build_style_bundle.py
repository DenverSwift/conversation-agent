"""Build or inspect the private AA.2 incremental style bundle."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from conversation_agent.settings import Settings
from conversation_agent.storage.sqlite_repository import SQLiteFeedbackRepository
from conversation_agent.style.compiler import (
    analysis_fingerprint,
    build_style_bundle,
    plan_style_build,
    scan_source_examples,
)
from conversation_agent.style.compiler_state import load_compiler_state
from conversation_agent.style.openai_analyzer import OpenAIStyleAnalyzer


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Incrementally compile private Matvey style evidence. "
            "Unchanged evidence is reused without OpenAI calls."
        )
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--dry-run",
        action="store_true",
        help="Show aggregate planned actions without building or exposing private text.",
    )
    modes.add_argument(
        "--full-rebuild",
        action="store_true",
        help="Explicitly reanalyze all unique sources and replace compatible cached state.",
    )
    modes.add_argument(
        "--status",
        action="store_true",
        help="Show safe local compiler metadata and pending aggregate changes.",
    )
    modes.add_argument(
        "--force-resynthesize",
        action="store_true",
        help="Regenerate artifacts from compatible cached observations only.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        help="Override STYLE_ANALYSIS_BATCH_SIZE for this build.",
    )
    args = parser.parse_args()
    try:
        summary = asyncio.run(run_build(args))
    except Exception as exc:  # noqa: BLE001
        print(f"Style bundle build failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


async def run_build(args: argparse.Namespace) -> dict[str, Any]:
    settings = Settings.load()
    batch_size = args.batch_size or settings.style_analysis_batch_size
    feedback_records = _feedback_records(settings)
    if args.status:
        return _status(settings, feedback_records, batch_size)
    print("[1/2] Загрузка исходных примеров и базы отзывов...", file=sys.stderr)
    if args.full_rebuild:
        print(
            "FULL REBUILD: all unique style sources will be reanalyzed. "
            "This may use significantly more time and OpenAI API tokens.",
            file=sys.stderr,
        )

    analyzer = None
    if not args.dry_run and not args.force_resynthesize:
        plan = plan_style_build(
            source_path=settings.style_source_examples_path,
            contact_id=settings.allowed_telegram_user_id,
            source_limit=settings.training_export_limit,
            feedback_records=feedback_records,
            analysis_model=settings.style_analysis_model,
            state_path=settings.style_compiler_state_path,
            batch_size=batch_size,
            full_rebuild=args.full_rebuild,
        )
        if plan.hashes_to_analyze:
            analyzer = OpenAIStyleAnalyzer(
                api_key=settings.openai_api_key,
                model=settings.style_analysis_model,
                timeout_seconds=settings.openai_timeout_seconds,
            )
    return await build_style_bundle(
        source_path=settings.style_source_examples_path,
        output_directory=settings.style_bundle_directory,
        contact_id=settings.allowed_telegram_user_id,
        source_limit=settings.training_export_limit,
        analyzer=analyzer,
        analysis_model=settings.style_analysis_model,
        feedback_records=feedback_records,
        batch_size=batch_size,
        state_path=settings.style_compiler_state_path,
        incremental=settings.style_incremental_compilation,
        full_rebuild=args.full_rebuild,
        dry_run=args.dry_run,
        force_resynthesize=args.force_resynthesize,
        verbose=True,
    )


def _status(
    settings: Settings,
    feedback_records: Any,
    batch_size: int,
) -> dict[str, Any]:
    state = load_compiler_state(settings.style_compiler_state_path)
    examples, invalid = scan_source_examples(
        settings.style_source_examples_path,
        contact_id=settings.allowed_telegram_user_id,
        limit=settings.training_export_limit,
        feedback_records=feedback_records,
    )
    fingerprint = analysis_fingerprint(
        analysis_model=settings.style_analysis_model,
        batch_size=batch_size,
    )
    metadata = state.metadata if state is not None else {}
    compatible = metadata.get("analysis_fingerprint") in {None, fingerprint}
    if compatible:
        plan = plan_style_build(
            source_path=settings.style_source_examples_path,
            contact_id=settings.allowed_telegram_user_id,
            source_limit=settings.training_export_limit,
            feedback_records=feedback_records,
            analysis_model=settings.style_analysis_model,
            state_path=settings.style_compiler_state_path,
            batch_size=batch_size,
        )
        pending = {
            "new": len(plan.new),
            "modified": len(plan.modified),
            "deleted": len(plan.deleted),
        }
    else:
        pending = {"full_rebuild_required": True}
    return {
        "last_successful_build": metadata.get("last_successful_build"),
        "source_count": len(examples),
        "cached_analysis_count": len(state.content_cache) if state else 0,
        "analysis_fingerprint": fingerprint,
        "stored_analysis_fingerprint": metadata.get("analysis_fingerprint"),
        "fingerprint_compatible": compatible,
        "bundle_version": metadata.get("bundle_version"),
        "pending_source_changes": pending,
        "invalid_sources": invalid,
        "last_build_mode": metadata.get("last_build_mode"),
    }


def _feedback_records(settings: Settings) -> list[Any]:
    if not settings.feedback_database_path.is_file():
        return []
    repository = SQLiteFeedbackRepository(settings.feedback_database_path)
    repository.initialize()
    return repository.reviewed_replies()


if __name__ == "__main__":
    raise SystemExit(main())
