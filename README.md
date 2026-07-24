# conversation-agent

An AI communication agent that understands personal context, mimics a user's communication style, and generates natural replies across messaging platforms.

## Purpose

`conversation-agent` is a local Python application that replies in one allowed private Telegram dialog through Telethon and the OpenAI API. Release `AAA.2` adds local feedback collection and review-first dataset exports without implementing training or automatic personalization.

## Repository Structure

```text
conversation-agent/
|-- AGENTS.md
|-- GEMINI.md
|-- README.md
|-- plan.md
|-- pyproject.toml
|-- .env.example
|-- docs/
|   |-- vision.md
|   |-- architecture.md
|   |-- roadmap.md
|   |-- versioning.md
|   `-- decisions/
|-- prompts/
|-- src/conversation_agent/
|   |-- storage/
|   |-- telegram/
|   |-- tools/
|   `-- training/
|-- tests/
|-- scripts/
|-- migrations/
`-- evals/
```

## Current Status

Release `AAA.2`: the reply MVP remains restricted to Telegram user `1751105897`. Generated replies and explicit Saved Messages feedback can be stored in a local SQLite database, and local CLI tools can export human-authored history or reviewed feedback for manual inspection.

## Versioning

Conversation Agent uses an internal Cup Size progression instead of semantic milestone versions. The current release is `AAA.2`: `AAA` identifies the infrastructure capability stage, while `.2` identifies its second engineering iteration.

See [docs/versioning.md](docs/versioning.md) for the complete progression, rules, roadmap, and examples.

## Matvey communication behavior

- Write briefly and naturally, like Matvey replying in Telegram.
- Keep the tone direct, calm, and conversational.
- Ask a short clarifying question when the context is not enough.
- Do not invent personal facts, plans, preferences, or commitments.
- Do not sound like a formal assistant unless the conversation itself is formal.

## Local Run

1. Copy `.env.example` to `.env` and fill in local secrets.
2. Run `scripts\login_telegram.bat` once to create the Telethon session.
3. Run `scripts\start_agent.bat` to start the agent.
4. Run `scripts\stop_agent.bat` to stop the running agent by PID.

## Local Feedback

Feedback is local by design because generated replies, context snapshots, and corrections contain private conversation data. With `FEEDBACK_ENABLED=true`, SQLite is created at `.runtime/feedback.sqlite3`. Set it to `false` to keep the original reply behavior without creating or requiring a database.

When Saved Messages cards are enabled, use:

```text
/good <reply_id>
/bad <reply_id> <category or comment>
/fix <reply_id> <corrected reply>
/feedback_help
```

History and reviewed-feedback exports are written only under `.runtime/exports/`:

```bat
scripts\export_training_data.bat
scripts\export_feedback.bat
```

See [docs/feedback-and-exports.md](docs/feedback-and-exports.md) for data handling, grouping, cleaning, deletion, and privacy details. Every exported dataset requires manual review before any future training.
