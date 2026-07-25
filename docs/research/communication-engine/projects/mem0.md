# Mem0

## Snapshot

| Field | Value |
| --- | --- |
| Repository | [mem0ai/mem0](https://github.com/mem0ai/mem0) |
| Commit | [`b357a5a1b03c299ec8229c268e63cfac0f7c6566`](https://github.com/mem0ai/mem0/tree/b357a5a1b03c299ec8229c268e63cfac0f7c6566) |
| Researched | 2026-07-25 |
| License | Apache-2.0 |
| Primary language | Python/TypeScript |
| Activity | Very active; researched commit dated 2026-07-25 |
| Delivery | OSS library/server and managed platform |
| Maturity | Mature memory API with broad provider integrations |
| Purpose | Extract, store, search, revise, and delete scoped memories |

## Architecture

The OSS core is a `Memory` facade over pluggable LLM, embedder, vector store,
optional graph, reranker, and a SQLite change-history store. Fact extraction and
memory actions are orchestrated in
[`mem0/memory/main.py`](https://github.com/mem0ai/mem0/blob/b357a5a1b03c299ec8229c268e63cfac0f7c6566/mem0/memory/main.py);
provider construction is isolated behind
[`utils/factory.py`](https://github.com/mem0ai/mem0/blob/b357a5a1b03c299ec8229c268e63cfac0f7c6566/mem0/utils/factory.py).
The optional REST server has its own SQL migrations and auth.

The add flow extracts candidate facts, finds related memories, and persists new
or revised records plus history. Current v3 prompts emphasize additive,
evidence-bound extraction with related-memory links; legacy explicit CRUD
methods remain public.

## Identity and Style

Scopes include `user_id`, `agent_id`, and `run_id`. They represent ownership
dimensions, not a compiled persona or style system. There is no native
per-contact voice, negative style evidence, or human-vs-AI evidence policy.

## Memory Model

The API exposes add/get/search/update/delete/delete-all/history. Session
identifiers are validated and required for scoped list/search operations
([main implementation](https://github.com/mem0ai/mem0/blob/b357a5a1b03c299ec8229c268e63cfac0f7c6566/mem0/memory/main.py)).
Change history is recorded in local SQLite
([`storage.py`](https://github.com/mem0ai/mem0/blob/b357a5a1b03c299ec8229c268e63cfac0f7c6566/mem0/memory/storage.py)).
Recent extraction prompts preserve attribution and link contradictions to
existing memory IDs
([`prompts.py`](https://github.com/mem0ai/mem0/blob/b357a5a1b03c299ec8229c268e63cfac0f7c6566/mem0/configs/prompts.py)).
This is useful provenance, but not bitemporal truth.

## Retrieval

Vector search is native. Some vector providers also implement keyword search;
the core can fuse results and optionally apply configurable rerankers. Metadata
filters enforce `user_id`/`agent_id`/`run_id` scopes. It is a strong adapter
surface, though retrieval scores do not encode our style/Fix/business priorities.

## Prompt Construction

Mem0 constructs extraction and reconciliation prompts, not the final business
reply prompt. Its extraction instructions are extensive and version-sensitive;
applications should pin and evaluate them before upgrades.

## Feedback and Evaluation

The repository contains a memory evaluation package and API feedback endpoint,
but no native Good/Bad/Fix reply review workflow. It does not automatically
protect a style model from AI-generated messages; the caller must supply and
enforce source metadata.

## Multi-Tenancy

Entity filters provide logical scoping. They are not a workspace/domain model,
RBAC system, or database-enforced RLS boundary. Treating a caller-provided
`user_id` as sufficient tenant isolation would be unsafe.

## Business Workflow Support

No lead stages, playbooks, approval modes, opt-outs, or channel policies. Mem0 is
a memory subsystem, not a communication engine.

## Deployment and Operations

OSS can be self-hosted but requires an LLM, embedder, and vector store; graph and
reranking add dependencies. The managed platform reduces operations but adds
vendor and data-location concerns. Apache-2.0 permits library use.

## Strong Ideas

- Stable CRUD API with storage/provider abstractions.
- Required scoped filters and explicit memory history.
- Separate extraction, vector retrieval, keyword retrieval, and reranking.
- Attribute assistant-provided information rather than silently treating it as
  user-authored fact.

## Weaknesses

- Logical IDs do not equal tenant security.
- Automatic extraction can over-capture; prompts themselves recommend broad
  extraction and rely on downstream deduplication.
- No communication workflow, style compiler, approval, or audit domain.
- Full adoption brings more infrastructure than v1 needs.

## Relevance to conversation-agent

Use its API semantics and provider abstraction as a reference. Do not replace
the existing provenance-aware style compiler with Mem0. A later factual-memory
adapter can benchmark Mem0 against a simpler PostgreSQL implementation using the
same evaluation set.

## Decision

- **Use as reference now:** memory CRUD, history, provider ports.
- **Pilot later:** behind a factual-memory adapter.
- **Reject:** using entity filters as the sole multi-tenant boundary.

## Evidence

- [Memory facade and scoped APIs](https://github.com/mem0ai/mem0/blob/b357a5a1b03c299ec8229c268e63cfac0f7c6566/mem0/memory/main.py)
- [Extraction and attribution prompts](https://github.com/mem0ai/mem0/blob/b357a5a1b03c299ec8229c268e63cfac0f7c6566/mem0/configs/prompts.py)
- [SQLite history store](https://github.com/mem0ai/mem0/blob/b357a5a1b03c299ec8229c268e63cfac0f7c6566/mem0/memory/storage.py)
- [Vector-store interface](https://github.com/mem0ai/mem0/blob/b357a5a1b03c299ec8229c268e63cfac0f7c6566/mem0/vector_stores/base.py)
- [Apache-2.0 license](https://github.com/mem0ai/mem0/blob/b357a5a1b03c299ec8229c268e63cfac0f7c6566/LICENSE)
