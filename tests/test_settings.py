from __future__ import annotations

import pytest

from conversation_agent.settings import Settings

ENV_KEYS = (
    "TELEGRAM_API_ID",
    "TELEGRAM_API_HASH",
    "TELEGRAM_SESSION_PATH",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "ALLOWED_TELEGRAM_USER_ID",
    "CONTEXT_MESSAGE_LIMIT",
    "README_PATH",
    "OPENAI_TIMEOUT_SECONDS",
    "TRAINER_BOT_ENABLED",
    "TRAINER_BOT_TOKEN",
    "TRAINER_TELEGRAM_USER_ID",
    "TRAINER_BOT_REVIEW_CHAT_ID",
)


def write_env(tmp_path, extra: str = ""):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TELEGRAM_API_ID=1\n"
        "TELEGRAM_API_HASH=hash\n"
        "TELEGRAM_SESSION_PATH=.secrets/matvey\n"
        "OPENAI_API_KEY=test\n"
        "OPENAI_MODEL=test-model\n"
        "ALLOWED_TELEGRAM_USER_ID=1751105897\n"
        "CONTEXT_MESSAGE_LIMIT=30\n"
        "README_PATH=README.md\n"
        "OPENAI_TIMEOUT_SECONDS=30\n"
        f"{extra}",
        encoding="utf-8",
    )
    return env_path


def clear_environment(monkeypatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_trainer_settings_are_optional_when_disabled(tmp_path, monkeypatch) -> None:
    clear_environment(monkeypatch)

    settings = Settings.load(write_env(tmp_path, "TRAINER_BOT_ENABLED=false\n"))

    assert not settings.trainer_bot_enabled
    assert settings.trainer_bot_token is None
    assert settings.prompt_version == "AAA.3"


def test_enabled_trainer_requires_token(tmp_path, monkeypatch) -> None:
    clear_environment(monkeypatch)

    with pytest.raises(ValueError, match="TRAINER_BOT_TOKEN"):
        Settings.load(
            write_env(
                tmp_path,
                "TRAINER_BOT_ENABLED=true\nTRAINER_TELEGRAM_USER_ID=123\n",
            )
        )


def test_enabled_trainer_requires_user_id(tmp_path, monkeypatch) -> None:
    clear_environment(monkeypatch)

    with pytest.raises(ValueError, match="TRAINER_TELEGRAM_USER_ID"):
        Settings.load(
            write_env(
                tmp_path,
                "TRAINER_BOT_ENABLED=true\nTRAINER_BOT_TOKEN=fake\n",
            )
        )


def test_trainer_rejects_non_private_review_chat(tmp_path, monkeypatch) -> None:
    clear_environment(monkeypatch)

    with pytest.raises(ValueError, match="must equal"):
        Settings.load(
            write_env(
                tmp_path,
                "TRAINER_BOT_ENABLED=true\n"
                "TRAINER_BOT_TOKEN=fake\n"
                "TRAINER_TELEGRAM_USER_ID=123\n"
                "TRAINER_BOT_REVIEW_CHAT_ID=-100123\n",
            )
        )



def test_trainer_token_is_hidden_from_settings_repr(tmp_path, monkeypatch) -> None:
    clear_environment(monkeypatch)
    settings = Settings.load(
        write_env(
            tmp_path,
            "TRAINER_BOT_ENABLED=true\n"
            "TRAINER_BOT_TOKEN=top-secret-token\n"
            "TRAINER_TELEGRAM_USER_ID=123\n",
        )
    )

    assert "top-secret-token" not in repr(settings)
