"""Telegram update handling (PROJECT_SPEC §25): linking, commands, cash entry, Undo.

Message text is never logged. Free text that looks like a cash entry is published to
``raw-events`` as MANUAL_TEXT; the processor parses it and the confirmation (with Undo)
is sent when the resulting ``cash-events`` message arrives (see :mod:`kharcha_notifier.confirm`).
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kharcha_common import wallet
from kharcha_common.cash_parser import parse_cash_entry
from kharcha_common.categories import Category
from kharcha_common.db.models import Budget, RawEventRow, User
from kharcha_common.events import RawEvent, RawEventPayload, RawEventType
from kharcha_common.kafka import EventPublisher, derived_event_id
from kharcha_common.linking import redeem_link_code
from kharcha_common.money import MoneyError, format_inr, rupees_to_paise
from kharcha_common.time import to_ist, utcnow
from kharcha_common.topics import Topic
from kharcha_notifier import queries, texts
from kharcha_notifier.policy import RoastLevel
from kharcha_notifier.telegram import Messenger

log = logging.getLogger(__name__)

PRODUCER = "notifier"
TELEGRAM_APP = "telegram"
UNDO_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class BotDeps:
    sessions: async_sessionmaker[AsyncSession]
    publisher: EventPublisher
    redis: Redis
    messenger: Messenger


async def handle_update(update: dict[str, Any], deps: BotDeps) -> None:
    if (callback := update.get("callback_query")) is not None:
        await _handle_callback(callback, deps)
        return
    message = update.get("message") or {}
    text = message.get("text")
    chat_id = (message.get("chat") or {}).get("id")
    if not isinstance(text, str) or not isinstance(chat_id, int):
        return
    text = text.strip()

    if text.startswith("/start"):
        await _link(chat_id, text.removeprefix("/start").strip(), deps)
        return

    async with deps.sessions() as session:
        user = await queries.user_by_chat(session, chat_id)
    if user is None:
        await deps.messenger.send_message(chat_id, texts.LINK_FIRST)
        return

    command, _, arg = text.partition(" ")
    match command.split("@")[0].lower():
        case "/help":
            await deps.messenger.send_message(chat_id, texts.HELP)
        case "/summary":
            async with deps.sessions() as session:
                summary = await queries.day_summary(session, user.id, to_ist(utcnow()).date())
            await deps.messenger.send_message(chat_id, texts.summary_text(summary))
        case "/cash":
            async with deps.sessions() as session:
                balance = await wallet.cash_balance(session, user.id)
            await deps.messenger.send_message(chat_id, balance.describe())
        case "/undo":
            await _undo_latest(chat_id, user.id, deps)
        case "/level":
            await _set_level(chat_id, user.id, arg, deps)
        case "/budget":
            await _set_budget(chat_id, user.id, arg, deps)
        case _ if text.startswith("/"):
            await deps.messenger.send_message(chat_id, texts.HELP)
        case _:
            await _free_text(chat_id, message, user.id, text, deps)


async def _link(chat_id: int, code: str, deps: BotDeps) -> None:
    user_id = await redeem_link_code(deps.redis, code)
    if user_id is None:
        await deps.messenger.send_message(
            chat_id, "That code is invalid or expired. " + texts.LINK_FIRST
        )
        return
    async with deps.sessions.begin() as session:
        # One chat belongs to one user: unlink it anywhere else first.
        await session.execute(
            update(User).where(User.telegram_chat_id == chat_id).values(telegram_chat_id=None)
        )
        await session.execute(
            update(User).where(User.id == user_id).values(telegram_chat_id=chat_id)
        )
    log.info("telegram linked", extra={"user": user_id})
    await deps.messenger.send_message(chat_id, "🔗 Linked! " + texts.HELP)


async def _free_text(
    chat_id: int, message: dict[str, Any], user_id: str, text: str, deps: BotDeps
) -> None:
    if parse_cash_entry(text) is None:
        reply = (
            "Ask Kharcha (questions about your money) arrives soon. " if "?" in text else ""
        ) + texts.HELP
        await deps.messenger.send_message(chat_id, reply)
        return
    now = utcnow()
    event = RawEvent(
        event_id=derived_event_id("telegram", str(chat_id), str(message.get("message_id"))),
        user_id=user_id,
        type=RawEventType.MANUAL_TEXT.value,
        occurred_at=now,
        producer=PRODUCER,
        payload=RawEventPayload(
            source_app=TELEGRAM_APP,
            text=text[:2000],
            posted_at=now,
            device_id=f"telegram:{chat_id}",
            redacted=True,  # typed by the user; contains no bank data
        ),
    )
    async with deps.sessions.begin() as session:
        await session.execute(
            insert(RawEventRow)
            .values(
                event_id=uuid.UUID(event.event_id),
                user_id=user_id,
                type=event.type,
                source_app=TELEGRAM_APP,
                text=event.payload.text,
                posted_at=now,
            )
            .on_conflict_do_nothing()
        )
    await deps.publisher.publish_event(Topic.RAW_EVENTS, event)


async def _undo_latest(chat_id: int, user_id: str, deps: BotDeps) -> None:
    async with deps.sessions.begin() as session:
        ledger_id = await queries.latest_cash_entry_id(session, user_id, utcnow() - UNDO_WINDOW)
        removed = ledger_id is not None and await wallet.delete_cash_entry(
            session, user_id, ledger_id
        )
    await deps.messenger.send_message(
        chat_id, "↩️ Removed your last cash entry." if removed else "Nothing to undo."
    )


async def _handle_callback(callback: dict[str, Any], deps: BotDeps) -> None:
    data = str(callback.get("data") or "")
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    callback_id = str(callback.get("id"))
    if not isinstance(chat_id, int):
        return
    async with deps.sessions.begin() as session:
        user = await queries.user_by_chat(session, chat_id)
        removed = False
        if user is not None and data.startswith("undo:"):
            removed = await wallet.delete_cash_entry(session, user.id, data.removeprefix("undo:"))
    await deps.messenger.answer_callback(callback_id, "Undone" if removed else "Already gone")
    if removed and isinstance(message.get("message_id"), int):
        await deps.messenger.edit_message_text(
            chat_id, message["message_id"], "↩️ Undone: " + str(message.get("text", ""))
        )


async def _set_level(chat_id: int, user_id: str, arg: str, deps: BotDeps) -> None:
    try:
        level = RoastLevel(arg.strip().upper())
    except ValueError:
        await deps.messenger.send_message(
            chat_id, "Use /level mild, /level medium, /level savage or /level off"
        )
        return
    async with deps.sessions.begin() as session:
        await session.execute(
            update(User).where(User.id == user_id).values(roast_level=level.value)
        )
    await deps.messenger.send_message(chat_id, f"Roast level set to {level.value.lower()}.")


async def _set_budget(chat_id: int, user_id: str, arg: str, deps: BotDeps) -> None:
    parts = arg.split()
    try:
        category = Category(parts[0].upper())
        limit = rupees_to_paise(parts[1])
    except (IndexError, ValueError, MoneyError):
        names = ", ".join(c.value.lower() for c in Category)
        await deps.messenger.send_message(
            chat_id, f"Use /budget <category> <amount>. Categories: {names}"
        )
        return
    if limit <= 0:
        await deps.messenger.send_message(chat_id, "Budget must be more than zero.")
        return
    async with deps.sessions.begin() as session:
        await session.execute(
            insert(Budget)
            .values(user_id=user_id, category=category.value, monthly_limit_paise=limit)
            .on_conflict_do_update(
                index_elements=[Budget.user_id, Budget.category],
                set_={"monthly_limit_paise": limit},
            )
        )
    await deps.messenger.send_message(
        chat_id, f"Budget for {category.value.lower()} set to {format_inr(limit)} a month."
    )
