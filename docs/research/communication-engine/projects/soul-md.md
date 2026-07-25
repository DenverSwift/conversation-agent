# soul.md

## Snapshot

| Field | Value |
| --- | --- |
| Repository | [aaronjmars/soul.md](https://github.com/aaronjmars/soul.md) |
| Commit | [`940c178e8e78b2a006e2d1ed7f585abd7b07d4e8`](https://github.com/aaronjmars/soul.md/tree/940c178e8e78b2a006e2d1ed7f585abd7b07d4e8) |
| Researched | 2026-07-25 |
| License | MIT |
| Primary language | Markdown |
| Activity | Active; researched commit dated 2026-07-23 |
| Delivery | Self-hosted files/agent skill |
| Maturity | Early, specification-oriented |
| Purpose | Compile source writing or an interview into editable identity and voice files |

## Architecture

This is a file protocol rather than an application. `SOUL.md` owns worldview and
identity, `STYLE.md` owns expression, `MEMORY.md` is an append-only human-editable
session log, `examples/` carries positive and negative calibration, and `data/`
retains raw grounding. The runtime reading order and source priority are explicit
in [`SKILL.md`](https://github.com/aaronjmars/soul.md/blob/940c178e8e78b2a006e2d1ed7f585abd7b07d4e8/SKILL.md).
There is no database, queue, retrieval index, or background worker.

Data flow is: raw writing/interview -> manual or agent analysis -> `SOUL.md` and
`STYLE.md` -> curated examples -> prompt-time reading. The builder documents the
questions and review loop, not a deterministic compiler
([`BUILD.md`](https://github.com/aaronjmars/soul.md/blob/940c178e8e78b2a006e2d1ed7f585abd7b07d4e8/BUILD.md)).

## Identity and Style

The strongest idea is an explicit boundary between "who" and "how." The identity
template includes worldview, opinions, tensions, boundaries, and current focus
([`SOUL.template.md`](https://github.com/aaronjmars/soul.md/blob/940c178e8e78b2a006e2d1ed7f585abd7b07d4e8/SOUL.template.md));
the style template separately captures rhythm, vocabulary, platform differences,
reactions, rhetorical moves, and anti-patterns
([`STYLE.template.md`](https://github.com/aaronjmars/soul.md/blob/940c178e8e78b2a006e2d1ed7f585abd7b07d4e8/STYLE.template.md)).
It has no per-contact model, confidence, provenance record, or automatic rollback.

## Memory Model

`MEMORY.md` is a brief chronological log intended for manual pruning. It is not
factual, episodic, temporal, or relationship memory in the database sense
([template](https://github.com/aaronjmars/soul.md/blob/940c178e8e78b2a006e2d1ed7f585abd7b07d4e8/MEMORY.md)).

## Retrieval

Files are read in a prescribed order and raw sources are browsed on demand.
There is no lexical, vector, graph, or hybrid ranking and no context-budget
algorithm.

## Prompt Construction

Stable identity precedes style, memory, examples, and raw data. Good and bad
examples are first-class. The hierarchy is useful, but prompt assembly is
delegated to the host agent and is neither versioned nor inspectable as a plan.

## Feedback and Evaluation

Drafts are reviewed conversationally ("does this sound like you?"). Quality
checks are qualitative. There is no Good/Bad/Fix persistence, automated
evaluation, or protection against AI outputs being added as evidence.

## Multi-Tenancy

Absent. A directory represents one identity; access control and tenant
partitioning belong to the host.

## Business Workflow Support

Absent. Modes (chat, tweet, essay) are content modes, not sales/support workflow
states, policies, approvals, or audit events.

## Deployment and Operations

Operational burden is minimal, but every safety, consistency, and concurrency
property is external. MIT permits direct reuse with attribution.

## Strong Ideas

- Separate identity, style, memory, curated examples, and raw evidence.
- Keep the compiled representation editable and reviewable.
- Treat negative examples and boundaries as first-class inputs.
- Give stable material priority over raw bulk evidence.

## Weaknesses

- The runtime instruction to "never break character" and conceal AI status is
  unsuitable for truthful business communication.
- No provenance, confidence, tenant isolation, incremental compiler state, or
  conflict handling.
- Compilation is subjective and non-reproducible.

## Relevance to conversation-agent

Adapt the artifact boundaries and reviewability, not the embodiment policy.
`conversation-agent` already has stronger source hashing, provenance, and
incremental rebuilding; `SOUL.md` contributes a clearer product-facing split
between identity and style.

## Decision

- **Adapt now:** identity/style/raw-evidence separation and editable overrides.
- **Use as reference:** template fields and positive/negative examples.
- **Reject:** concealed impersonation and unqualified extrapolation.

## Evidence

- [Runtime hierarchy and source priority](https://github.com/aaronjmars/soul.md/blob/940c178e8e78b2a006e2d1ed7f585abd7b07d4e8/SKILL.md)
- [Builder workflow](https://github.com/aaronjmars/soul.md/blob/940c178e8e78b2a006e2d1ed7f585abd7b07d4e8/BUILD.md)
- [Identity template](https://github.com/aaronjmars/soul.md/blob/940c178e8e78b2a006e2d1ed7f585abd7b07d4e8/SOUL.template.md)
- [Style template](https://github.com/aaronjmars/soul.md/blob/940c178e8e78b2a006e2d1ed7f585abd7b07d4e8/STYLE.template.md)
- [MIT license](https://github.com/aaronjmars/soul.md/blob/940c178e8e78b2a006e2d1ed7f585abd7b07d4e8/LICENSE)
