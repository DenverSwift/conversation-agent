from __future__ import annotations

import json

from conversation_agent.storage.models import GeneratedReplyRecord
from conversation_agent.training.feedback_export import write_feedback_exports


def record(
    reply_id: int,
    *,
    status: str,
    corrected: str | None = None,
) -> GeneratedReplyRecord:
    return GeneratedReplyRecord(
        id=reply_id,
        dialog_id=1751105897,
        incoming_message_id=reply_id * 10,
        sent_message_id=reply_id * 100,
        created_at="2026-07-24T10:00:00+00:00",
        sent_at="2026-07-24T10:00:01+00:00",
        model="model",
        prompt_version="AAA.2",
        generated_reply_text="original AI reply",
        context_json='[{"role":"user","text":"question"}]',
        delivery_status="sent",
        feedback_status=status,
        feedback_category="wrong_tone" if status == "rejected" else None,
        feedback_comment=None,
        corrected_reply_text=corrected,
        feedback_updated_at="2026-07-24T10:01:00+00:00",
    )


def read_jsonl(path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_corrected_reply_becomes_preferred_target(tmp_path) -> None:
    write_feedback_exports(
        output_directory=tmp_path,
        records=[record(1, status="corrected", corrected="human correction")],
        redact_pii=False,
    )

    positive = read_jsonl(tmp_path / "feedback_positive.jsonl")

    assert positive[0]["target_reply"] == "human correction"
    assert positive[0]["original_generated_reply"] == "original AI reply"


def test_rejected_uncorrected_reply_is_not_positive(tmp_path) -> None:
    write_feedback_exports(
        output_directory=tmp_path,
        records=[record(1, status="rejected")],
        redact_pii=False,
    )

    assert read_jsonl(tmp_path / "feedback_positive.jsonl") == []
    assert len(read_jsonl(tmp_path / "feedback_negative.jsonl")) == 1
