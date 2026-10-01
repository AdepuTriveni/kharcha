# ADR-010: Telegram first, through a thin Bot API client

- Status: Accepted
- Date: 2026-10-01

## Context
Kharcha needs a channel for summaries, nudges, cash entry and button feedback before the
Android app has full screens. Building push notifications and in-app chat first would delay
the "usable by me" milestone (Phase 1). The spec allows python-telegram-bot or aiogram.

## Decision
Telegram is the first delivery channel; FCM push follows. The notifier talks to the Bot API
through a small async httpx client (`kharcha_notifier.telegram`) behind a `Messenger`
protocol, with long polling locally and webhooks in production. The update offset is stored
in Redis. Every user-facing message still passes the notifier's policy gate.

## Alternatives considered
- **python-telegram-bot / aiogram**: complete frameworks, but they bring their own app
  lifecycle and handler model that fight our FastStream + asyncio service, and they are harder
  to fake in tests. We use five Bot API methods.
- **WhatsApp**: where most users are, but the Business API needs approval and costs money
  (kept as stretch goal F21).

## Consequences
- Easy to test: bot logic runs against a fake `Messenger`.
- We maintain the client ourselves (small surface). Rate limits and retries are our job.
- Users need Telegram installed; the app keeps the same features for those who do not.
