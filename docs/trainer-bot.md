# Private Trainer Bot

AAA.3 introduced the separate private Telegram Bot API review interface. The
current AA.1 human-agent workflow also uses it as a mandatory approval gate:
the main Telethon agent prepares drafts, and only an approved or corrected
action can start delivery to the real contact.

## Setup

1. Open the official `@BotFather` chat in Telegram.
2. Create a bot with `/newbot` and keep the returned token private.
3. Obtain the trainer's numeric Telegram user ID from a trusted Telegram ID lookup
   method or the `account_id` line written by the existing Telethon login flow.
4. Open a private chat with the new bot and press **Start**.
5. Set these values in the local `.env`:

```dotenv
TRAINER_BOT_ENABLED=true
TRAINER_BOT_TOKEN=
TRAINER_TELEGRAM_USER_ID=
TRAINER_BOT_REVIEW_CHAT_ID=
TRAINER_BOT_POLLING_ENABLED=true
```

For AAA.3, `TRAINER_BOT_REVIEW_CHAT_ID` must be the same numeric ID as
`TRAINER_TELEGRAM_USER_ID`. If the chat ID is left empty, it defaults to the
trainer user ID. Never commit `.env` or paste the token into logs or issues.

Run the agent and trainer in separate terminals:

```bat
scripts\start_agent.bat
scripts\start_trainer_bot.bat
```

Stop them cleanly with:

```bat
scripts\stop_agent.bat
scripts\stop_trainer_bot.bat
```

The trainer process uses long polling and a separate `.runtime` lock, so a
second trainer instance exits instead of polling concurrently.

## Review controls

- **Approve** queues the exact proposed bubbles for delivery.
- **Fix** waits up to 15 minutes for the reply the account owner wants to send, then queues
  that human-authored correction.
- **Reject** records a negative review and sends nothing.
- **Handoff** sends nothing and disables automatic processing for the contact.
- **Skip** closes the draft without sending.
- **Details** shows concise analysis, goal, timing, retrieval, model, and prompt
  metadata without the complete prompt.

Use `/cancel` to leave text-entry mode. Corrections and reasons are stored in
SQLite and survive a process restart. A Fix is sent only through the same
stale-aware behavior runtime used by Approve. `/recent` shows at most five
recent cards, while `/pending` shows at most five unreviewed cards. Repeated
callbacks are idempotent.

## Delivery failures

Card delivery has bounded retries. SQLite stores only the state, attempt count,
timestamp, and a concise error category. Failed or interrupted cards are retried
in a limited batch when the trainer process starts. The saved trainer message ID
prevents duplicate automatic cards.

A trainer-card failure leaves the draft undelivered. Check
`logs/trainer-bot.log` and `/status`; logs never contain message, reply,
correction, context, or token text.

## Privacy and disabling

The trainer bot rejects every user and chat except the configured private
trainer chat. Callback payloads contain only an action and local reply ID.
Review cards do not contain the full context or system instructions.

Historical AAA.2 Saved Messages reviews remain readable and exportable; no new
Saved Messages feedback is created. The AA.1 approval-first agent intentionally
refuses production startup when `TRAINER_BOT_ENABLED=false`,
`FEEDBACK_ENABLED=false`, or shadow mode is disabled.

The existing feedback exporter keeps approval outcomes available for evaluation
and treats corrected Fix text as human-authored evidence. Rejected reviews are
negative data and are never promoted to a positive target.

For AA.2 style retrieval, only human-authored Fix corrections and imported real
human history are positive owner evidence. An approved AI reply remains useful
for evaluation but is never treated as proof of Matvey's writing style.
