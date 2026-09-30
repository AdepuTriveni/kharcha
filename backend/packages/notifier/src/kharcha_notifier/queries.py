"""Read queries used by the notifier. Every query is scoped by ``user_id`` (CLAUDE.md rule 7)."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.cash import CashBalance, balance_from_totals
from kharcha_common.db.models import AlertSent, CashLedgerRow, TransactionRow, User
from kharcha_common.events import CashEntryType, TxnKind, TxnStatus
from kharcha_common.time import IST, to_ist
from kharcha_notifier.policy import AlertKind, Counts


def ist_day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=IST)
    return start, start + timedelta(days=1)


async def user_by_chat(session: AsyncSession, chat_id: int) -> User | None:
    return (
        await session.execute(select(User).where(User.telegram_chat_id == chat_id))
    ).scalar_one_or_none()


async def cash_balance(session: AsyncSession, user_id: str) -> CashBalance:
    rows = await session.execute(
        select(CashLedgerRow.entry_type, func.sum(CashLedgerRow.amount_paise))
        .where(CashLedgerRow.user_id == user_id)
        .group_by(CashLedgerRow.entry_type)
    )
    return balance_from_totals({entry: int(total) for entry, total in rows.all()})


@dataclass(frozen=True, slots=True)
class DaySummary:
    day: date
    spent_paise: int
    payments: int
    cash_spent_paise: int
    top: list[tuple[str, int]]
    cash: CashBalance


async def day_summary(session: AsyncSession, user_id: str, day: date) -> DaySummary:
    start, end = ist_day_bounds(day)
    spend = (
        select(TransactionRow)
        .where(
            TransactionRow.user_id == user_id,
            TransactionRow.kind == TxnKind.SPEND.value,
            TransactionRow.status == TxnStatus.SUCCESS.value,
            TransactionRow.txn_time >= start,
            TransactionRow.txn_time < end,
        )
        .subquery()
    )
    total, count = (
        await session.execute(
            select(func.coalesce(func.sum(spend.c.amount_paise), 0), func.count())
        )
    ).one()
    merchant = func.coalesce(spend.c.merchant_raw, "unknown")
    top_rows = await session.execute(
        select(merchant, func.sum(spend.c.amount_paise))
        .group_by(merchant)
        .order_by(func.sum(spend.c.amount_paise).desc())
        .limit(3)
    )
    cash_spent = (
        await session.execute(
            select(func.coalesce(func.sum(CashLedgerRow.amount_paise), 0)).where(
                CashLedgerRow.user_id == user_id,
                CashLedgerRow.entry_type == CashEntryType.CASH_SPEND.value,
                CashLedgerRow.occurred_at >= start,
                CashLedgerRow.occurred_at < end,
            )
        )
    ).scalar_one()
    return DaySummary(
        day=day,
        spent_paise=int(total),
        payments=int(count),
        cash_spent_paise=int(cash_spent),
        top=[(str(name), int(amount)) for name, amount in top_rows.all()],
        cash=await cash_balance(session, user_id),
    )


async def alert_counts(session: AsyncSession, user_id: str, now: datetime) -> Counts:
    today = to_ist(now).date()
    day_start, _ = ist_day_bounds(today)
    week_start, _ = ist_day_bounds(today - timedelta(days=today.weekday()))
    sent = select(AlertSent.alert_type, AlertSent.sent_at).where(
        AlertSent.user_id == user_id, AlertSent.status == "SENT", AlertSent.sent_at >= week_start
    )
    rows = [(kind, at) for kind, at in (await session.execute(sent)).all() if at is not None]
    non_urgent = {AlertKind.ROAST.value, AlertKind.NUDGE.value}
    kinds_today = [kind for kind, at in rows if at >= day_start]
    return Counts(
        roasts_today=kinds_today.count(AlertKind.ROAST.value),
        roasts_week=sum(kind == AlertKind.ROAST.value for kind, _ in rows),
        non_urgent_today=sum(kind in non_urgent for kind in kinds_today),
    )


async def delete_cash_entry(session: AsyncSession, user_id: str, ledger_id: str) -> bool:
    result = await session.execute(
        delete(CashLedgerRow)
        .where(CashLedgerRow.id == ledger_id, CashLedgerRow.user_id == user_id)
        .returning(CashLedgerRow.id)
    )
    return result.first() is not None


async def latest_cash_entry_id(session: AsyncSession, user_id: str, since: datetime) -> str | None:
    return (
        await session.execute(
            select(CashLedgerRow.id)
            .where(CashLedgerRow.user_id == user_id, CashLedgerRow.occurred_at >= since)
            .order_by(CashLedgerRow.occurred_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
