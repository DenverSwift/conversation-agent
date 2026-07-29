# ADR 0001: Product Boundaries

- Status: Accepted
- Date: 2026-07-25

## Context

The current personal Telegram agent could expand into an agent framework, CRM,
lead-sourcing system, or communication product. Combining those scopes would
hide the trust boundary and delay a usable business pilot.

## Decision

Build a multi-tenant engine for drafting, approving, sending, receiving, and
learning from business communication. Include contact/conversation context,
workflow state, policy, provenance, feedback, and audit. Exclude autonomous
lead sourcing, bulk-spam tooling, full CRM/help desk, general agent runtime, and
undisclosed impersonation.

## Consequences

The engine can integrate with CRM, inbox, and model systems without owning
them. Product work must define an explicit initial workflow and lawful contact
source. Features outside the boundary need a new decision.
