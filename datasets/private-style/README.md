# Private Style Dataset

This directory is a local-only staging area for confirmed human writing
examples. The repository tracks only this documentation, the JSON Schema, and
empty directory markers.

Positive targets must come from `human_manual`, `human_edit`, `human_fix`, or
`imported_human_verified`. Raw model output, accepted unchanged AI drafts,
benchmark answers, and synthetic targets are never human style evidence.

Before building a dataset, every example needs provenance, conversation
context, style evidence, privacy review, and explicit approval. Credential
patterns are rejected before an artifact is written. PII review flags are
preserved in manifests and are not silently removed.

The Stage 2 benchmark is evaluation-only and must never be copied here.
