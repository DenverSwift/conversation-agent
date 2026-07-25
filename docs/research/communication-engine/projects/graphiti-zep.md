# Graphiti / Zep

## Snapshot

| Field | Value |
| --- | --- |
| Repository | [getzep/graphiti](https://github.com/getzep/graphiti) |
| Commit | [`3bb2d0bba56f8e22311574c045452c420a012f49`](https://github.com/getzep/graphiti/tree/3bb2d0bba56f8e22311574c045452c420a012f49) |
| Researched | 2026-07-25 |
| License | Apache-2.0 |
| Primary language | Python |
| Activity | Very active; researched commit dated 2026-07-23 |
| Delivery | OSS engine; Zep is managed context-graph infrastructure |
| Maturity | Mature specialized framework |
| Purpose | Ingest episodes into a temporally aware context graph and retrieve facts |

## Architecture

Graphiti separates graph drivers, LLM extraction, embeddings, cross-encoder
reranking, node/edge models, search recipes, prompts, and maintenance utilities.
The `Graphiti` facade orchestrates episode ingestion, entity/edge extraction,
resolution, deduplication, temporal invalidation, and persistence
([`graphiti.py`](https://github.com/getzep/graphiti/blob/3bb2d0bba56f8e22311574c045452c420a012f49/graphiti_core/graphiti.py)).
Drivers support Neo4j, FalkorDB, Neptune, and deprecated Kuzu.

Data flow: episode -> extracted entities/relationships -> resolve/dedupe ->
persist episodic provenance and facts -> hybrid search -> rerank. This is not a
message runtime or business workflow engine.

## Identity and Style

No persona/style compiler. Entities and typed edges can represent operators,
contacts, companies, and relationships, but voice rules and examples require a
separate subsystem.

## Memory Model

`EpisodicNode` stores raw episode content and `valid_at`; `EntityNode` represents
resolved objects. `EntityEdge` stores facts with `valid_at`, `invalid_at`, and
`expired_at`
([nodes](https://github.com/getzep/graphiti/blob/3bb2d0bba56f8e22311574c045452c420a012f49/graphiti_core/nodes.py);
[edges](https://github.com/getzep/graphiti/blob/3bb2d0bba56f8e22311574c045452c420a012f49/graphiti_core/edges.py)).
Every graph object has `group_id` partitioning. Episode links preserve
provenance. This is the strongest temporal/relationship model in the set.

## Retrieval

Search can combine full-text, embeddings, and graph traversal. Recipes provide
RRF, MMR, cross-encoder, episode-mention, and node-distance rerankers
([recipes](https://github.com/getzep/graphiti/blob/3bb2d0bba56f8e22311574c045452c420a012f49/graphiti_core/search/search_config_recipes.py)).
Limits and group filters constrain results. It is powerful but costlier than
PostgreSQL hybrid retrieval for early product stages.

## Prompt Construction

Graphiti owns extraction/deduplication prompts, not reply prompts. Structured
output quality is a hard dependency and smaller models may fail, as the
[README](https://github.com/getzep/graphiti/blob/3bb2d0bba56f8e22311574c045452c420a012f49/README.md)
notes.

## Feedback and Evaluation

Tests cover ingestion/search behavior. There is no Good/Bad/Fix reply loop or
human approval workflow. Corrections could be added as new episodes but require
application semantics.

## Multi-Tenancy

`group_id` provides graph partitioning and is applied across nodes/edges/search.
It is logical partitioning, not a complete tenant/RBAC/compliance layer.
Zep's managed product advertises broader governance; that cloud behavior was not
verified from Graphiti source.

## Business Workflow Support

Relationship histories are relevant to customers, but lead stages, campaign
actions, opt-outs, approvals, and channel operations are absent.

## Deployment and Operations

Self-hosting requires a supported graph database plus LLM and embedding
providers; some backends need a full-text service. Ingestion performs multiple
LLM operations and has rate-limit/concurrency controls. Apache-2.0 permits reuse.

## Strong Ideas

- Preserve raw episodes as provenance for derived facts.
- Model fact validity separately from ingestion time.
- Resolve contradictions by invalidating facts, not deleting history.
- Combine lexical, semantic, and graph signals with explicit rerank recipes.

## Weaknesses

- Significant database and extraction complexity.
- `group_id` is not sufficient tenant security.
- No style, message, approval, or business workflow domain.
- Premature for hundreds—not millions—of lead conversations.

## Relevance to conversation-agent

Adopt the temporal vocabulary and provenance model in relational tables. Do not
introduce a graph database in v1. Re-evaluate Graphiti only when evaluated
multi-hop relationship questions cannot be served by PostgreSQL.

## Decision

- **Adapt now:** temporal fact fields and episode provenance.
- **Use as reference:** hybrid/rerank recipes.
- **Revisit later:** Graphiti adapter after a measured graph-retrieval need.
- **Reject for now:** graph database as mandatory core storage.

## Evidence

- [Graphiti facade and ingestion](https://github.com/getzep/graphiti/blob/3bb2d0bba56f8e22311574c045452c420a012f49/graphiti_core/graphiti.py)
- [Node model](https://github.com/getzep/graphiti/blob/3bb2d0bba56f8e22311574c045452c420a012f49/graphiti_core/nodes.py)
- [Temporal edge model](https://github.com/getzep/graphiti/blob/3bb2d0bba56f8e22311574c045452c420a012f49/graphiti_core/edges.py)
- [Hybrid search recipes](https://github.com/getzep/graphiti/blob/3bb2d0bba56f8e22311574c045452c420a012f49/graphiti_core/search/search_config_recipes.py)
- [Apache-2.0 license](https://github.com/getzep/graphiti/blob/3bb2d0bba56f8e22311574c045452c420a012f49/LICENSE)
