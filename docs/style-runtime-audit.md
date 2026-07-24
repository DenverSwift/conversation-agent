# Style Runtime Audit Before AA.1

This audit describes the code state immediately before AA.1 runtime style
adaptation.

## Confirmed causes

1. `.runtime/exports/cleaned_examples.jsonl` is not read during live
   generation. It is written only by `tools/export_training_data.py`.
2. None of the exported human examples are included in OpenAI requests.
3. There is no persistent Matvey style rulebook. `prompt_builder.py` reads only
   the `Matvey communication behavior` section from README at startup.
4. Trainer Fix corrections are stored in SQLite and exported, but are not read
   by `Responder` or the OpenAI client.
5. Recent outgoing messages, including known AI-generated messages, are loaded
   from the real Telegram dialog and passed with role `assistant`.
6. The history exporter can distinguish known AI messages through sent
   Telegram IDs in SQLite. The live context builder cannot: it distinguishes
   only `user` and `assistant`.
7. The exact persistent instructions are the six strings in
   `agent/prompt_builder.py`, followed by the README behavior section when
   present. The recent Telegram messages are sent separately as model input.
8. The README behavior asks for brief, direct, calm, conversational replies and
   a clarifying question when context is insufficient. No instruction requires
   emotional support, conflict de-escalation, profanity avoidance, universal
   politeness, or professional friendliness.
9. No response post-processing sanitizes profanity. The only post-processing
   is whitespace trimming and an empty-response check.
10. Trainer-bot messages cannot enter the real conversation context because
    Telethon history is read from the allowed contact's private peer, while the
    trainer uses a separate Bot API chat.

## Likely causes

- The small generic README behavior section provides much weaker style evidence
  than Matvey's real history.
- Known AI messages re-entering context without provenance can reinforce an
  earlier generic response as though it were Matvey's own message.
- Fix corrections cannot correct subsequent behavior because there is no
  runtime feedback retrieval.
- Base-model defaults can appear assistant-like when no retrieved human
  evidence supports a more specific response pattern.

These are code-supported hypotheses, not claims about an undocumented OpenAI
refusal or filtering behavior.

## Disproven causes

- The application does not explicitly ban profanity or insults.
- It does not contain a therapist, moderator, support-agent, or de-escalation
  instruction.
- It does not upload all 500 examples or truncate them before a live request;
  they are not loaded at all.
- Trainer-bot conversations are not mixed into the allowed contact's Telethon
  history.

## Required changes

- Compile the complete qualifying local corpus into a persistent reviewed
  behavior rulebook and example bank.
- Load the rulebook into every style-enabled request.
- Retrieve a small relevant set of real examples and immediate Fix corrections.
- classify recent messages as `contact`, `human_matvey`, or `ai_generated`.
- Keep AI-generated and rejected replies out of positive style evidence.
- Add contact-specific rules, manual overrides, prompt budgeting, safe
  inspection, and local evaluation.
- Preserve the AAA.3 request path when style adaptation is disabled.
