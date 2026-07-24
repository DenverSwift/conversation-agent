from __future__ import annotations

from conversation_agent.agent.prompt_builder import build_instructions, load_readme_behavior


def test_readme_behavior_is_used_in_instructions(tmp_path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(
        "# conversation-agent\n\n"
        "## Matvey communication behavior\n\n"
        "- Reply like Matvey.\n"
        "- Keep it concise.\n\n"
        "## Other Section\n\n"
        "Ignore this.\n",
        encoding="utf-8",
    )

    behavior = load_readme_behavior(readme)
    instructions = build_instructions(readme)

    assert "Reply like Matvey" in behavior
    assert "Keep it concise" in instructions
    assert "Ignore this" not in instructions
    assert "Return only the Telegram message text" in instructions
