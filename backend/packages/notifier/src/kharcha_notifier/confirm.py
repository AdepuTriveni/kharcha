"""Send "Logged ₹150 · vada pav [Undo]" for cash entries that came from Telegram (§13)."""

import uuid

from sqlalchemy import select

from kharcha_common.cash import ledger_id_for
from kharcha_common.db.models import RawEventRow, User
from kharcha_common.events import CashEvent
from kharcha_common.idempotency import is_processed, mark_processed
from kharcha_notifier.bot import TELEGRAM_APP, BotDeps
from kharcha_notifier.telegram import Button
from kharcha_notifier.texts import cash_logged_text

CONFIRM_CONSUMER = "notifier.cash-confirm"


async def handle_cash_event(body: bytes, deps: BotDeps) -> None:
    event = CashEvent.model_validate_json(body)
    if event.causation_id is None:
        return
    async with deps.sessions() as session:
        if await is_processed(session, CONFIRM_CONSUMER, event.event_id):
            return
        row = (
            await session.execute(
                select(RawEventRow.source_app, User.telegram_chat_id)
                .join(User, User.id == RawEventRow.user_id)
                .where(
                    RawEventRow.event_id == uuid.UUID(event.causation_id),
                    RawEventRow.user_id == event.user_id,
                )
            )
        ).one_or_none()
    if row is not None and row.source_app == TELEGRAM_APP and row.telegram_chat_id is not None:
        p = event.payload
        await deps.messenger.send_message(
            row.telegram_chat_id,
            cash_logged_text(p.entry_type, p.amount_paise, p.note, p.category),
            buttons=[[Button("Undo", f"undo:{ledger_id_for(event.event_id)}")]],
        )
    # Marked after sending: a crash in between may resend once, which beats losing it.
    async with deps.sessions.begin() as session:
        await mark_processed(session, CONFIRM_CONSUMER, event.event_id)
