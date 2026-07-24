# conversation-agent

An AI communication agent that understands personal context, mimics a user's communication style, and generates natural replies across messaging platforms.

## Purpose

`conversation-agent` is a local Python application that replies in one allowed private Telegram dialog through Telethon and the OpenAI API. Release `AAA.3` adds a separate private trainer bot for reviewing generated replies without exposing the personal Telegram account to Bot API polling.

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
|   `-- adr/
|-- prompts/
|-- src/conversation_agent/
|   |-- storage/
|   |-- telegram/
|   |-- trainer/
|   |-- tools/
|   `-- training/
|-- tests/
|-- scripts/
|-- migrations/
`-- evals/
```

## Current Status

Release `AAA.3`: the reply MVP remains restricted to Telegram user `1751105897`. Generated replies are stored in local SQLite, reviewed through a separate private trainer bot, and available to the existing local export tools.

The current runtime does not load exported datasets or Fix corrections during
generation. It uses the global behavior section below plus up to 30 recent
Telegram messages and sends that context to the configured OpenAI base model.

## AA.1 Runtime Adaptation

AA.1 must implement provider-independent dynamic few-shot retrieval:

```text
incoming message
-> global Matvey style profile
-> contact-specific real human examples
-> high-priority relevant Fix corrections
-> recent conversation with provenance
-> configured OpenAI base model
-> Telegram reply
```

The complete 500-example export is not sent on every request. AA.1 will select
only a small relevant set. AI-generated replies are never style evidence,
rejected replies are never positive examples, and human-authored Fix
corrections have the highest retrieval priority. This changes request context,
not model weights.

## Versioning

Conversation Agent uses an internal Cup Size progression instead of semantic milestone versions. The current release is `AAA.3`: `AAA` identifies the infrastructure capability stage, while `.3` identifies its third engineering iteration.

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
4. Create a private bot with BotFather and fill the `TRAINER_BOT_*` settings.
5. Open the bot from the configured trainer account and send `/start`.
6. Run `scripts\start_trainer_bot.bat` in a second terminal.

Use `scripts\stop_agent.bat` and `scripts\stop_trainer_bot.bat` for clean shutdown.

## Local Feedback

Feedback is local by design because generated replies, context snapshots, and corrections contain private conversation data. With `FEEDBACK_ENABLED=true`, SQLite is created at `.runtime/feedback.sqlite3`. Set it to `false` to keep the original reply behavior without creating or requiring a database.

The trainer bot accepts these private commands:

```text
/start
/help
/status
/recent
/pending
/cancel
```

Review cards provide `Good`, `Bad`, `Fix`, `Should not reply`, and `Details`
buttons. Corrections and comments are stored locally and are never sent to the
allowed contact. `FEEDBACK_SAVED_MESSAGES_ENABLED` is retained only as a
deprecated compatibility setting; AAA.3 does not register Saved Messages
feedback commands or create cards there.

Provider-independent history and reviewed-feedback exports are written only
under `.runtime/exports/`:

```bat
scripts\export_training_data.bat
scripts\export_feedback.bat
```

See [docs/trainer-bot.md](docs/trainer-bot.md) for setup and operating details,
and [docs/feedback-and-exports.md](docs/feedback-and-exports.md) for data
handling, grouping, cleaning, deletion, and privacy. Every exported dataset
requires manual review. Datasets remain useful for runtime example retrieval,
evaluation, prompt development, and possible future training of open-weight
models. They are not an instruction or supported workflow for uploading JSONL
to OpenAI to obtain a custom hosted model.
