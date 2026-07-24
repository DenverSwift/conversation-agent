"""Export provider-independent, explicitly reviewed feedback records."""

from __future__ import annotations

import argparse
import json
import sys

from conversation_agent.settings import Settings
from conversation_agent.storage.sqlite_repository import SQLiteFeedbackRepository
from conversation_agent.training.feedback_export import write_feedback_exports


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export provider-independent feedback JSONL for retrieval, evaluation, "
            "and prompt development."
        )
    )
    parser.parse_args()
    try:
        settings = Settings.load()
        repository = SQLiteFeedbackRepository(settings.feedback_database_path)
        repository.initialize()
        summary = write_feedback_exports(
            output_directory=settings.training_export_directory,
            records=repository.reviewed_replies(),
            redact_pii=settings.training_export_redact_pii,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Feedback export failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
