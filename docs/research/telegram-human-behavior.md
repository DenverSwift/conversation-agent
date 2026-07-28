# Telegram Human Behavior Research

Access date: 2026-07-28.

## Confirmed Platform Paths

The Bot API is the stable HTTP interface for bot accounts. It is suitable for customer support bots and Telegram Business connected bots, but a bot account is not the same as controlling Matvey's personal account directly.

Telegram Business and Secretary Mode let users connect bots that process and answer messages on their behalf. Official docs describe business connection updates, business messages, deleted/edited business messages, and reply permissions.

Telethon is an asyncio MTProto client that can sign in with a user's API ID/hash and phone-backed session. This gives account-level access that matches the current MVP, but session safety, account limits, and behavior throttling are operational risks.

## Required Runtime Not Supplied By The Market

- batching incoming message bursts before replying;
- cancelling a draft after a newer incoming message;
- cancelling a draft after manual human reply;
- typing indicators with natural delays;
- splitting replies into multiple Telegram bubbles;
- choosing reactions instead of text;
- deciding that no reply is better;
- preserving evidence provenance for each style decision.

## Viability Notes

Telegram Business is safer for customer-facing business inboxes. Telethon remains the fastest path for local experiments, but should stay conservative: one account, low volume, private allowlist, no spam/outbound automation, and explicit human handoff.
