# AA.2 Runtime Matvey Behavior Rules

AA.2 adapts the configured base model at request time. It does not upload a
fine-tuning dataset or modify model weights. It extends AA.1 with a private,
incremental compiler so unchanged evidence is analyzed once per runtime device.

## Data flow

```text
current private sources
-> canonical source keys and SHA-256 content hashes
-> compare with .runtime/style/compiler_state.sqlite3
-> reuse unchanged and duplicate-content observations
-> analyze only new and modified unique evidence
-> remove deleted and superseded contributions
-> deterministic observation merge
-> complete runtime bundle

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

The complete export is never sent on every runtime request. Compilation and
runtime retrieval are separate: the compiler may inspect up to 500 local
examples offline, while live retrieval selects only a small relevant set.

## Configuration

```dotenv
STYLE_ADAPTATION_ENABLED=true
STYLE_BUNDLE_DIRECTORY=.runtime/style
STYLE_SOURCE_EXAMPLES_PATH=.runtime/exports/cleaned_examples.jsonl
STYLE_ANALYSIS_MODEL=gpt-4o-mini
STYLE_RETRIEVAL_LIMIT=8
STYLE_RULES_MAX_CHARS=12000
STYLE_EXAMPLES_MAX_CHARS=10000
STYLE_REQUIRE_BUNDLE=true
STYLE_INCREMENTAL_COMPILATION=true
STYLE_COMPILER_STATE_PATH=.runtime/style/compiler_state.sqlite3
STYLE_ANALYSIS_BATCH_SIZE=50
PROMPT_VERSION=AA.2
```

Incremental compilation is enabled by default. Disabling it does not silently
fall back to a costly corpus rebuild; an explicit `--full-rebuild` is required.

## Build commands

Export private human examples, build, inspect, and start:

```bat
scripts\export_training_data.bat
scripts\build_style_bundle.bat
scripts\inspect_style_runtime.bat
scripts\start_agent.bat
```

Preview aggregate work without displaying conversation text:

```bat
scripts\build_style_bundle.bat --dry-run
```

Show safe local state and pending counts:

```bat
scripts\build_style_bundle.bat --status
```

Explicitly discard compatible cached interpretation and reanalyze unique
sources:

```bat
scripts\build_style_bundle.bat --full-rebuild
```

The full rebuild prints a warning and may use significantly more time and API
tokens. It is never triggered automatically.

Regenerate artifacts from unchanged compatible cached observations:

```bat
scripts\build_style_bundle.bat --force-resynthesize
```

## Source identity and comparison

Telegram sources use stable message identities such as
`telegram:<dialog_id>:<message_ids>`. Feedback uses identities such as
`feedback:<reply_id>:fix`. The content hash is deterministic JSON over
normalized style-relevant fields: source type, contact, incoming text, reply or
correction, feedback state, relevant context, provenance, and evidence
polarity. Timestamps and file modification times do not define identity.

Each build classifies sources as unchanged, new, modified, deleted, invalid, or
duplicate content:

- unchanged sources reuse their cached observations;
- new and modified unique content is analyzed in bounded delta batches;
- modified old contributions are replaced;
- deleted contributions are removed without reanalyzing unchanged text;
- another source with an already analyzed content hash reuses that analysis;
- repeated evidence still adds a supporting source and increases rule
  confidence without creating duplicate prose.

An identical build performs zero OpenAI calls, consumes zero analysis tokens,
does not rewrite bundle artifacts, and reports `build_mode=no_op`.

## Compiler state and fingerprint

`.runtime/style/compiler_state.sqlite3` contains the state schema version,
compiler and normalization versions, analyzer prompt and model fingerprint,
per-source hashes, structured observations, provenance, contribution IDs,
timestamps, content-hash cache, and last successful bundle ID.

The analysis fingerprint covers every setting that changes interpretation,
including the analyzer model and prompt template, observation schema, compiler
and normalization versions, batch size, and evidence policy. A mismatch stops
before any API call and requests an explicit `--full-rebuild`.

Existing AA.1 bundle files without compiler state remain usable until a build is
requested. Their first AA.2 build is an initial full build. The old bundle and
state remain untouched if that analysis fails.

## Evidence policy

- Real outgoing Matvey messages are positive style evidence.
- Human-authored Fix corrections are positive evidence with highest priority.
- Approved AI replies remain evaluation data, never Matvey style evidence.
- Bad and `should_not_reply` records are negative evidence.
- AI-generated recent messages retain `ai_generated` provenance.
- Slang, profanity, misspellings, lowercase text, fragments, and unusual
  punctuation are preserved when supported by real evidence.

Structured observations include behavior category, normalized instruction,
context and contact scope, confidence, polarity, source priority, and
supporting source keys and hashes. Final rules are rendered deterministically
from all currently valid observations, so obsolete behavior disappears when
its final supporting source disappears.

## Prompt priority and safety

1. Core product identity and safety boundaries.
2. `.runtime/style/manual_overrides.md`.
3. Compiled behavior rules.
4. Contact profile.
5. Retrieved examples, with Fix first.
6. Recent conversation, trimmed oldest-first when necessary.

The model may follow evidenced ordinary profanity, slang, reciprocal teasing,
and short reciprocal insults. It must not invent genuine threats, blackmail,
doxxing, hate-based abuse, or sustained harassment. Manual overrides are never
overwritten during compilation.

## Inspection and evaluation

Safe metadata inspection does not print rules or conversations:

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

## Failure safety

Analysis and artifact generation happen before publication. Bundle files and a
replacement SQLite state are written to temporary paths and atomically
replaced with rollback protection. A failed delta does not mark sources as
compiled and does not publish a partial rulebook; the same delta remains
pending on the next run.

`build_summary.json` contains aggregate counts, fingerprints, request counts,
available token usage, and artifact hashes. It contains no conversation text.
Token numbers are omitted when the provider does not report them.

## Multi-device privacy

Application code synchronizes through GitHub. `.env`, Telegram sessions,
exports, feedback SQLite, compiler state, cached observations, generated style
files, manual overrides, and evaluation results do not.

Each device maintains its own successful compiler state. A new device performs
one initial build and later builds are incremental. Copying only rulebook files
without the matching compiler state is insufficient. Trusted devices may use a
secure private backup or transfer of the complete `.runtime/style/` directory.
None of this private data belongs in Git, and AA.2 adds no cloud synchronization.
