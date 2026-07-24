"""Build private AA.1 style artifacts from local reviewed source data."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from conversation_agent.settings import Settings
from conversation_agent.storage.sqlite_repository import SQLiteFeedbackRepository
from conversation_agent.style.compiler import build_style_bundle
from conversation_agent.style.openai_analyzer import OpenAIStyleAnalyzer


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile all qualifying local examples into a private AA.1 style bundle."
    )
    parser.add_argument("--batch-size", type=int, default=25)
    args = parser.parse_args()
    try:
        summary = asyncio.run(run_build(batch_size=args.batch_size))
    except Exception as exc:  # noqa: BLE001
        print(f"Style bundle build failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


async def run_build(*, batch_size: int) -> dict[str, object]:
    settings = Settings.load()
    print("[1/2] Загрузка исходного экспорта и базы отзывов...", file=sys.stderr)
    repository = None
    if settings.feedback_database_path.is_file():
        repository = SQLiteFeedbackRepository(settings.feedback_database_path)
        repository.initialize()
    analyzer = OpenAIStyleAnalyzer(
        api_key=settings.openai_api_key,
        model=settings.style_analysis_model,
        timeout_seconds=settings.openai_timeout_seconds,
    )
    print(f"[2/2] Компиляция профиля стиля и банка примеров в {settings.style_bundle_directory}...", file=sys.stderr)
    summary = await build_style_bundle(
        source_path=settings.style_source_examples_path,
        output_directory=settings.style_bundle_directory,
        contact_id=settings.allowed_telegram_user_id,
        source_limit=settings.training_export_limit,
        analyzer=analyzer,
        analysis_model=settings.style_analysis_model,
        feedback_records=repository.reviewed_replies() if repository else (),
        batch_size=batch_size,
    )
    print(
        f"Успешно скомпилирован бандл стиля! Примеров в банке: {summary.get('example_count', 0)}, "
        f"сохранено в {settings.style_bundle_directory}",
        file=sys.stderr,
    )
    return summary


if __name__ == "__main__":
    raise SystemExit(main())
