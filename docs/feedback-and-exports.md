# Local Feedback and Data Exports

## Local-only storage

Feedback is stored locally because generated replies, context snapshots, and corrected replies contain private conversation data. With `FEEDBACK_ENABLED=true`, the application creates:

```text
.runtime/feedback.sqlite3
```

The path can be changed with `FEEDBACK_DATABASE_PATH`. The database, runtime directory, logs, Telegram sessions, and exports are ignored by Git.

If feedback is enabled, a generated-reply record must be written before Telegram delivery. A failed initial database write blocks that delivery so the agent does not create an AI-authored Telegram message that the exporter cannot identify later. With `FEEDBACK_ENABLED=false`, replies continue normally and no feedback database is required.

## Saved Messages commands

Commands are accepted only from Matvey's own Saved Messages chat. They are never forwarded to the allowed user and never invoke OpenAI.

```text
/good <reply_id>
/bad <reply_id> <category or comment>
/fix <reply_id> <corrected reply>
/feedback_help
```

`/good` approves the generated reply. `/bad` rejects it and stores either a normalized category or free-text comment. `/fix` stores Matvey's correction as the preferred answer. Saved Messages cards can be disabled with `FEEDBACK_SAVED_MESSAGES_ENABLED=false`; commands remain available while feedback storage is enabled.

## Telegram history export

Run:

```bat
scripts\export_training_data.bat
```

or:

```text
uv run python -m conversation_agent.tools.export_training_data
```

The exporter reads only the private dialog configured by `ALLOWED_TELEGRAM_USER_ID`. It never modifies Telegram messages. It scans messages oldest to newest, keeps text-only messages, and builds targets only from outgoing messages authored by Matvey.

Consecutive fragments from the same side are joined with newlines when there is no intervening excluded or opposite-side message and the time gap is at most three minutes. This keeps short Telegram message bursts together while avoiding broad conversational merging.

Known agent-generated outgoing messages are excluded using sent Telegram message IDs stored in the feedback database. Messages generated before ID tracking existed cannot be identified with certainty, so all exports require manual review.

Files are written under `TRAINING_EXPORT_DIRECTORY`, which defaults to `.runtime/exports/`:

```text
raw_examples.jsonl
cleaned_examples.jsonl
export_summary.json
```

The summary contains aggregate counts only, never conversation text.

## Reviewed feedback export

Run:

```bat
scripts\export_feedback.bat
```

or:

```text
uv run python -m conversation_agent.tools.export_feedback
```

The export contains all reviewed records plus separate positive and negative files. Approved replies and `/fix` corrections can appear as positive targets. For corrected records, the correction is the preferred target. Rejected replies without corrections never become positive supervised examples.

## Cleaning and privacy

Cleaning removes empty targets, link-only targets, bot commands, forwarded targets, duplicates, examples without meaningful incoming context, and known AI-authored targets. It preserves slang, informal grammar, punctuation, and misspellings because they are style signals.

When `TRAINING_EXPORT_REDACT_PII=true`, obvious email addresses, phone numbers, sensitive-query URLs, and token-like secrets are replaced with placeholders. This is conservative pattern matching, not complete anonymization. Personal names cannot be anonymized reliably and may remain in context or replies.

Exports must be reviewed manually before any future training, sharing, or upload.

## Deleting local data

Stop the agent first, then delete the local database and exports:

```powershell
Remove-Item -LiteralPath ".runtime\feedback.sqlite3" -ErrorAction SilentlyContinue
Remove-Item -LiteralPath ".runtime\exports" -Recurse -Force -ErrorAction SilentlyContinue
```

The database is recreated automatically on the next feedback-enabled start.
