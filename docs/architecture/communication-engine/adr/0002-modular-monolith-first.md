# ADR 0002: Modular Monolith First

- Status: Accepted
- Date: 2026-07-25

## Context

The proposed domain has many consistency requirements but no measured scale or
team topology that justifies distributed ownership.

## Decision

Implement bounded modules in one codebase and use PostgreSQL as the transaction
boundary. Run API and worker processes independently as needed. Do not begin
with microservices, CQRS, or full event sourcing.

## Consequences

Inbox/outbox and append-only audit records preserve integration history without
making all domain state event-sourced. A module can be extracted only after
measured deployment, scale, isolation, or regulatory pressure.
