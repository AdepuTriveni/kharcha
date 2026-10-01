"""mcp-finance tools (PROJECT_SPEC §17.3). Read-only and scoped to the run's user.

Units: every ``*Paise`` field is an integer in paise; ``*Text`` fields are rupees already
formatted (₹1,23,456.78). Account numbers never appear (only categories and merchants).
"""

from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.categories import Category
from kharcha_common.db.models import Budget, CashLedgerRow, Merchant, TransactionRow
from kharcha_common.events import CashEntryType, Direction, TxnKind, TxnStatus
from kharcha_common.money import format_inr
from kharcha_common.time import IST, to_ist
from kharcha_common.wallet import cash_balance
from kharcha_insights.forecast.inputs import build_input
from kharcha_insights.forecast.model import forecast, what_if
from kharcha_runtime.tools import Tool, tool
from kharcha_runtime.types import RunContext


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    pass


class WeekArgs(_Args):
    week: Literal["this", "last"] = "this"


class ListArgs(_Args):
    days: int = Field(default=7, ge=1, le=60)
    merchant: str | None = Field(default=None, max_length=80)
    category: Category | None = None
    limit: int = Field(default=10, ge=1, le=20)


class WhatIfArgs(_Args):
    category: Category
    reduction_pct: int = Field(default=100, ge=10, le=100, alias="reductionPct")


def _ist_midnight(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=IST)


def _week_bounds(which: str, now: datetime) -> tuple[date, date]:
    today = to_ist(now).date()
    monday = today - timedelta(days=today.weekday())
    if which == "last":
        monday -= timedelta(days=7)
    return monday, monday + timedelta(days=7)


def _spend_filter(user_id: str, start: datetime, end: datetime) -> list[Any]:
    return [
        TransactionRow.user_id == user_id,
        TransactionRow.kind == TxnKind.SPEND.value,
        TransactionRow.status == TxnStatus.SUCCESS.value,
        TransactionRow.direction == Direction.DEBIT.value,
        TransactionRow.txn_time >= start,
        TransactionRow.txn_time < end,
    ]


async def _spent(session: AsyncSession, user_id: str, start: datetime, end: datetime) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(TransactionRow.amount_paise), 0)).where(
            *_spend_filter(user_id, start, end)
        )
    )
    return int(total or 0)


def _merchant_label() -> Any:
    return func.coalesce(Merchant.name, TransactionRow.merchant_raw, "unknown")


@tool("get_weekly_summary", "Spending for this or last week (Mon-Sun, IST).", WeekArgs)
async def get_weekly_summary(
    session: AsyncSession, ctx: RunContext, args: WeekArgs
) -> dict[str, Any]:
    user_id, now = ctx.user_id, ctx.clock()
    start_day, end_day = _week_bounds(args.week, now)
    start, end = _ist_midnight(start_day), _ist_midnight(end_day)
    where = _spend_filter(user_id, start, end)
    spent = await _spent(session, user_id, start, end)
    payments = int(await session.scalar(select(func.count()).where(*where)) or 0)
    by_category = (
        await session.execute(
            select(TransactionRow.category, func.sum(TransactionRow.amount_paise))
            .where(*where)
            .group_by(TransactionRow.category)
            .order_by(func.sum(TransactionRow.amount_paise).desc())
        )
    ).all()
    label = _merchant_label()
    merchants = (
        await session.execute(
            select(label, func.sum(TransactionRow.amount_paise), func.count())
            .select_from(TransactionRow)
            .outerjoin(Merchant, Merchant.id == TransactionRow.merchant_id)
            .where(*where)
            .group_by(label)
            .order_by(func.sum(TransactionRow.amount_paise).desc())
            .limit(3)
        )
    ).all()
    cash_spent = await session.scalar(
        select(func.coalesce(func.sum(CashLedgerRow.amount_paise), 0)).where(
            CashLedgerRow.user_id == user_id,
            CashLedgerRow.entry_type == CashEntryType.CASH_SPEND.value,
            CashLedgerRow.occurred_at >= start,
            CashLedgerRow.occurred_at < end,
        )
    )
    previous = await _spent(session, user_id, start - timedelta(days=7), start)
    return {
        "weekStart": start_day.isoformat(),
        "weekEnd": (end_day - timedelta(days=1)).isoformat(),
        "spentPaise": spent,
        "spentText": format_inr(spent),
        "payments": payments,
        "cashSpentPaise": int(cash_spent or 0),
        "previousWeekSpentPaise": previous,
        "byCategory": [{"category": c, "spentPaise": int(v)} for c, v in by_category],
        "topMerchants": [
            {"merchant": str(m), "spentPaise": int(v), "payments": int(n)} for m, v, n in merchants
        ],
    }


@tool("list_transactions", "Recent payments, newest first (no account numbers).", ListArgs)
async def list_transactions(
    session: AsyncSession, ctx: RunContext, args: ListArgs
) -> dict[str, Any]:
    user_id, now = ctx.user_id, ctx.clock()
    label = _merchant_label()
    stmt = (
        select(TransactionRow, label)
        .outerjoin(Merchant, Merchant.id == TransactionRow.merchant_id)
        .where(
            TransactionRow.user_id == user_id,
            TransactionRow.txn_time >= now - timedelta(days=args.days),
            TransactionRow.txn_time <= now,
        )
        .order_by(TransactionRow.txn_time.desc())
        .limit(args.limit)
    )
    if args.merchant:
        stmt = stmt.where(TransactionRow.merchant_raw == args.merchant)
    if args.category:
        stmt = stmt.where(TransactionRow.category == args.category.value)
    rows = (await session.execute(stmt)).all()
    return {
        "items": [
            {
                "amountPaise": row.amount_paise,
                "direction": row.direction,
                "kind": row.kind,
                "status": row.status,
                "merchant": str(name),
                "category": row.category,
                "date": to_ist(row.txn_time).date().isoformat(),
            }
            for row, name in rows
        ]
    }


@tool("get_cash_balance", "Cash in hand from the cash wallet.", NoArgs)
async def get_cash_balance(session: AsyncSession, ctx: RunContext, args: NoArgs) -> dict[str, Any]:
    user_id = ctx.user_id
    balance = await cash_balance(session, user_id)
    return {
        "cashInHandPaise": max(0, balance.balance_paise),
        "loggedMoreThanTrackedPaise": max(0, -balance.balance_paise),
        "text": balance.describe(),
    }


@tool("get_forecast", "Broke-date forecast: p20/p50/p80 dates (IST) and money now.", NoArgs)
async def get_forecast(session: AsyncSession, ctx: RunContext, args: NoArgs) -> dict[str, Any]:
    user_id, now = ctx.user_id, ctx.clock()
    built = await build_input(session, user_id, now, runs=1_000)
    if built.input is None:
        return {"status": "NEEDS_BALANCE"}
    result = forecast(built.input)
    return {
        "status": "OK",
        "moneyNowPaise": result.start_paise,
        "brokeP20": result.broke_p20.isoformat() if result.broke_p20 else None,
        "brokeP50": result.broke_p50.isoformat() if result.broke_p50 else None,
        "brokeP80": result.broke_p80.isoformat() if result.broke_p80 else None,
        "daysLeftP50": result.days_left_p50(built.input.today),
        "horizonDays": result.horizon_days,
        "dailySpendP50Paise": result.daily_spend_p50_paise,
    }


@tool("what_if", "Days gained on the broke date by cutting a category's spending.", WhatIfArgs)
async def what_if_tool(session: AsyncSession, ctx: RunContext, args: WhatIfArgs) -> dict[str, Any]:
    user_id, now = ctx.user_id, ctx.clock()
    built = await build_input(session, user_id, now, runs=1_000)
    if built.input is None:
        return {"status": "NEEDS_BALANCE"}
    change = what_if(built.input, args.category.value, args.reduction_pct)
    return {
        "status": "OK",
        "category": args.category.value,
        "reductionPct": args.reduction_pct,
        "daysGained": change.days_gained,
        "newBrokeP50": change.changed.broke_p50.isoformat() if change.changed.broke_p50 else None,
    }


@tool("get_budgets", "Monthly budgets with month-to-date spend (IST month).", NoArgs)
async def get_budgets(session: AsyncSession, ctx: RunContext, args: NoArgs) -> dict[str, Any]:
    user_id, now = ctx.user_id, ctx.clock()
    month_start = _ist_midnight(to_ist(now).date().replace(day=1))
    budgets = (
        await session.execute(
            select(Budget.category, Budget.monthly_limit_paise).where(Budget.user_id == user_id)
        )
    ).all()
    out = []
    for category, limit in budgets:
        spent = await session.scalar(
            select(func.coalesce(func.sum(TransactionRow.amount_paise), 0)).where(
                *_spend_filter(user_id, month_start, now + timedelta(seconds=1)),
                TransactionRow.category == category,
            )
        )
        spent_paise = int(spent or 0)
        out.append(
            {
                "category": category,
                "monthlyLimitPaise": int(limit),
                "spentThisMonthPaise": spent_paise,
                "percentUsed": spent_paise * 100 // int(limit) if limit else 0,
            }
        )
    return {"budgets": out}


TOOLS: dict[str, Tool] = {
    t.spec.name: t
    for t in (
        get_weekly_summary,
        list_transactions,
        get_cash_balance,
        get_forecast,
        what_if_tool,
        get_budgets,
    )
}


def _with_cash_tools() -> None:
    from kharcha_mcp_finance.cash_tools import TOOLS as CASH_TOOLS

    TOOLS.update(CASH_TOOLS)


_with_cash_tools()
