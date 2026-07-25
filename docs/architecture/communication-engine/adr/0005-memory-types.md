# ADR 0005: Typed, Source-Linked Memory

- Status: Accepted
- Date: 2026-07-25

## Context

A single undifferentiated memory collection mixes facts, events, relationships,
style, workflow state, and model summaries with different validity rules.

## Decision

Keep authoritative business entities in their owning modules. Represent
derived memory as typed fact, event, and relationship observations with
workspace, subject, source message, extractor version, confidence, observed
time, valid time, expiry, and supersession. Keep style evidence and workflow
state separate.

## Consequences

Retrieval can filter by purpose and validity, corrections remain traceable, and
source deletion can invalidate derivatives. A graph or external memory service
may index these observations but is not authoritative.
