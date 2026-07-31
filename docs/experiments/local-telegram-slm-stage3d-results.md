# Stage 3D Authoritative Human Pilot Results

## Identity

- Date: 2026-07-31
- Branch: `experiment/local-telegram-slm`
- Starting commit: `9ba872d0759ba47f8231bf2685bdefd8056eca61`
- Status: `READY_FOR_PII_REVIEW`
- User review: pending

## Pilot

- Authoritative human episodes available: 83
- Pilot episodes selected: 82
- Confirmed AI episodes excluded: 22
- Unknown episodes excluded: 95
- Conflicting episodes included: 0
- Synthetic examples created: 0
- Human text normalized: false

The selected examples cover one-word, short, multi-bubble, lowercase,
normally cased, emoji, question and non-question, technical, casual, joking,
emotional, profane or slang, serious, call-coordination, and topic-shift
categories. Observed response length spans 1-100 characters and bubble counts
span 1-2.

## Privacy

- Total PII findings scoped: 24
- PII affecting authoritative human data: 1
- PII affecting the selected pilot: 0
- PII attached to confirmed AI data: 1
- PII attached to skipped unknown data: 2
- PII attached to excluded records: 20
- Authoritative PII decisions pending: 1
- Pilot scan findings after exclusion: 0

Recommendations were generated locally and none were approved or applied.
The one affected authoritative episode was excluded before pilot selection.
PII in AI, unknown, and otherwise excluded records did not block the pilot.

## Verification

- Pilot selection fingerprint prefix: `79b9b5ecfd14`
- Stage 3C reconciliation artifacts unchanged: true
- Private runtime artifacts Git ignored: true
- Future confirmed dataset path Git ignored: true
- Curated confirmation executed: false
- Private style profile built: false
- OpenAI called: false
- Local LLM called: false
- Training started: false

The technical result is ready for the user to review and approve the single
authoritative PII decision. No private message, name, username, contact ID, or
private runtime identifier is included in this document.
