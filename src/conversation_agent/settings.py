"""Application settings loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
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
    feedback_enabled: bool = True
    feedback_database_path: Path = Path(".runtime/feedback.sqlite3")
    feedback_saved_messages_enabled: bool = False
    prompt_version: str = "AA.2"
    trainer_bot_enabled: bool = False
    trainer_bot_token: str | None = field(default=None, repr=False)
    trainer_telegram_user_id: int | None = None
    trainer_bot_review_chat_id: int | None = None
    trainer_bot_polling_enabled: bool = True
    training_export_directory: Path = Path(".runtime/exports")
    training_export_limit: int = 500
    training_export_context_limit: int = 10
    training_export_redact_pii: bool = True
    style_adaptation_enabled: bool = True
    style_bundle_directory: Path = Path(".runtime/style")
    style_source_examples_path: Path = Path(".runtime/exports/cleaned_examples.jsonl")
    style_analysis_model: str = "gpt-4o-mini"
    style_retrieval_limit: int = 8
    style_rules_max_chars: int = 12000
    style_examples_max_chars: int = 10000
    style_require_bundle: bool = True
    style_incremental_compilation: bool = True
    style_compiler_state_path: Path = Path(
        ".runtime/style/compiler_state.sqlite3"
    )
    style_analysis_batch_size: int = 50
    generation_mode: str = "openai_only"
    local_agent_id: str = "informal-manager"
    local_generation_provider: str = "openai_compatible"
    local_generation_base_url: str = "http://127.0.0.1:8080/v1"
    local_generation_model: str = "Qwen/Qwen3-0.6B-GGUF:Q8_0"
    local_generation_api_key: str = "local-no-key"
    local_generation_timeout_seconds: float = 30.0
    local_generation_max_output_tokens: int = 256
    local_generation_context_tokens: int = 4096
    local_generation_temperature: float = 0.7
    local_generation_top_k: int = 20
    local_generation_top_p: float = 0.9
    local_generation_min_p: float = 0.0
    local_generation_presence_penalty: float = 1.5
    local_generation_thinking: bool = False
    local_generation_seed: int | None = None
    local_generation_low_confidence_threshold: float = 0.55
    local_context_budget_chars: int = 2400
    log_path: Path = Path("logs/agent.log")
    runtime_dir: Path = Path(".runtime")

    @classmethod
    def load(cls, env_file: str | Path = ".env") -> Settings:
        load_env_file(Path(env_file))
        generation_mode = _choice(
            "GENERATION_MODE",
            "openai_only",
            {"local_only", "local_with_fallback", "openai_only", "compare_shadow"},
        )
        openai_is_required = generation_mode != "local_only"
        trainer_enabled = _boolean("TRAINER_BOT_ENABLED", default=False)
        trainer_token = _optional("TRAINER_BOT_TOKEN")
        trainer_user_id = _optional_int("TRAINER_TELEGRAM_USER_ID")
        review_chat_id = _optional_int("TRAINER_BOT_REVIEW_CHAT_ID")
        if trainer_enabled:
            if not trainer_token:
                raise ValueError("Missing required setting: TRAINER_BOT_TOKEN")
            if trainer_user_id is None:
                raise ValueError("Missing required setting: TRAINER_TELEGRAM_USER_ID")
            review_chat_id = review_chat_id or trainer_user_id
            if review_chat_id != trainer_user_id:
                raise ValueError(
                    "TRAINER_BOT_REVIEW_CHAT_ID must equal TRAINER_TELEGRAM_USER_ID "
                    "for the private trainer bot"
                )

        return cls(
            telegram_api_id=_required_int("TELEGRAM_API_ID"),
            telegram_api_hash=_required("TELEGRAM_API_HASH"),
            telegram_session_path=_required("TELEGRAM_SESSION_PATH"),
            openai_api_key=(
                _required("OPENAI_API_KEY")
                if openai_is_required
                else _with_default("OPENAI_API_KEY", "local-disabled")
            ),
            openai_model=(
                _required("OPENAI_MODEL")
                if openai_is_required
                else _with_default("OPENAI_MODEL", "local-disabled")
            ),
            allowed_telegram_user_id=_required_int("ALLOWED_TELEGRAM_USER_ID"),
            context_message_limit=_positive_int("CONTEXT_MESSAGE_LIMIT"),
            readme_path=Path(_required("README_PATH")),
            openai_timeout_seconds=_positive_float("OPENAI_TIMEOUT_SECONDS"),
            feedback_enabled=_boolean("FEEDBACK_ENABLED", default=True),
            feedback_database_path=Path(
                _with_default("FEEDBACK_DATABASE_PATH", ".runtime/feedback.sqlite3")
            ),
            feedback_saved_messages_enabled=_boolean(
                "FEEDBACK_SAVED_MESSAGES_ENABLED",
                default=False,
            ),
            prompt_version=_with_default("PROMPT_VERSION", "AA.2"),
            trainer_bot_enabled=trainer_enabled,
            trainer_bot_token=trainer_token,
            trainer_telegram_user_id=trainer_user_id,
            trainer_bot_review_chat_id=review_chat_id,
            trainer_bot_polling_enabled=_boolean(
                "TRAINER_BOT_POLLING_ENABLED",
                default=True,
            ),
            training_export_directory=Path(
                _with_default("TRAINING_EXPORT_DIRECTORY", ".runtime/exports")
            ),
            training_export_limit=_positive_int_with_default("TRAINING_EXPORT_LIMIT", 500),
            training_export_context_limit=_positive_int_with_default(
                "TRAINING_EXPORT_CONTEXT_LIMIT",
                10,
            ),
            training_export_redact_pii=_boolean(
                "TRAINING_EXPORT_REDACT_PII",
                default=True,
            ),
            style_adaptation_enabled=_boolean(
                "STYLE_ADAPTATION_ENABLED",
                default=True,
            ),
            style_bundle_directory=Path(
                _with_default("STYLE_BUNDLE_DIRECTORY", ".runtime/style")
            ),
            style_source_examples_path=Path(
                _with_default(
                    "STYLE_SOURCE_EXAMPLES_PATH",
                    ".runtime/exports/cleaned_examples.jsonl",
                )
            ),
            style_analysis_model=_with_default("STYLE_ANALYSIS_MODEL", "gpt-4o-mini"),
            style_retrieval_limit=_positive_int_with_default("STYLE_RETRIEVAL_LIMIT", 8),
            style_rules_max_chars=_positive_int_with_default(
                "STYLE_RULES_MAX_CHARS",
                12000,
            ),
            style_examples_max_chars=_positive_int_with_default(
                "STYLE_EXAMPLES_MAX_CHARS",
                10000,
            ),
            style_require_bundle=_boolean("STYLE_REQUIRE_BUNDLE", default=True),
            style_incremental_compilation=_boolean(
                "STYLE_INCREMENTAL_COMPILATION",
                default=True,
            ),
            style_compiler_state_path=Path(
                _with_default(
                    "STYLE_COMPILER_STATE_PATH",
                    ".runtime/style/compiler_state.sqlite3",
                )
            ),
            style_analysis_batch_size=_positive_int_with_default(
                "STYLE_ANALYSIS_BATCH_SIZE",
                50,
            ),
            generation_mode=generation_mode,
            local_agent_id=_with_default("LOCAL_AGENT_ID", "informal-manager"),
            local_generation_provider=_choice(
                "LOCAL_GENERATION_PROVIDER",
                "openai_compatible",
                {"fake", "openai_compatible"},
            ),
            local_generation_base_url=_with_default(
                "LOCAL_LLM_BASE_URL",
                _with_default("LOCAL_GENERATION_BASE_URL", "http://127.0.0.1:8080/v1"),
            ),
            local_generation_model=_with_default(
                "LOCAL_LLM_MODEL",
                _with_default(
                    "LOCAL_GENERATION_MODEL",
                    "Qwen/Qwen3-0.6B-GGUF:Q8_0",
                ),
            ),
            local_generation_api_key=_with_default("LOCAL_LLM_API_KEY", "local-no-key"),
            local_generation_timeout_seconds=_positive_float_with_default(
                "LOCAL_LLM_TIMEOUT_SECONDS",
                _positive_float_with_default("LOCAL_GENERATION_TIMEOUT_SECONDS", 30.0),
            ),
            local_generation_max_output_tokens=_positive_int_with_default(
                "LOCAL_LLM_MAX_OUTPUT_TOKENS",
                _positive_int_with_default("LOCAL_GENERATION_MAX_OUTPUT_TOKENS", 256),
            ),
            local_generation_context_tokens=_positive_int_with_default(
                "LOCAL_LLM_CONTEXT_TOKENS",
                4096,
            ),
            local_generation_temperature=_non_negative_float_with_default(
                "LOCAL_LLM_TEMPERATURE",
                _non_negative_float_with_default("LOCAL_GENERATION_TEMPERATURE", 0.7),
            ),
            local_generation_top_k=_positive_int_with_default("LOCAL_LLM_TOP_K", 20),
            local_generation_top_p=_positive_float_with_default(
                "LOCAL_LLM_TOP_P",
                _positive_float_with_default("LOCAL_GENERATION_TOP_P", 0.9),
            ),
            local_generation_min_p=_non_negative_float_with_default("LOCAL_LLM_MIN_P", 0.0),
            local_generation_presence_penalty=_non_negative_float_with_default(
                "LOCAL_LLM_PRESENCE_PENALTY",
                1.5,
            ),
            local_generation_thinking=_boolean("LOCAL_LLM_THINKING", default=False),
            local_generation_seed=_optional_int("LOCAL_LLM_SEED")
            or _optional_int("LOCAL_GENERATION_SEED"),
            local_generation_low_confidence_threshold=_positive_float_with_default(
                "LOCAL_GENERATION_LOW_CONFIDENCE_THRESHOLD",
                0.55,
            ),
            local_context_budget_chars=_positive_int_with_default(
                "LOCAL_CONTEXT_BUDGET_CHARS",
                2400,
            ),
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


def _positive_float_with_default(name: str, default: float) -> float:
    raw_value = _with_default(name, str(default))
    try:
        parsed = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"Setting {name} must be a number") from exc
    if parsed <= 0:
        raise ValueError(f"Setting {name} must be greater than zero")
    return parsed


def _non_negative_float_with_default(name: str, default: float) -> float:
    raw_value = _with_default(name, str(default))
    try:
        parsed = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"Setting {name} must be a number") from exc
    if parsed < 0:
        raise ValueError(f"Setting {name} must be non-negative")
    return parsed


def _with_default(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip()
    if not value:
        raise ValueError(f"Setting {name} must not be empty")
    return value


def _optional(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _optional_int(name: str) -> int | None:
    value = _optional(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Setting {name} must be an integer") from exc


def _positive_int_with_default(name: str, default: int) -> int:
    raw_value = _with_default(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"Setting {name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"Setting {name} must be greater than zero")
    return value


def _boolean(name: str, *, default: bool) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Setting {name} must be true or false")


def _choice(name: str, default: str, allowed: set[str]) -> str:
    value = _with_default(name, default)
    if value not in allowed:
        options = ", ".join(sorted(allowed))
        raise ValueError(f"Setting {name} must be one of: {options}")
    return value
