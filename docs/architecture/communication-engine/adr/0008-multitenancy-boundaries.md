# ADR 0008: Workspace Tenant Boundary

- Status: Accepted
- Date: 2026-07-25

## Context

Logical memory namespaces and caller-provided IDs do not prevent accidental or
malicious cross-customer access.

## Decision

Use `Workspace` as the tenant boundary. Carry `workspace_id` on all private
rows and enforce verified membership, PostgreSQL RLS, tenant-aware foreign
keys, scoped caches/queues/storage, and audited privileged access.

## Consequences

Repositories and jobs fail closed without a tenant context. Isolation tests are
mandatory for reads, writes, search, exports, traces, and background work. A
database-per-workspace placement can be introduced without changing the domain
contract.
