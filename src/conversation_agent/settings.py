"""Application settings loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    telegram_api_id: int
    telegram_api_hash: str
    telegram_session_path: str
    openai_api_key: str
    openai_model: str
    allowed_telegram_user_id: int
    context_message_limit: int
    readme_path: Path
    openai_timeout_seconds: float
    log_path: Path = Path("logs/agent.log")
    runtime_dir: Path = Path(".runtime")

    @classmethod
    def load(cls, env_file: str | Path = ".env") -> Settings:
        load_env_file(Path(env_file))
        return cls(
            telegram_api_id=_required_int("TELEGRAM_API_ID"),
            telegram_api_hash=_required("TELEGRAM_API_HASH"),
            telegram_session_path=_required("TELEGRAM_SESSION_PATH"),
            openai_api_key=_required("OPENAI_API_KEY"),
            openai_model=_required("OPENAI_MODEL"),
            allowed_telegram_user_id=_required_int("ALLOWED_TELEGRAM_USER_ID"),
            context_message_limit=_positive_int("CONTEXT_MESSAGE_LIMIT"),
            readme_path=Path(_required("README_PATH")),
            openai_timeout_seconds=_positive_float("OPENAI_TIMEOUT_SECONDS"),
        )


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Missing required setting: {name}")
    return value


def _required_int(name: str) -> int:
    value = _required(name)
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Setting {name} must be an integer") from exc


def _positive_int(name: str) -> int:
    value = _required_int(name)
    if value <= 0:
        raise ValueError(f"Setting {name} must be greater than zero")
    return value


def _positive_float(name: str) -> float:
    value = _required(name)
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"Setting {name} must be a number") from exc
    if parsed <= 0:
        raise ValueError(f"Setting {name} must be greater than zero")
    return parsed
