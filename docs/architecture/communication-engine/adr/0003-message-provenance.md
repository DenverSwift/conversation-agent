# ADR 0003: Immutable Message Provenance

- Status: Accepted
- Date: 2026-07-25

## Context

Style learning, audit, evaluation, disclosure, and deletion all fail if a
message's author or derivation can be overwritten or inferred from text.

## Decision

Every message has immutable provenance: `contact`, `human_operator`,
`ai_generated`, `imported`, or `system_generated`, plus origin actor, generation
and approval links when applicable. Corrections create new records or explicit
relationships; they do not relabel historical AI output as human.

## Consequences

All ingress paths must assign provenance before persistence. Human-only style
datasets can be derived safely. Migration must preserve historical prompt
version strings and mark uncertain legacy provenance explicitly.
