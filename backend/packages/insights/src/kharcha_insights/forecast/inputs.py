"""Build a :class:`ForecastInput` for one user from Postgres (PROJECT_SPEC §14).

- Bank balance: per account, the latest ``balance_after_paise`` plus the net of that account's
  later transactions (fresh if <= 3 days old, else "last known + net since"). No balance at all
  -> the caller asks the user once.
- Cash in hand from the wallet (never negative).
- Recurring items from the last 90 days; discretionary days from the last 60 days of
  non-recurring spends plus cash spends, zero-spend days included.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.db.models import CashLedgerRow, TransactionRow
from kharcha_common.events import CashEntryType, Direction, TxnKind, TxnStatus
from kharcha_common.merchants import normalize_merchant
from kharcha_common.time import IST, to_ist
from kharcha_common.wallet import cash_balance
from kharcha_insights.forecast.model import DEFAULT_HORIZON, DEFAULT_RUNS, DaySample, ForecastInput
from kharcha_insights.forecast.recurring import PastTxn, detect_recurring

RECURRING_LOOKBACK = timedelta(days=90)
SAMPLE_DAYS = 60
FRESH_BALANCE = timedelta(days=3)


@dataclass(frozen=True, slots=True)
class BankBalance:
    paise: int
    as_of: datetime
    fresh: bool


@dataclass(frozen=True, slots=True)
class BuiltInput:
    input: ForecastInput | None
    balance: BankBalance | None
    reason: str | None = None  # "NEEDS_BALANCE" when we have no bank balance at all


def _signed(row: TransactionRow) -> int:
    return row.amount_paise if row.direction == Direction.CREDIT.value else -row.amount_paise


def bank_balance(rows: list[TransactionRow], now: datetime) -> BankBalance | None:
    """``rows`` sorted by time. Sums the per-account balances."""
    by_account: dict[str, list[TransactionRow]] = defaultdict(list)
    for row in rows:
        by_account[row.account_hint or "?"].append(row)
    total, oldest, found = 0, now, False
    for account_rows in by_account.values():
        anchors = [r for r in account_rows if r.balance_after_paise is not None]
        if not anchors:
            continue
        anchor = anchors[-1]
        assert anchor.balance_after_paise is not None
        later = [r for r in account_rows if r.txn_time > anchor.txn_time]
        total += anchor.balance_after_paise + sum(_signed(r) for r in later)
        oldest = min(oldest, anchor.txn_time)
        found = True
    if not found:
        return None
    return BankBalance(total, oldest, fresh=now - oldest <= FRESH_BALANCE)


def _key(row: TransactionRow) -> str:
    if row.merchant_id:
        return row.merchant_id
    return normalize_merchant(row.merchant_raw) if row.merchant_raw else f"?{row.id}"


async def build_input(
    session: AsyncSession,
    user_id: str,
    now: datetime,
    *,
    bank_override_paise: int | None = None,
    runs: int = DEFAULT_RUNS,
    horizon_days: int = DEFAULT_HORIZON,
    seed: int = 0,
) -> BuiltInput:
    today = to_ist(now).date()
    since = now - RECURRING_LOOKBACK
    rows = list(
        (
            await session.scalars(
                select(TransactionRow)
                .where(
                    TransactionRow.user_id == user_id,
                    TransactionRow.status == TxnStatus.SUCCESS.value,
                    TransactionRow.txn_time <= now,
                )
                .order_by(TransactionRow.txn_time, TransactionRow.id)
            )
        ).all()
    )

    balance = bank_balance(rows, now)
    if bank_override_paise is not None:
        balance = BankBalance(bank_override_paise, now, fresh=True)
    if balance is None:
        return BuiltInput(None, None, reason="NEEDS_BALANCE")

    recent = [r for r in rows if r.txn_time >= since]
    past = [
        PastTxn(
            r.id,
            _key(r),
            r.merchant_raw or r.category,
            _signed(r),
            to_ist(r.txn_time).date(),
            r.category,
        )
        for r in recent
        if r.kind in {TxnKind.SPEND.value, TxnKind.INCOME.value}
    ]
    recurring, explained = detect_recurring(past)

    cash_rows = (
        await session.execute(
            select(
                CashLedgerRow.occurred_at, CashLedgerRow.amount_paise, CashLedgerRow.category
            ).where(
                CashLedgerRow.user_id == user_id,
                CashLedgerRow.entry_type == CashEntryType.CASH_SPEND.value,
                CashLedgerRow.occurred_at >= now - timedelta(days=SAMPLE_DAYS),
            )
        )
    ).all()

    spend_by_day: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in recent:
        if (
            r.kind == TxnKind.SPEND.value
            and r.direction == Direction.DEBIT.value
            and r.id not in explained
        ):
            spend_by_day[to_ist(r.txn_time).date()][r.category] += r.amount_paise
    for occurred_at, amount, category in cash_rows:
        spend_by_day[to_ist(occurred_at).date()][category or "CASH_UNCATEGORIZED"] += int(amount)

    first_seen = min((to_ist(r.txn_time).date() for r in rows), default=today)
    start = max(today - timedelta(days=SAMPLE_DAYS), first_seen)
    samples = [
        DaySample(day.weekday(), dict(spend_by_day.get(day, {})))
        for day in (start + timedelta(days=i) for i in range((today - start).days))
    ]

    cash = max(0, (await cash_balance(session, user_id)).balance_paise)
    return BuiltInput(
        ForecastInput(
            today=today,
            bank_paise=balance.paise,
            cash_paise=cash,
            samples=samples,
            recurring=recurring,
            horizon_days=horizon_days,
            runs=runs,
            seed=seed,
        ),
        balance,
    )


def ist_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=IST)
