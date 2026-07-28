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
    style_compiler_state_path: Path = Path(".runtime/style/compiler_state.sqlite3")
    style_analysis_batch_size: int = 50
    log_path: Path = Path("logs/agent.log")
    runtime_dir: Path = Path(".runtime")
    shadow_mode: bool = True
    accumulation_min_wait_seconds: float = 3.0
    accumulation_max_wait_seconds: float = 12.0
    urgent_message_bypass: bool = False
    typing_speed_min_chars_per_second: float = 7.0
    typing_speed_max_chars_per_second: float = 13.0
    behavior_delay_jitter_ms: int = 350
    initial_read_delay_min_ms: int = 800
    initial_read_delay_max_ms: int = 3500
    pre_typing_delay_min_ms: int = 500
    pre_typing_delay_max_ms: int = 2500
    bubble_delay_min_ms: int = 500
    bubble_delay_max_ms: int = 1800
    max_bubble_count: int = 4
    max_message_length: int = 1200
    confidence_threshold: float = 0.55
    handoff_threshold: float = 0.25
    allowed_telegram_user_ids: tuple[int, ...] = ()
    identity_profile_path: Path = Path("config/identity.example.json")
    business_profile_path: Path = Path("config/business.example.json")
    style_profile_path: Path = Path("config/style.example.json")
    analysis_model: str = ""
    response_model: str = ""
    prompt_token_budget: int = 6000
    debug_mode: bool = False
    approval_poll_interval_seconds: float = 0.5

    @property
    def allowed_contact_ids(self) -> tuple[int, ...]:
        return self.allowed_telegram_user_ids or (self.allowed_telegram_user_id,)

    @classmethod
    def load(cls, env_file: str | Path = ".env") -> Settings:
        load_env_file(Path(env_file))
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
            openai_api_key=_required("OPENAI_API_KEY"),
            openai_model=_required("OPENAI_MODEL"),
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
            style_bundle_directory=Path(_with_default("STYLE_BUNDLE_DIRECTORY", ".runtime/style")),
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
            shadow_mode=_boolean("SHADOW_MODE", default=True),
            accumulation_min_wait_seconds=_positive_float_with_default(
                "ACCUMULATION_MIN_WAIT_SECONDS",
                3.0,
            ),
            accumulation_max_wait_seconds=_positive_float_with_default(
                "ACCUMULATION_MAX_WAIT_SECONDS",
                12.0,
            ),
            urgent_message_bypass=_boolean("URGENT_MESSAGE_BYPASS", default=False),
            typing_speed_min_chars_per_second=_positive_float_with_default(
                "TYPING_SPEED_MIN_CHARS_PER_SECOND",
                7.0,
            ),
            typing_speed_max_chars_per_second=_positive_float_with_default(
                "TYPING_SPEED_MAX_CHARS_PER_SECOND",
                13.0,
            ),
            behavior_delay_jitter_ms=_non_negative_int_with_default(
                "BEHAVIOR_DELAY_JITTER_MS",
                350,
            ),
            initial_read_delay_min_ms=_non_negative_int_with_default(
                "INITIAL_READ_DELAY_MIN_MS",
                800,
            ),
            initial_read_delay_max_ms=_non_negative_int_with_default(
                "INITIAL_READ_DELAY_MAX_MS",
                3500,
            ),
            pre_typing_delay_min_ms=_non_negative_int_with_default(
                "PRE_TYPING_DELAY_MIN_MS",
                500,
            ),
            pre_typing_delay_max_ms=_non_negative_int_with_default(
                "PRE_TYPING_DELAY_MAX_MS",
                2500,
            ),
            bubble_delay_min_ms=_non_negative_int_with_default(
                "BUBBLE_DELAY_MIN_MS",
                500,
            ),
            bubble_delay_max_ms=_non_negative_int_with_default(
                "BUBBLE_DELAY_MAX_MS",
                1800,
            ),
            max_bubble_count=_positive_int_with_default("MAX_BUBBLE_COUNT", 4),
            max_message_length=_positive_int_with_default("MAX_MESSAGE_LENGTH", 1200),
            confidence_threshold=_unit_float_with_default(
                "CONFIDENCE_THRESHOLD",
                0.55,
            ),
            handoff_threshold=_unit_float_with_default("HANDOFF_THRESHOLD", 0.25),
            allowed_telegram_user_ids=_int_tuple("ALLOWED_TELEGRAM_USER_IDS"),
            identity_profile_path=Path(
                _with_default("IDENTITY_PROFILE_PATH", "config/identity.example.json")
            ),
            business_profile_path=Path(
                _with_default("BUSINESS_PROFILE_PATH", "config/business.example.json")
            ),
            style_profile_path=Path(
                _with_default("STYLE_PROFILE_PATH", "config/style.example.json")
            ),
            analysis_model=_optional("ANALYSIS_MODEL") or _required("OPENAI_MODEL"),
            response_model=_optional("RESPONSE_MODEL") or _required("OPENAI_MODEL"),
            prompt_token_budget=_positive_int_with_default("PROMPT_TOKEN_BUDGET", 6000),
            debug_mode=_boolean("DEBUG_MODE", default=False),
            approval_poll_interval_seconds=_positive_float_with_default(
                "APPROVAL_POLL_INTERVAL_SECONDS",
                0.5,
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


def _non_negative_int_with_default(name: str, default: int) -> int:
    raw_value = _with_default(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"Setting {name} must be an integer") from exc
    if value < 0:
        raise ValueError(f"Setting {name} must be zero or greater")
    return value


def _positive_float_with_default(name: str, default: float) -> float:
    raw_value = _with_default(name, str(default))
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"Setting {name} must be a number") from exc
    if value <= 0:
        raise ValueError(f"Setting {name} must be greater than zero")
    return value


def _unit_float_with_default(name: str, default: float) -> float:
    value = _positive_float_with_default(name, default)
    if value > 1:
        raise ValueError(f"Setting {name} must be at most one")
    return value


def _int_tuple(name: str) -> tuple[int, ...]:
    raw_value = os.environ.get(name, "").strip()
    if not raw_value:
        return ()
    result: list[int] = []
    for item in raw_value.split(","):
        try:
            result.append(int(item.strip()))
        except ValueError as exc:
            raise ValueError(f"Setting {name} must be a comma-separated list of integers") from exc
    return tuple(dict.fromkeys(result))


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
