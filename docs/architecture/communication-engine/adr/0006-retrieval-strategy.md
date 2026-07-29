# ADR 0006: Progressive Retrieval Strategy

- Status: Accepted
- Date: 2026-07-25

## Context

The landscape offers lexical, vector, hybrid, and graph retrieval, but the
project has no representative benchmark proving the operational cost of every
layer.

## Decision

Use structured filters and PostgreSQL FTS first, with explicit token budgets,
source diversity, recency/validity, and evidence IDs. Benchmark pgvector and a
reranker next. Add graph retrieval only for demonstrated temporal or multi-hop
failures.

## Consequences

Each prompt plan records query, candidates, selected and excluded evidence,
scores, versions, and budgets. Provider adapters cannot hide source lineage.
More complex retrieval must beat the baseline on a versioned evaluation set.
