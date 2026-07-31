# Stage 3C Provenance Recovery Results

## Identity

- Date: 2026-07-31
- Branch: `experiment/local-telegram-slm`
- Starting commit: `46d5daae67b7d7b63473efe1c8bb79d2ef473617`
- Status: `READY_FOR_BATCH_REVIEW`
- User review: pending

## Discovery

- Git worktrees inspected: 2
- Candidate SQLite databases: 2
- Readable databases: 2
- Whole-disk scan: false
- Database access: read-only

The current experimental worktree had no feedback database. Discovery found
the existing feedback/send audit and style compiler state in another Git
worktree of the same repository. No database was copied, modified, or included
in Git.

## Reconciliation

- Exact chat/message authoritative matches: 22
- Authoritative destination/hash/time human matches: 175
- Secondary-only hash/time matches: 278
- `human_confirmed` messages: 175
- `ai_generated` messages: 22
- `human_edited_ai` messages: 0
- `unknown_historical` messages: 523
- `conflicting_evidence` messages: 0

Episode result:

- authoritative human episodes: 83
- confirmed AI episodes excluded: 22
- unknown review-required episodes: 95
- candidate episodes after contamination filtering: 178

Heuristic flags changed no provenance verdict.

## Curation

- PII review records: 24
- Batch count: 19
- Suggested balanced first pilot: 82
- Reconciliation fingerprint prefix: `232ea39ae9bb`
- Batch decisions approved: 0
- PII decisions approved: 0

The Stage 3B preview remained byte-for-byte unchanged. Batch and PII review
artifacts are local and ignored by Git. Neither the old confirmation command
nor curated confirmation was executed.

## Decision

The 22 authoritative AI-contaminated episodes cannot become human targets.
The 83 authoritative human episodes may enter a future curated pilot after PII
review. The 95 unknown episodes remain excluded from human style evidence
unless their batches receive explicit human, consent, and privacy approval.

No OpenAI, local model, embedding, training, Telegram write, or production
configuration change occurred.
