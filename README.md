# conversation-agent

An AI communication agent that understands personal context, mimics a user's communication style, and generates natural replies across messaging platforms.

## Purpose

`conversation-agent` is an early-stage Python project for experimenting with a personal conversation assistant for Telegram and other messaging channels. The repository currently contains only documentation, prompts, configuration placeholders, and an empty source layout.

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
|   `-- decisions/
|-- prompts/
|-- src/conversation_agent/
|-- tests/
|-- scripts/
|-- migrations/
`-- evals/
```

## Current Status

Initial MVP: a local Telegram conversation agent that can reply to one allowed private chat through Telethon and the OpenAI Responses API.

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
