# ADR 0004: Source-Bound Style Evidence and Compilation

- Status: Accepted
- Date: 2026-07-25

## Context

Raw chat history is noisy, private, and may contain AI replies. Rebuilding style
on every run wastes model calls and makes releases irreproducible.

## Decision

Keep style separate from identity, memory, and workflow. Only allow-listed,
human-authored evidence with source hashes enters a provider-independent,
incremental compiler. Unchanged private evidence reuses cached observations;
an analyzer fingerprint change requires explicit full rebuild. Publish versions
atomically.

## Consequences

AI feedback affects evaluation but does not become human evidence unless a
human supplies the replacement. Deletion or consent changes invalidate linked
evidence and trigger the smallest safe recompilation.
