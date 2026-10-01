"""Cash wallet queries shared by the notifier and the API (PROJECT_SPEC §13). Scoped by user."""

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.cash import CashBalance, balance_from_totals, ledger_id_for
from kharcha_common.db.models import CashLedgerRow
from kharcha_common.kafka import derived_event_id


async def cash_balance(session: AsyncSession, user_id: str) -> CashBalance:
    rows = await session.execute(
        select(CashLedgerRow.entry_type, func.sum(CashLedgerRow.amount_paise))
        .where(CashLedgerRow.user_id == user_id)
        .group_by(CashLedgerRow.entry_type)
    )
    return balance_from_totals({entry: int(total) for entry, total in rows.all()})


async def delete_cash_entry(session: AsyncSession, user_id: str, ledger_id: str) -> bool:
    result = await session.execute(
        delete(CashLedgerRow)
        .where(CashLedgerRow.id == ledger_id, CashLedgerRow.user_id == user_id)
        .returning(CashLedgerRow.id)
    )
    return result.first() is not None


def ledger_ids_for_entry(entry_id: str) -> list[str]:
    """Ledger ids an app-side entry id can refer to.

    ``entry_id`` is either the cash event id (``POST /v1/cash``) or the raw event id of a
    MANUAL_TEXT / WIDGET_TAP upload, which the processor turns into ``derived("cash", id)``.
    """
    return [ledger_id_for(entry_id), ledger_id_for(derived_event_id("cash", entry_id))]
