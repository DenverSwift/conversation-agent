# Architecture Inputs From Technology Landscape

Access date: 2026-07-28.

This is not an architecture RFC. It is a research handoff document for future design work.

## Architectural Requirements

- Preserve local-first privacy for Telegram sessions, exports, feedback, SQLite, and style bundles.
- Keep style evidence provider-independent and provenance-auditable.
- Separate transport, memory, style compiler, prompt composer, behavior runtime, feedback, and evaluation.
- Support both current Telethon userbot experiments and possible Telegram Business transport later.
- Never treat AI-generated replies as Matvey-authored evidence.
- Support cancellation on manual reply and newer incoming messages.
- Model non-response, typing, delay, multi-bubble replies, reactions, and future media/voice as explicit policies.

## Verified Constraints

- Telegram Bot API bot accounts are not the same as personal account automation.
- Telegram Business connected bots are official but permission-gated.
- Telethon gives account-level access but requires sensitive session files and conservative automation.
- Memory products do not solve Telegram behavior policy.
- Sales/support products do not solve local Matvey style evidence.
- Fine-tuning-oriented clone projects are not aligned with the current AA.2 runtime adaptation path.

## Candidate Ready Components

- Transport: Telethon now, Telegram Business later.
- Memory: LangMem, Mem0, Letta, Graphiti/Zep, Supermemory.
- Relationship graph: Graphiti/Zep.
- Persona packaging: soul.md/SoulSpec and SillyTavern concepts.
- Sales/support workflow: respond.io patterns, ChatPlace/Chat Data/BotB2B setup lessons.

## Open Questions

- Should the project keep Telethon as the main transport or move production to Telegram Business?
- Which memory substrate best preserves provenance and local privacy?
- What is the minimal relationship model before graph memory is justified?
- How should outbound lead generation be constrained or excluded?
- What behavior metrics prove that replies feel human without becoming unsafe?

## Required ADRs

- Transport boundary: Telethon userbot vs Telegram Business connected bot.
- Memory substrate: local SQLite/vector store vs external memory service.
- Style evidence provenance schema.
- Human behavior runtime policy.
- Feedback taxonomy and evaluation loop.
- Multi-account tenancy boundary.

## Risks

- Account bans or abuse flags from aggressive userbot automation.
- Vendor lock-in if memory/retrieval is outsourced too early.
- Privacy leakage from hosted memory or support SaaS tools.
- Over-designing CRM before personal-style reliability is validated.
- Confusing marketing claims with verified human behavior simulation.

## Recommended First Vertical Slice

Build a narrow Telegram behavior runtime on top of the existing AA.2 style retrieval: collect incoming bursts, retrieve evidence, decide reply vs no-reply, simulate typing and delay, send one or more text bubbles, cancel on manual reply or newer incoming message, log metadata and provenance IDs, and evaluate against saved feedback.
