"""Local model and adapter registry metadata."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class AdapterSpec:
    agent_id: str
    base_model: str
    adapter_id: str
    adapter_version: str
    active: bool
    dataset_fingerprint: str
    output_path: str


class AgentAdapterRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, AdapterSpec]:
        if not self.path.is_file():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return {
            str(item["agent_id"]): AdapterSpec(
                agent_id=str(item["agent_id"]),
                base_model=str(item["base_model"]),
                adapter_id=str(item["adapter_id"]),
                adapter_version=str(item["adapter_version"]),
                active=bool(item["active"]),
                dataset_fingerprint=str(item["dataset_fingerprint"]),
                output_path=str(item["output_path"]),
            )
            for item in raw.get("adapters", [])
        }

    def select(self, agent_id: str) -> AdapterSpec | None:
        spec = self.load().get(agent_id)
        if spec is not None and spec.active:
            return spec
        return None

    def save(self, adapters: list[AdapterSpec]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"adapters": [asdict(item) for item in adapters]},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

