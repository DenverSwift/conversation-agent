# ADR 0007: Channel Abstraction

- Status: Accepted
- Date: 2026-07-25

## Context

The existing runtime is Telegram-specific, while business communication needs
core behavior that can survive channel changes and capability differences.

## Decision

Normalize channel events into a `MessageEnvelope`; keep external identifiers,
capabilities, parsing, credentials, rate limits, and send mechanics in channel
adapters. Use durable inbox/outbox records and stable idempotency keys.

## Consequences

Telegram becomes the first legacy adapter rather than a core type. Lowest-common
denominator behavior is avoided through capability checks. Duplicate delivery
and uncertain send results require connector-specific reconciliation.
