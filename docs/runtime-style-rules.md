# AA.1 Runtime Matvey Behavior Rules

AA.1 adapts the configured base model at request time. It does not upload a
fine-tuning dataset or modify model weights.

## Data flow

```text
up to 500 real Matvey examples + reviewed local feedback
-> staged offline style compiler
-> .runtime/style/matvey_behavior_rules.md
-> .runtime/style/style_profile.json
-> .runtime/style/example_bank.jsonl
-> .runtime/style/contacts/1751105897.json

incoming message
-> core identity and safety
-> manual overrides
-> compiled behavior rulebook
-> contact profile
-> relevant real examples and immediate Fix corrections
-> recent conversation with provenance
-> configured OpenAI base model
-> Telegram reply
```

The rulebook is analogous to persistent project instructions such as
`AGENTS.md`: it converts repeated observed behavior into compact, high-priority
instructions included in every style-enabled request. The compiler analyzes all
qualifying source examples in bounded batches, merges every batch observation,
and writes artifacts only after the complete build succeeds.

## Setup

Configure `.env`:

```dotenv
STYLE_ADAPTATION_ENABLED=true
STYLE_BUNDLE_DIRECTORY=.runtime/style
STYLE_SOURCE_EXAMPLES_PATH=.runtime/exports/cleaned_examples.jsonl
STYLE_ANALYSIS_MODEL=gpt-4o-mini
STYLE_RETRIEVAL_LIMIT=8
STYLE_RULES_MAX_CHARS=12000
STYLE_EXAMPLES_MAX_CHARS=10000
STYLE_REQUIRE_BUNDLE=true
PROMPT_VERSION=AA.1
```

Then run on the device that has the Telegram session and private conversation:

```bat
scripts\export_training_data.bat
scripts\build_style_bundle.bat
scripts\inspect_style_runtime.bat
scripts\start_agent.bat
```

`build_style_bundle` uses the configured OpenAI analysis model with
`store=False`. It sends private source batches only for the explicit local
rulebook build. It never logs source text or summaries.

If `STYLE_REQUIRE_BUNDLE=true`, startup fails with the exact build command when
artifacts are missing. Set `STYLE_ADAPTATION_ENABLED=false` to preserve the
previous AAA.3 prompt path.

## Evidence policy

- Real outgoing Matvey messages are positive style evidence.
- Human-authored Fix corrections are positive evidence with highest priority
  and become searchable immediately without a bundle rebuild.
- Approved AI replies remain evaluation data but are not Matvey style evidence.
- Bad and `should_not_reply` records are explicitly negative evidence.
- AI-generated recent messages may remain for conversation continuity but carry
  `ai_generated` provenance.
- Profanity, slang, misspellings, lowercase text, informal grammar, fragments,
  and unusual punctuation are preserved when present in real evidence.

Retrieval uses deterministic weighted token similarity plus contact, intent,
profanity, recency, and feedback-source bonuses. It selects eight examples by
default, deduplicates them, and ranks Fix corrections highest. It does not
require embeddings and never sends the full 500-example bank per request.

## Prompt priority and safety

1. Core product identity and safety boundaries.
2. `.runtime/style/manual_overrides.md`.
3. Compiled behavior rules.
4. Contact profile.
5. Retrieved examples, with Fix first.
6. Recent conversation, trimmed oldest-first when necessary.

The model may follow evidenced ordinary profanity, slang, reciprocal teasing,
and short reciprocal insults. It must not invent genuine threats, blackmail,
doxxing, hate-based abuse, or sustained harassment. It must not blindly mirror
every insult.

Optional manual overrides are never overwritten during rebuilds. Examples:

```markdown
- Matvey may use profanity with this contact.
- Do not respond like a therapist.
- Do not use exclamation marks in routine greetings.
- On "Дарова", do not answer "Привет! Как дела?" unless context requires it.
```

## Inspection and evaluation

Safe metadata inspection:

```bat
scripts\inspect_style_runtime.bat
```

Private prompt inspection requires an explicit flag and prints a warning:

```bat
scripts\inspect_style_runtime.bat --show-private-content
```

Local A-E comparison writes private results under `.runtime/style/` and never
sends Telegram messages:

```bat
scripts\evaluate_style.bat
```

Automatic metrics are supporting evidence only and cannot establish
human-level imitation.

## Multi-device privacy

`.env`, Telegram sessions, exports, SQLite, style artifacts, manual overrides,
and evaluation results are ignored by Git. They do not synchronize between
devices. Rebuild the bundle on each runtime device or copy it through a
separately secured private channel.
