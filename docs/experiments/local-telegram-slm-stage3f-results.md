# Stage 3F Profile Audit And Offline Replay Results

## Identity

- Date: 2026-07-31
- Branch: `experiment/local-telegram-slm`
- Starting commit: `d98ed3d330c7b9a3635d7aea1bfa09e09f4cd362`
- Dataset fingerprint:
  `013eb7a9df30bb3dfe3f4884c287616742245cf0ed2eddfda0a8d5ec3a019de4`
- Profile schema: 2
- Status: `PROFILE_AUDIT_PASSED`,
  `INSUFFICIENT_RELATIONSHIP_DIVERSITY`, `TIMING_DATA_UNAVAILABLE`

## Evidence

- Verified human episodes: 71
- Owner message bubbles: 73
- Relationships: 1
- Contacts: 1
- AI examples: 0
- Privacy findings: 0
- Fixed rules: 0

The confirmed dataset was not changed. Profile and replay artifacts remained
private and Git ignored.

## Feature Audit

The v1 `uppercase_frequency` measured only whether the first alphabetic
character was uppercase. Schema v2 produced this actual casing distribution:

| Category | Count | Frequency |
| --- | ---: | ---: |
| Normal sentence case | 55 | 0.753425 |
| Lowercase | 10 | 0.136986 |
| ALL CAPS | 6 | 0.082192 |
| Mixed case | 1 | 0.013699 |
| Uncased | 1 | 0.013699 |

Incoming context is now extracted from contact turns:

- incoming character median: 38;
- incoming character p90: 101;
- incoming question frequency: 0.084507;
- incoming short frequency at the recorded 25-character threshold: 0.366197;
- incoming long frequency at the recorded 280-character threshold: 0.028169;
- owner question frequency: 0.098592.

Typo frequency is `null` and unsupported because no authoritative typo labels
or reliable deterministic detector are available. The limited profanity
detector matched 6 tokens in 6 owner bubbles and records its lexicon version
and coverage; this evidence remains relationship-specific.

Response delays are unavailable for all 71 episodes. Two inter-bubble
intervals are also unavailable. No missing timestamp was converted to zero.

## Confidence

- Previous undifferentiated confidence: 0.926664
- Global agent confidence: 0.389554
- Relationship confidence: 0.913087
- Single-relationship bias: true

The global score is reduced by the configured single-relationship multiplier.
The relationship score remains high for directly measured surface features.
Timing and typo feature confidence are zero because those features are
unavailable, not because a zero preference was observed.

## Five-Fold Replay

Every metric below is computed over 71 held-out episodes. Targets were not
provided to the resolver, and episode bubbles were never split between train
and evaluation.

| Mode | Casing acc. | Bubble exact | Length band acc. | Length MAE | Punctuation acc. | Short acc. | Fallback | Avg. feature confidence |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Neutral fallback | 0.746479 | 0.971831 | 0.183099 | 62.507042 | 0.098592 | 0.239437 | 1.0 | 0.0 |
| Agent only | 0.746479 | 0.971831 | 0.760563 | 12.591549 | 0.901408 | 0.760563 | 0.0 | 0.171318 |
| Relationship only | 0.746479 | 0.971831 | 0.760563 | 12.591549 | 0.901408 | 0.760563 | 0.0 | 0.601422 |
| Agent + relationship | 0.746479 | 0.971831 | 0.760563 | 12.591549 | 0.901408 | 0.760563 | 0.0 | 0.601422 |
| Agent + relationship + snapshot | 0.746479 | 0.971831 | 0.760563 | 12.591549 | 0.901408 | 0.760563 | 0.0 | 0.601422 |

Question-decision accuracy was 0.901408, emoji-presence accuracy was 0.985915,
and limited profanity-tendency accuracy was 0.915493 in every mode. Sentence
completeness band accuracy was 0.535211 for profile modes and 0.464789 for
neutral fallback.

The observed conversation snapshot is not applied as a style override because
there is no validated context-conditioned feature model. This avoids turning
a few local messages into a fixed generation rule.

## Temporal Holdout

Explicit episode timestamps supported an early-80/late-20 split:

- train episodes: 56;
- evaluation episodes: 15;
- JSONL order used as time: false.

For agent + relationship, temporal casing accuracy was 0.666667, bubble exact
match 1.0, length-band accuracy 0.8, length MAE 16.466667, punctuation
accuracy 0.866667, short-response accuracy 0.8, and fallback rate 0.0.
Neutral fallback had length-band accuracy 0.066667, length MAE 59.133333,
punctuation accuracy 0.133333, short-response accuracy 0.2, and fallback
rate 1.0.

## Safety

- OpenAI called: false
- Local LLM called: false
- Embeddings called: false
- Training started: false
- Synthetic targets created: false
- Production generation default changed: false
- Confirmed dataset changed: false

The next recommended step is to collect verified examples from additional
relationships before promoting relationship-local lexical or profanity
features into global agent evidence. LoRA or QLoRA readiness is not implied by
this audit.
