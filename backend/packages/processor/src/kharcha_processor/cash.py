"""processor.cash: ``cash-events`` -> ``cash_ledger`` (PROJECT_SPEC §13)."""

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.cash import ledger_id_for
from kharcha_common.db.models import CashLedgerRow
from kharcha_common.events import CashEvent


async def write_cash_entry(session: AsyncSession, event: CashEvent) -> None:
    p = event.payload
    await session.execute(
        insert(CashLedgerRow)
        .values(
            id=ledger_id_for(event.event_id),
            user_id=event.user_id,
            entry_type=p.entry_type.value,
            amount_paise=p.amount_paise,
            category=p.category,
            note=p.note,
            related_transaction_id=p.related_transaction_id,
            occurred_at=event.occurred_at,
        )
        .on_conflict_do_nothing()
    )
