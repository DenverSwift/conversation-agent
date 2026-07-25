# ADR 0009: Human Approval Modes

- Status: Accepted
- Date: 2026-07-25

## Context

Outbound business communication creates legal, reputational, and platform risk.
A global autonomous/manual switch is too coarse.

## Decision

Support `draft_only`, `approval_required`, and `auto_send_allowlisted` policy
modes per workspace, identity, workflow, and use case. Default new outbound
work to approval-required. Recheck policy immediately before delivery, and
provide kill switches.

## Consequences

Approval binds one content version and expires after relevant context or policy
changes. Auto-send requires shadow evidence and narrow allow-listing; sensitive,
uncertain, or escalated cases always return to a human.
