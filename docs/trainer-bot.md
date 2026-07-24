# Private Trainer Bot

AAA.3 replaces the old Saved Messages review interface with a separate private
Telegram Bot API bot. The main Telethon agent still sends conversation replies;
the trainer bot only records human review in the shared local SQLite database.

## Setup

1. Open the official `@BotFather` chat in Telegram.
2. Create a bot with `/newbot` and keep the returned token private.
3. Obtain Matvey's numeric Telegram user ID from a trusted Telegram ID lookup
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

- **Good** approves the exact generated reply.
- **Bad** opens normalized quality categories. **Other** waits up to 15 minutes
  for a free-text reason.
- **Fix** waits up to 15 minutes for the reply Matvey should have written.
- **Should not reply** records a negative `should_not_reply` example.
- **Details** shows concise delivery and model metadata without full context.

Use `/cancel` to leave text-entry mode. Corrections and reasons are stored in
SQLite, survive a process restart, and are never sent to the real conversation
contact. `/recent` shows at most five recent cards, while `/pending` shows at
most five unreviewed cards.

## Delivery failures

Card delivery has bounded retries. SQLite stores only the state, attempt count,
timestamp, and a concise error category. Failed or interrupted cards are retried
in a limited batch when the trainer process starts. The saved trainer message ID
prevents duplicate automatic cards.

A trainer-card failure never retracts or blocks a conversation reply that
Telegram has already accepted. Check `logs/trainer-bot.log` and `/status`; logs
never contain message, reply, correction, context, or token text.

## Privacy and disabling

The trainer bot rejects every user and chat except the configured private
trainer chat. Callback payloads contain only an action and local reply ID.
Review cards do not contain the full context or system instructions.

Set `TRAINER_BOT_ENABLED=false` and do not start the trainer process to disable
the UI. The main agent and SQLite feedback storage continue working. Historical
AAA.2 Saved Messages reviews remain readable and exportable; AAA.3 does not
create or process new Saved Messages feedback.

The existing feedback exporter treats approved and corrected reviews as
positive data, rejected reviews as negative data, and never promotes an
uncorrected rejection to a positive target.

For AA.1 style retrieval, only human-authored Fix corrections are positive
Matvey evidence. A Good-reviewed AI reply remains useful for evaluation but is
never treated as proof of Matvey's writing style.
