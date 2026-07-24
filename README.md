# conversation-agent

An AI communication agent that understands personal context, mimics a user's communication style, and generates natural replies across messaging platforms.

## Purpose

`conversation-agent` is a local Python application that replies in one allowed private Telegram dialog through Telethon and the OpenAI API. Release `AA.1` adds persistent runtime Matvey behavior rules and dynamic local example retrieval without modifying model weights.

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
|   |-- runtime-style-rules.md
|   |-- style-runtime-audit.md
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

Release `AA.1`: the agent remains restricted to Telegram user `1751105897`.
Generated replies and trainer feedback stay in local SQLite. A compiled local
rulebook is included in each style-enabled model request, and a small relevant
set of real Matvey examples and Fix corrections is retrieved dynamically.

## AA.1 Runtime Adaptation

AA.1 implements provider-independent dynamic few-shot retrieval:

```text
incoming message
-> global Matvey style profile
-> contact-specific real human examples
-> high-priority relevant Fix corrections
-> recent conversation with provenance
-> configured OpenAI base model
-> Telegram reply
```

The complete 500-example export is analyzed offline and is not sent on every
request. Runtime selects only a small relevant set. AI-generated replies are never style evidence,
rejected replies are never positive examples, and human-authored Fix
corrections have the highest retrieval priority. This changes request context,
not model weights.

The compiled behavior rulebook is analogous to persistent project instructions
such as `AGENTS.md`: it condenses observed rules and is supplied on every
request. Original examples remain in a private local example bank.

## Versioning

Conversation Agent uses an internal Cup Size progression instead of semantic milestone versions. The current release is `AA.1`: `AA` identifies the base-personality capability stage, while `.1` identifies its first engineering iteration.

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

For AA.1 style adaptation, prepare local private artifacts before starting the
agent:

```bat
scripts\export_training_data.bat
scripts\build_style_bundle.bat
scripts\inspect_style_runtime.bat
```

The export and style bundle are intentionally not synchronized by Git. Build
them on every runtime device that has the Telegram session and private data, or
copy them through a separately secured private channel.

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
[docs/runtime-style-rules.md](docs/runtime-style-rules.md) for AA.1 style setup,
and [docs/feedback-and-exports.md](docs/feedback-and-exports.md) for data
handling, grouping, cleaning, deletion, and privacy. Every exported dataset
requires manual review. Datasets remain useful for runtime example retrieval,
evaluation, prompt development, and possible future training of open-weight
models. They are not an instruction or supported workflow for uploading JSONL
to OpenAI to obtain a custom hosted model.
