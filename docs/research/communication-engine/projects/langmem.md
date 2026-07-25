# LangMem

## Snapshot

| Field | Value |
| --- | --- |
| Repository | [langchain-ai/langmem](https://github.com/langchain-ai/langmem) |
| Commit | [`56d85939d80bb731bd5e237567148d817d7bfd16`](https://github.com/langchain-ai/langmem/tree/56d85939d80bb731bd5e237567148d817d7bfd16) |
| Researched | 2026-07-25 |
| License | MIT |
| Primary language | Python |
| Activity | Active; researched commit dated 2026-07-25 |
| Delivery | Library plus LangGraph deployment examples |
| Maturity | Incubating library with tests and detailed guides |
| Purpose | Extract/manage long-term memories and optimize prompts from trajectories |

## Architecture

LangMem is a library, not a full agent runtime. Its main boundaries are
schema-driven knowledge extraction, stateful memory management over LangGraph's
`BaseStore`, background reflection, short-term summarization, and prompt
optimization. The public construction functions live in
[`knowledge/extraction.py`](https://github.com/langchain-ai/langmem/blob/56d85939d80bb731bd5e237567148d817d7bfd16/src/langmem/knowledge/extraction.py);
background/local/remote execution is isolated in
[`reflection.py`](https://github.com/langchain-ai/langmem/blob/56d85939d80bb731bd5e237567148d817d7bfd16/src/langmem/reflection.py).

The hot path can manage memory inline. The reflection path queues work and can
deduplicate pending tasks before applying it to a store. Storage, scheduling,
authentication, and durable execution are supplied by LangGraph or the host.

## Identity and Style

There is no native persona or per-contact style model. Structured memory schemas
can represent them, and prompt optimization can change procedural instructions,
but evidence provenance and human/AI authorship policy must be added by the
application.

## Memory Model

The guides distinguish semantic knowledge, episodic experience, and procedural
instructions. The manager can extract, update, and delete schema instances using
tool calls. Namespace templates provide user/team/domain partitioning
([semantic memory guide](https://github.com/langchain-ai/langmem/blob/56d85939d80bb731bd5e237567148d817d7bfd16/docs/docs/guides/extract_semantic_memories.md)).
Episodic examples store observation, thoughts, action, and result
([episodic guide](https://github.com/langchain-ai/langmem/blob/56d85939d80bb731bd5e237567148d817d7bfd16/docs/docs/guides/extract_episodic_memories.md)).
Temporal validity and conflict history are not a native domain model.

## Retrieval

Retrieval is whatever the configured LangGraph `BaseStore` implements. Examples
use semantic search with namespaces; search/manage tools expose that store to an
agent ([tools source](https://github.com/langchain-ai/langmem/blob/56d85939d80bb731bd5e237567148d817d7bfd16/src/langmem/knowledge/tools.py)).
Lexical fusion, provenance-aware ranking, and cross-memory-type budgets are host
responsibilities.

## Prompt Construction

The library optimizes one or multiple prompts from conversation trajectories.
The gradient optimizer explicitly avoids changing prompts without evidence of
failure and requests minimal edits
([`gradient.py`](https://github.com/langchain-ai/langmem/blob/56d85939d80bb731bd5e237567148d817d7bfd16/src/langmem/prompts/gradient.py)).
This is useful offline machinery, not a complete request-time prompt composer.

## Feedback and Evaluation

Trajectory feedback can drive prompt optimization. The repository contains
tests, but no business-grade Good/Bad/Fix review ledger or experiment registry.
Automatically accepting an optimized prompt without human review would be risky.

## Multi-Tenancy

Namespace templating makes isolation possible, and the sample auth hook prefixes
store namespaces with the authenticated identity
([`graphs/auth.py`](https://github.com/langchain-ai/langmem/blob/56d85939d80bb731bd5e237567148d817d7bfd16/src/langmem/graphs/auth.py)).
This is not equivalent to database row-level security or workspace RBAC.

## Business Workflow Support

Absent. Goals, lead stages, approvals, opt-outs, and campaign limits belong to
the host application.

## Deployment and Operations

The library is light when embedded, but durable background reflection typically
pulls in LangGraph storage/deployment. Provider and store choices can remain
portable. MIT permits direct library use.

## Strong Ideas

- Separate memory extraction from the response hot path.
- Use schemas and namespace templates instead of untyped memory strings.
- Deduplicate pending reflection work.
- Optimize prompts only from observed failures and keep edits minimal.

## Weaknesses

- No temporal truth model, provenance policy, tenant RLS, or business workflow.
- LLM-driven update/delete decisions need auditing and replay protection.
- Adopting the full LangGraph stack is unnecessary for the current MVP.

## Relevance to conversation-agent

The extraction schemas and background reflection pattern are directly useful
after the product has durable conversations and workers. The current
incremental style compiler should remain independent. LangMem should be piloted
behind a memory port, not made the core domain framework.

## Decision

- **Adapt next:** schema-driven extraction and offline reflection.
- **Use as reference:** prompt optimizer and namespace design.
- **Reject for now:** mandatory LangGraph orchestration.

## Evidence

- [Knowledge extraction implementation](https://github.com/langchain-ai/langmem/blob/56d85939d80bb731bd5e237567148d817d7bfd16/src/langmem/knowledge/extraction.py)
- [Reflection executor](https://github.com/langchain-ai/langmem/blob/56d85939d80bb731bd5e237567148d817d7bfd16/src/langmem/reflection.py)
- [Semantic memory and namespaces](https://github.com/langchain-ai/langmem/blob/56d85939d80bb731bd5e237567148d817d7bfd16/docs/docs/guides/extract_semantic_memories.md)
- [Episodic memory guide](https://github.com/langchain-ai/langmem/blob/56d85939d80bb731bd5e237567148d817d7bfd16/docs/docs/guides/extract_episodic_memories.md)
- [MIT license](https://github.com/langchain-ai/langmem/blob/56d85939d80bb731bd5e237567148d817d7bfd16/LICENSE)
