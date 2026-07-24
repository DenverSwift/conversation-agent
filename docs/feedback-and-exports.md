# Local Feedback and Provider-Independent Data Exports

## Local-only storage

Feedback is stored locally because generated replies, context snapshots, and corrected replies contain private conversation data. With `FEEDBACK_ENABLED=true`, the application creates:

```text
.runtime/feedback.sqlite3
```

The path can be changed with `FEEDBACK_DATABASE_PATH`. The database, runtime directory, logs, Telegram sessions, and exports are ignored by Git.

If feedback is enabled, a generated-reply record must be written before Telegram delivery. A failed initial database write blocks that delivery so the agent does not create an AI-authored Telegram message that the exporter cannot identify later. With `FEEDBACK_ENABLED=false`, replies continue normally and no feedback database is required.

## Private trainer bot

AAA.3 uses a separate Telegram Bot API account for feedback. The trainer runs
as its own long-polling process and accepts updates only from
`TRAINER_TELEGRAM_USER_ID` in the matching private
`TRAINER_BOT_REVIEW_CHAT_ID`. It never forwards trainer actions to the allowed
contact and never invokes OpenAI.

```text
/start  /help  /status  /recent  /pending  /cancel
```

Each review card shows the specific incoming text, exact generated reply,
delivery metadata, model, prompt version, and generation time. `Good` approves
the reply. `Bad` records a normalized reason or short free-text comment. `Fix`
stores Matvey's correction as the preferred answer. `Should not reply` records
a negative `should_not_reply` example. Corrections are never sent to the
contact.

Start and stop the process with:

```bat
scripts\start_trainer_bot.bat
scripts\stop_trainer_bot.bat
```

`FEEDBACK_SAVED_MESSAGES_ENABLED` is deprecated and defaults to `false`.
Historical AAA.2 feedback remains readable and exports with
`feedback_source=saved_messages`; new reviews use
`feedback_source=trainer_bot`.

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

`TRAINING_EXPORT_LIMIT=500` is an export bound, not a runtime prompt size. The
AA.1 compiler reads these files offline, and the complete dataset is never sent
on every runtime request.

## Reviewed feedback export

Run:

```bat
scripts\export_feedback.bat
```

or:

```text
uv run python -m conversation_agent.tools.export_feedback
```

The export contains all reviewed records plus separate positive and negative
files. For compatibility, approved generated replies and `/fix` corrections
can appear in the positive export. For corrected records, the human correction
is the preferred target. Rejected replies without corrections never enter the
positive file.

The positive file is not automatically a style-evidence index. AA.1 retrieval
must use only real Matvey-authored messages and corrected Fix replies as
positive style evidence. Approved AI-generated replies may remain useful for
evaluation or preference analysis, but must never teach Matvey's style.

## Separate data stages

- **Feedback collection** records Good, Bad, Fix, and Should not reply decisions
  in local SQLite.
- **Dataset export** writes provider-independent JSONL for inspection and
  downstream tools.
- **Runtime few-shot adaptation** selects a small relevant subset for each AA.1
  request.
- **Optional future training** may use an open-weight model or another
  provider-independent workflow after explicit review. It is not required for
  AA.1.

Exported datasets are useful for runtime example retrieval, evaluation, prompt
development, and possible future open-weight model training. The exporter does
not upload JSONL, call a training API, create an `ft:` model, or change model
weights.

## Cleaning and privacy

Cleaning removes empty targets, link-only targets, bot commands, forwarded targets, duplicates, examples without meaningful incoming context, and known AI-authored targets. It preserves slang, informal grammar, punctuation, and misspellings because they are style signals.

When `TRAINING_EXPORT_REDACT_PII=true`, obvious email addresses, phone numbers, sensitive-query URLs, and token-like secrets are replaced with placeholders. This is conservative pattern matching, not complete anonymization. Personal names cannot be anonymized reliably and may remain in context or replies.

Exports must be reviewed manually before retrieval indexing, evaluation,
sharing, or any optional future open-weight training.

## Deleting local data

Stop the agent first, then delete the local database and exports:

```powershell
Remove-Item -LiteralPath ".runtime\feedback.sqlite3" -ErrorAction SilentlyContinue
Remove-Item -LiteralPath ".runtime\exports" -Recurse -Force -ErrorAction SilentlyContinue
```

The database is recreated automatically on the next feedback-enabled start.
