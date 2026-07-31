# Stage 3B Private Telegram Import Results

## Identity

- Date: 2026-07-31
- Branch: `experiment/local-telegram-slm`
- Importer implementation commit: `091ca249ed9fd0861e6a19fd29485997bdaa6371`
- Importer status: real preview verified
- Telethon status: existing session authorized
- Resolved contact: strict numeric match verified
- User review: pending

## Privacy Boundary

The importer reused the configured Telethon session in read-only mode. Contact
resolution required an exact numeric user match and exposed only masked entity
details. No message, reaction, read acknowledgement, media download, OpenAI
request, local-model request, embedding request, telemetry upload, training
run, or benchmark import was performed by the importer.

Raw history, message identifiers, names, and review artifacts remain under an
ignored runtime path shaped like:

```text
.runtime/private-imports/telegram/<pilot>/
```

Git contains no contact identity, raw message, private Telegram message ID, or
private preview artifact from this run.

## Preview

The local pilot preview records:

- aggregate incoming and outgoing counts;
- provenance and exclusion counts;
- turn and bubble segmentation;
- deterministic PII and secret findings;
- pseudonymized candidate episodes;
- a diverse review sample;
- separate agent and relationship style previews;
- empty review decisions;
- a content fingerprint.

Exact local aggregates and the full fingerprint remain in the ignored
`summary.md` and `manifest.json` inside the preview directory. They are not
copied into this Git document to avoid turning private operational metadata
into repository history.

Allowed aggregate result:

- fetched messages: 1,500;
- candidate episodes: 200;
- excluded records: 552;
- privacy findings: 24;
- verified-human profile sample count: 0;
- preview fingerprint prefix: `541d486a4d9f`;
- approved review decisions: 0.

## Decision

Technical status: `READY_FOR_PRIVATE_REVIEW`.

No example has been confirmed as training data. The operator must inspect the
review sample, explicitly fill include/privacy/provenance decisions, rerun
review statistics, and separately choose whether to execute the fingerprinted
confirmation command. Stage 3B itself does not execute confirmation or
training.
