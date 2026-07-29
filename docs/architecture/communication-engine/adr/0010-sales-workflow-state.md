# ADR 0010: Deterministic Sales Workflow State

- Status: Accepted
- Date: 2026-07-25

## Context

Free-form agents can draft persuasive language but are unreliable owners of
timers, opt-outs, campaign eligibility, and auditable business-stage changes.

## Decision

Store workflow instances and transitions as deterministic, versioned state
machines. Models may propose classifications, facts, and drafts; application
rules validate every transition and policy controls every action.

## Consequences

Campaign, follow-up, qualification, escalation, and opt-out remain observable
and recoverable. Database-backed jobs are sufficient for v1; a workflow engine
is considered only after measured orchestration complexity.
