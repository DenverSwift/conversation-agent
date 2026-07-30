"""LoRA/QLoRA training dry-run planner."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from conversation_agent.local_slm.stage2_dataset import assert_training_source_allowed


@dataclass(frozen=True)
class TrainingDryRunSummary:
    dataset_examples: int
    train_examples: int
    test_examples: int
    base_model: str
    adapter_output_dir: str
    estimated_batches: int
    trainable_parameter_note: str
    gpu_required_for_real_run: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_examples": self.dataset_examples,
            "train_examples": self.train_examples,
            "test_examples": self.test_examples,
            "base_model": self.base_model,
            "adapter_output_dir": self.adapter_output_dir,
            "estimated_batches": self.estimated_batches,
            "trainable_parameter_note": self.trainable_parameter_note,
            "gpu_required_for_real_run": self.gpu_required_for_real_run,
        }


def training_dry_run(
    *,
    dataset_path: Path,
    base_model: str,
    adapter_output_dir: Path,
    batch_size: int = 4,
    lora_rank: int = 16,
) -> TrainingDryRunSummary:
    assert_training_source_allowed(dataset_path)
    rows = [
        json.loads(line)
        for line in dataset_path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    train = sum(item.get("split") == "train" for item in rows)
    test = sum(item.get("split") == "test" for item in rows)
    estimated_batches = (train + batch_size - 1) // batch_size if batch_size > 0 else 0
    return TrainingDryRunSummary(
        dataset_examples=len(rows),
        train_examples=train,
        test_examples=test,
        base_model=base_model,
        adapter_output_dir=str(adapter_output_dir),
        estimated_batches=estimated_batches,
        trainable_parameter_note=(
            f"LoRA rank {lora_rank}; exact trainable parameters require loading model config"
        ),
        gpu_required_for_real_run=True,
    )
