# Supermemory

## Snapshot

| Field | Value |
| --- | --- |
| Repository | [supermemoryai/supermemory](https://github.com/supermemoryai/supermemory) |
| Commit | [`80af8c904397786055735531df0e63337b6d6d82`](https://github.com/supermemoryai/supermemory/tree/80af8c904397786055735531df0e63337b6d6d82) |
| Researched | 2026-07-25 |
| License | MIT |
| Primary language | TypeScript |
| Activity | Very active; researched commit dated 2026-07-25 |
| Delivery | Hosted API, SDK middleware, and local/self-host option |
| Maturity | Active product and broad monorepo |
| Purpose | Unified memories, profiles, documents, connectors, and hybrid search |

## Architecture

The monorepo contains web/docs/MCP applications, framework middleware, SDKs,
benchmarks, and a local server surface. The public API centers on documents,
search, and profiles keyed by `containerTag`. SDK middleware retrieves a static
profile, dynamic profile, and query-relevant results, injects them into a model
request, and can asynchronously add the conversation back
([Python middleware](https://github.com/supermemoryai/supermemory/blob/80af8c904397786055735531df0e63337b6d6d82/packages/openai-sdk-python/src/supermemory_openai/middleware.py)).

The repository exposes much integration code, while some managed-engine
internals and operational guarantees are product surfaces rather than easily
auditable application code. Local mode is documented as an embedded graph and
API, but parity with managed service must be verified by deployment tests.

## Identity and Style

Profiles explicitly separate stable (`static`) and recent (`dynamic`) user
information
([profile docs](https://github.com/supermemoryai/supermemory/blob/80af8c904397786055735531df0e63337b6d6d82/apps/docs/concepts/user-profiles.mdx)).
This is factual/user context, not a communication identity or style-evidence
compiler. Per-contact voice and operator/company layering are absent.

## Memory Model

Conversations/documents are ingested; facts and profiles are derived; temporary
facts can expire and contradictions can supersede earlier information. The API
supports add, update, delete, forget, profiles, and graph views. `containerTag`
is the main ownership partition. Provenance detail exposed to callers is less
explicit than Graphiti's episode-edge model.

## Retrieval

Search supports memories/documents and hybrid mode. Profile calls can return
stable profile, dynamic profile, and ranked search results together
([search docs](https://github.com/supermemoryai/supermemory/blob/80af8c904397786055735531df0e63337b6d6d82/apps/docs/recall/search.mdx)).
SDK utilities deduplicate with static > dynamic > search priority
([utility](https://github.com/supermemoryai/supermemory/blob/80af8c904397786055735531df0e63337b6d6d82/packages/cartesia-sdk-python/src/supermemory_cartesia/utils.py)).

## Prompt Construction

Middleware adds retrieved context around an existing provider call. It does not
compose business goal, policy, style evidence, workflow state, approval mode,
or a versioned prompt plan.

## Feedback and Evaluation

MemoryBench and integration tests are notable. Benchmark claims in the README
should be treated as vendor-reported until reproduced on our data. There is no
native reply-level Good/Bad/Fix correction loop.

## Multi-Tenancy

`containerTag` supports per-user/entity isolation at the API layer. The source
review did not establish database RLS, workspace RBAC, data residency, or
cross-tenant penetration properties. These require a vendor/security review.

## Business Workflow Support

No lead/campaign/support state machine. Connectors and company knowledge help
retrieval, but compliance, opt-out, approval, and send deduplication remain ours.

## Deployment and Operations

Managed API offers fastest adoption but highest data/vendor dependency. Local
mode reduces dependency and is MIT, but operational parity, backups, upgrade
semantics, and tenancy need validation. The monorepo is large.

## Strong Ideas

- Return stable profile, dynamic profile, and relevant search in one contract.
- Deduplicate context by explicit source-tier priority.
- Provide evaluation tooling and a local/hosted choice.
- Keep memory middleware provider-independent.

## Weaknesses

- Marketing and managed behavior cannot all be confirmed from source.
- `containerTag` is not a complete tenant-security model.
- No style provenance or communication workflow.
- An all-in-one memory layer could obscure our ownership and deletion rules.

## Relevance to conversation-agent

The three-part profile/search response is worth adapting. Supermemory should be
benchmarked as an optional memory provider after a provider-neutral memory port,
deletion contract, provenance fields, and privacy review exist.

## Decision

- **Adapt next:** stable/dynamic/retrieved context tiers.
- **Pilot later:** managed or local provider behind an adapter.
- **Reject for now:** direct coupling of the communication core to its API.

## Evidence

- [Repository overview and local mode](https://github.com/supermemoryai/supermemory/blob/80af8c904397786055735531df0e63337b6d6d82/README.md)
- [User profile concept](https://github.com/supermemoryai/supermemory/blob/80af8c904397786055735531df0e63337b6d6d82/apps/docs/concepts/user-profiles.mdx)
- [Search API](https://github.com/supermemoryai/supermemory/blob/80af8c904397786055735531df0e63337b6d6d82/apps/docs/recall/search.mdx)
- [OpenAI middleware implementation](https://github.com/supermemoryai/supermemory/blob/80af8c904397786055735531df0e63337b6d6d82/packages/openai-sdk-python/src/supermemory_openai/middleware.py)
- [MIT license](https://github.com/supermemoryai/supermemory/blob/80af8c904397786055735531df0e63337b6d6d82/LICENSE)
