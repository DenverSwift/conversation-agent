# Methodology

## Scope and date

Research was performed on 2026-07-25. Every mandatory repository was cloned
outside the product worktree at a fixed commit. README and documentation claims
were checked against source structure, core models, prompt/retrieval code,
storage/migrations, tests, and licenses. Temporary clones are not tracked.

## Questions

Each report used the same sections and examined:

- component boundaries and orchestration;
- identity/style representation and provenance;
- memory types, mutation, conflict, age, and deletion;
- lexical, semantic, graph, hybrid, and reranking paths;
- prompt order, budgeting, inspection, and versioning;
- feedback, correction, evaluation, and self-training risk;
- tenant isolation and ownership;
- sales/support workflow, approvals, opt-out, and audit;
- deployment, dependency, license, and lock-in.

## Evidence standard

Technical conclusions link to a file at the researched SHA. Marketing claims
are labeled as such when implementation could not be established. `unclear`
means the reviewed sources did not establish the property; it does not mean the
property is impossible. Project activity is the date of the researched HEAD,
not a prediction of project health.

## Comparison scale

- `native`: explicit product/source capability;
- `partial`: implemented but incomplete for our requirement;
- `possible`: host can build it over available primitives;
- `absent`: no relevant implementation found;
- `unclear`: evidence insufficient.

## Limitations

- Hosted Zep, Mem0, and Supermemory internals were not penetration-tested.
- Issues/discussions were sampled only where source/docs exposed a material
  constraint; source code remained primary.
- Benchmarks were not reproduced on private conversation data.
- License identification is not legal advice.
- The active Letta architecture is transitioning away from the specified legacy
  repository; this is recorded rather than hidden.
